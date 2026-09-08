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
    """Flip scheduled campaigns to active once their start date passes."""
    now = datetime.now(timezone.utc)
    started: list[int] = []
    async with async_session_factory() as db:
        rows = list(
            (
                await db.execute(
                    select(AdsCampaign).where(AdsCampaign.status == "scheduled").limit(20)
                )
            ).scalars().all()
        )
        for campaign in rows:
            start = svc.as_utc(campaign.start_date)
            if start is None or start <= now:
                campaign.status = "active"
                svc.log_activity(
                    db, "campaign_started", campaign_id=campaign.id, detail="Scheduled start reached"
                )
                started.append(campaign.id)
        if started:
            await db.commit()
    return started


async def run_ads_cycle(*, send_inline: bool = False, batch: int = 25) -> dict:
    """One full cycle: activate, refresh always-on audiences, dispatch, follow up."""
    totals = {"activated": 0, "added": 0, "sent": 0, "followups": 0, "campaigns": 0}

    totals["activated"] = len(await activate_due_campaigns())

    async with async_session_factory() as db:
        try:
            totals["added"] = await svc.refresh_always_on(db)
        except Exception as exc:  # noqa: BLE001 - never abort the cycle
            logger.warning("Ads always-on refresh failed: %s", exc)

    async with async_session_factory() as db:
        campaign_ids = list(
            (
                await db.execute(
                    select(AdsCampaign.id)
                    .where(AdsCampaign.status == "active")
                    .order_by(AdsCampaign.id)
                    .limit(10)
                )
            ).scalars().all()
        )

    for campaign_id in campaign_ids:
        async with async_session_factory() as db:
            campaign = (
                await db.execute(select(AdsCampaign).where(AdsCampaign.id == campaign_id))
            ).scalar_one_or_none()
            if campaign is None:
                continue
            try:
                result = await svc.dispatch_campaign(
                    db, campaign, limit=batch, send_inline=send_inline
                )
                totals["sent"] += result["sent"]
                totals["campaigns"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("Ads dispatch for campaign %s failed: %s", campaign_id, exc)
                await db.rollback()

    async with async_session_factory() as db:
        try:
            # Read gateway outcomes back onto the assignments before analytics
            # are asked for them.
            await svc.sync_delivery(db)
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ads delivery sync failed: %s", exc)
            await db.rollback()

    async with async_session_factory() as db:
        try:
            fu = await svc.process_followups(db, send_inline=send_inline)
            totals["followups"] = fu["sent"] + fu["completed"]
        except Exception as exc:  # noqa: BLE001
            logger.error("Ads follow-up processing failed: %s", exc)
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
