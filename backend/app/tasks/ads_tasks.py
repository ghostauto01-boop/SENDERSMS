"""Background processing for the SMS Ads Manager.

Runs from two places, exactly like the existing campaign engine:

* Celery beat (``process_ads_manager``) when a worker is awake.
* The API's inline poller (``run_ads_cycle(send_inline=True)``) when it is not.

Both are safe to run at the same time: every assignment is claimed with a
conditional UPDATE before it is touched.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import async_session_factory
from app.models.ads import AdsCampaign
from app.services import ads_service as svc
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


async def activate_due_campaigns() -> list[int]:
    """Transition scheduled campaigns and wake their persisted jobs."""
    now = datetime.now(timezone.utc)
    started: list[int] = []
    from app.services.campaign_jobs import schedule_campaign_job

    async with async_session_factory() as db:
        rows = list((await db.execute(
            select(AdsCampaign).where(AdsCampaign.status == "scheduled").limit(100)
        )).scalars().all())
        for campaign in rows:
            start = svc.as_utc(campaign.start_date)
            if start is None or start <= now:
                svc.transition_campaign(
                    db, campaign, "active", reason="Scheduled start time reached", actor="system"
                )
                svc.log_activity(
                    db, "campaign_started", campaign_id=campaign.id, detail="Scheduled start reached"
                )
                await schedule_campaign_job(db, campaign, scheduled_for=now)
                started.append(campaign.id)
        if started:
            await db.commit()
    return started


async def run_ads_cycle(*, send_inline: bool = False, batch: int = 25) -> dict:
    """Drive persistent campaign jobs, delivery sync, follow-ups and optimization."""
    totals = {
        "activated": 0, "added": 0, "sent": 0, "followups": 0,
        "campaigns": 0, "optimized": 0, "jobs_failed": 0,
    }
    totals["activated"] = len(await activate_due_campaigns())

    async with async_session_factory() as db:
        try:
            totals["added"] = await svc.refresh_always_on(db)
        except Exception as exc:  # noqa: BLE001 - never abort the cycle
            logger.warning("Ads always-on refresh failed: %s", exc)
            await db.rollback()

    # Each campaign has an append-only, persisted queue job. The poller and
    # Celery beat can race safely because jobs are claimed with a conditional
    # UPDATE before any work is done.
    try:
        from app.services.campaign_jobs import process_due_jobs

        jobs = await process_due_jobs(send_inline=send_inline, batch=batch)
        totals["sent"] = int(jobs.get("sent") or 0)
        totals["campaigns"] = int(jobs.get("processed") or 0)
        totals["jobs_failed"] = int(jobs.get("failed") or 0)
    except Exception as exc:  # noqa: BLE001
        logger.error("Ads durable job poll failed: %s", exc)

    async with async_session_factory() as db:
        try:
            await svc.sync_delivery(db)
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ads delivery sync failed: %s", exc)
            await db.rollback()

    async with async_session_factory() as db:
        try:
            fu = await svc.process_followups(db, send_inline=send_inline, include_paused=True)
            totals["followups"] = fu["sent"] + fu["completed"]
        except Exception as exc:  # noqa: BLE001
            logger.error("Ads follow-up processing failed: %s", exc)
            await db.rollback()

    # Andromeda auto-optimization: ONLY for campaigns whose master switch is
    # ON. run_optimization enforces the opt-in again, so a missed filter here
    # can never cause an unwanted automatic shift.
    async with async_session_factory() as db:
        try:
            opt_ids = list((await db.execute(
                select(AdsCampaign.id).where(
                    AdsCampaign.status == "active",
                    AdsCampaign.auto_optimize.is_(True),
                )
            )).scalars().all())
        except Exception as exc:  # noqa: BLE001 - column may predate migration
            logger.warning("Ads optimization lookup failed: %s", exc)
            opt_ids = []
    for campaign_id in opt_ids:
        async with async_session_factory() as db:
            try:
                campaign = (await db.execute(
                    select(AdsCampaign).where(AdsCampaign.id == campaign_id)
                )).scalar_one_or_none()
                if campaign is None:
                    continue
                result = await svc.run_optimization(db, campaign, actor="andromeda")
                if result.get("ok") and (result.get("moved") or result.get("paused")):
                    totals["optimized"] += 1
                await db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.error("Ads optimization for campaign %s failed: %s", campaign_id, exc)
                await db.rollback()

    return totals


def _run(coro):
    loop = asyncio.get_event_loop()
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@celery_app.task
def process_ads_manager():
    """Celery beat entrypoint."""
    return _run(run_ads_cycle(send_inline=False))
