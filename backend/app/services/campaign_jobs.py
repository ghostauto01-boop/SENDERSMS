"""Persistent Ads campaign jobs.

The database row is the source of truth; Celery/Redis provides a fast wake-up
when it is available, and the existing inline/Celery poller recovers jobs after
process restarts or broker outages.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.ads import AdsAssignment, AdsCampaign, AdsCampaignJob
from app.models.conversation import Message

logger = logging.getLogger(__name__)
ACTIVE_JOB_STATES = ("queued", "running")
TERMINAL_ASSIGNMENT_STATES = ("sent", "delivered", "failed", "skipped", "cancelled", "blocked")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def serialize_job(job: AdsCampaignJob) -> dict:
    return {
        "id": job.id,
        "campaign_id": job.campaign_id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": {
            "completed": int(job.progress_completed or 0),
            "total": int(job.progress_total or 0),
        },
        "attempts": int(job.attempts or 0),
        "max_retries": int(job.max_retries or 5),
        "scheduled_for": job.scheduled_for,
        "worker_id": job.worker_id,
        "error": job.error,
        "result": json.loads(job.result_json) if job.result_json else None,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "updated_at": job.updated_at,
    }


async def _counts(db: AsyncSession, campaign_id: int) -> tuple[int, int, int]:
    total = int((await db.execute(
        select(func.count()).select_from(AdsAssignment).where(AdsAssignment.campaign_id == campaign_id)
    )).scalar() or 0)
    pending = int((await db.execute(
        select(func.count()).select_from(AdsAssignment).where(
            AdsAssignment.campaign_id == campaign_id,
            AdsAssignment.send_status == "pending",
        )
    )).scalar() or 0)
    sending = int((await db.execute(
        select(func.count()).select_from(AdsAssignment).where(
            AdsAssignment.campaign_id == campaign_id,
            AdsAssignment.send_status.in_(("sending", "ready")),
        )
    )).scalar() or 0)
    return total, pending, sending


async def schedule_campaign_job(
    db: AsyncSession,
    campaign: AdsCampaign,
    *,
    scheduled_for: datetime | None = None,
) -> AdsCampaignJob:
    """Create or wake the nonterminal job for one campaign, idempotently."""
    job = (
        await db.execute(
            select(AdsCampaignJob)
            .where(
                AdsCampaignJob.campaign_id == campaign.id,
                AdsCampaignJob.status.in_(ACTIVE_JOB_STATES),
            )
            .order_by(AdsCampaignJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    total, _pending, _sending = await _counts(db, campaign.id)
    target = scheduled_for
    if target is None and campaign.status == "scheduled":
        target = campaign.start_date
    if job is not None:
        job.progress_total = total
        if target is not None and (job.scheduled_for is None or target < job.scheduled_for):
            job.scheduled_for = target
        elif scheduled_for is None and campaign.status == "active" and job.scheduled_for is None:
            job.scheduled_for = now_utc()
        job.updated_at = now_utc()
        await db.flush()
        return job

    job = AdsCampaignJob(
        campaign_id=campaign.id,
        job_type="send_campaign_batch",
        status="queued",
        progress_completed=0,
        progress_total=total,
        max_retries=5,
        scheduled_for=target or (now_utc() if campaign.status == "active" else None),
    )
    db.add(job)
    await db.flush()
    return job


async def _set_progress(db: AsyncSession, job: AdsCampaignJob) -> tuple[int, int, int]:
    total, pending, sending = await _counts(db, job.campaign_id)
    completed = int((await db.execute(
        select(func.count()).select_from(AdsAssignment).where(
            AdsAssignment.campaign_id == job.campaign_id,
            AdsAssignment.send_status.in_(TERMINAL_ASSIGNMENT_STATES),
        )
    )).scalar() or 0)
    job.progress_total = total
    job.progress_completed = min(completed, total)
    return pending, sending, total


async def _next_wakeup(db: AsyncSession, campaign: AdsCampaign, result: dict) -> datetime:
    """Pick a durable retry time from campaign drip, schedule, and send limits."""
    from app.services import ads_service as svc

    now = now_utc()
    if campaign.status == "scheduled" and campaign.start_date:
        return max(svc.as_utc(campaign.start_date) or now, now + timedelta(seconds=5))

    pending_times = list((await db.execute(
        select(AdsAssignment.next_attempt_at).where(
            AdsAssignment.campaign_id == campaign.id,
            AdsAssignment.send_status == "pending",
            AdsAssignment.next_attempt_at.is_not(None),
            AdsAssignment.next_attempt_at > now,
        ).order_by(AdsAssignment.next_attempt_at).limit(1)
    )).scalars().all())
    if pending_times:
        return pending_times[0]

    # Campaign daily limits reset at the next local midnight.
    if campaign.daily_limit:
        used = await svc.sent_today(db, campaign)
        if used >= campaign.daily_limit:
            local_now = now.astimezone(svc._campaign_tz(campaign))
            next_day = (local_now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
            return next_day.astimezone(timezone.utc)

    # Respect the global Sending Rules window/caps without keeping a worker busy.
    if not campaign.test_mode:
        try:
            from app.services.sending_limits import gate_for_campaign

            gate = await (await gate_for_campaign(db, campaign)).check()
            if not gate.get("allowed"):
                return now + timedelta(seconds=max(int(gate.get("wait_seconds") or 60), 5))
        except Exception as exc:  # noqa: BLE001
            logger.info("Could not calculate send-gate wakeup for campaign %s: %s", campaign.id, exc)

    mode = (campaign.drip_mode or "off").lower()
    if mode in ("interval", "batch", "smart"):
        interval = max(int(campaign.drip_interval_minutes or 0), 1)
        batch = max(int(campaign.drip_batch_size or 1), 1)
        if mode == "interval":
            batch = 1
        elif mode == "smart":
            batch = svc._smart_slice(campaign, batch)
        recent = list((await db.execute(
            select(AdsAssignment.last_action_at).where(
                AdsAssignment.campaign_id == campaign.id,
                AdsAssignment.last_action_at.is_not(None),
                AdsAssignment.last_action_at >= now - timedelta(minutes=interval),
                AdsAssignment.send_status.in_(("sending", "sent", "delivered")),
            ).order_by(AdsAssignment.last_action_at).limit(batch)
        )).scalars().all())
        if len(recent) >= batch:
            earliest = recent[0]
            if earliest.tzinfo is None:
                earliest = earliest.replace(tzinfo=timezone.utc)
            return max(earliest + timedelta(minutes=interval), now + timedelta(seconds=2))

    # Provider messages that are still in flight/retrying are reconciled often,
    # but not by holding the HTTP launch request open.
    retry_at = (await db.execute(
        select(func.min(Message.retry_at)).where(
            Message.ads_campaign_id == campaign.id,
            Message.status.in_(("queued", "sending", "retrying")),
            Message.retry_at.is_not(None),
            Message.retry_at > now,
        )
    )).scalar_one_or_none()
    if retry_at:
        return retry_at
    return now + timedelta(seconds=30 if result.get("sent") else 60)


async def refresh_job_status(db: AsyncSession, job: AdsCampaignJob, *, result: dict | None = None) -> AdsCampaignJob:
    """Persist counts, next run time, completion and actionable failure state."""
    campaign = (
        await db.execute(select(AdsCampaign).where(AdsCampaign.id == job.campaign_id))
    ).scalar_one_or_none()
    if campaign is None:
        job.status = "cancelled"
        job.error = "Campaign no longer exists."
        job.finished_at = now_utc()
        job.scheduled_for = None
        await db.flush()
        return job

    pending, sending, total = await _set_progress(db, job)
    if campaign.status in ("paused", "draft", "archived", "error"):
        if campaign.status == "archived":
            job.status = "cancelled"
            job.finished_at = now_utc()
        else:
            job.status = "queued"
            job.scheduled_for = None
        job.updated_at = now_utc()
        await db.flush()
        return job
    if campaign.status == "completed":
        job.status = "completed"
        job.scheduled_for = None
        job.finished_at = now_utc()
        job.error = None
        await db.flush()
        return job

    # queued/sending provider Messages can outlive the campaign dispatch slice.
    inflight_messages = int((await db.execute(
        select(func.count()).select_from(Message).where(
            Message.ads_campaign_id == campaign.id,
            Message.status.in_(("queued", "sending", "retrying")),
        )
    )).scalar() or 0)
    if pending or sending or inflight_messages:
        job.status = "queued"
        job.finished_at = None
        job.error = None
        job.scheduled_for = await _next_wakeup(db, campaign, result or {})
    else:
        job.status = "completed"
        job.scheduled_for = None
        job.finished_at = now_utc()
        job.error = None
    job.result_json = json.dumps(result or {}, default=str)[:8000] if result is not None else job.result_json
    job.updated_at = now_utc()
    await db.flush()
    return job


async def create_missing_jobs(db: AsyncSession) -> int:
    """Recover launched campaigns with pending work after deploy/restart/broker loss."""
    now = now_utc()
    stale = await db.execute(
        update(AdsCampaignJob)
        .where(
            AdsCampaignJob.status == "running",
            AdsCampaignJob.updated_at < now - timedelta(minutes=10),
        )
        .values(
            status="queued",
            scheduled_for=now,
            error="Recovered a campaign job after its worker stopped responding.",
            updated_at=now,
        )
    )
    campaigns = list((await db.execute(
        select(AdsCampaign).where(
            AdsCampaign.status.in_(("active", "scheduled")),
        ).order_by(AdsCampaign.id).limit(100)
    )).scalars().all())
    created = 0
    for campaign in campaigns:
        total, pending, sending = await _counts(db, campaign.id)
        active = (await db.execute(
            select(AdsCampaignJob.id).where(
                AdsCampaignJob.campaign_id == campaign.id,
                AdsCampaignJob.status.in_(ACTIVE_JOB_STATES),
            ).limit(1)
        )).scalar_one_or_none()
        if active:
            continue
        last_job = (await db.execute(
            select(AdsCampaignJob).where(AdsCampaignJob.campaign_id == campaign.id)
            .order_by(AdsCampaignJob.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        if last_job and last_job.status == "failed" and last_job.attempts >= last_job.max_retries:
            continue
        if campaign.status == "scheduled" or pending or sending or campaign.always_on:
            await schedule_campaign_job(db, campaign)
            created += 1
    if created:
        await db.flush()
    return created + max(int(stale.rowcount or 0), 0)


async def process_campaign_job(job_id: str, *, send_inline: bool = False, batch: int = 25) -> dict:
    """Claim and perform one durable slice. Safe alongside Celery beat and API pollers."""
    now = now_utc()
    async with async_session_factory() as db:
        claim = await db.execute(
            update(AdsCampaignJob)
            .where(
                AdsCampaignJob.id == job_id,
                AdsCampaignJob.status == "queued",
                AdsCampaignJob.scheduled_for.is_not(None),
                AdsCampaignJob.scheduled_for <= now,
            )
            # ``attempts`` counts consecutive FAILED slices only (see the failure
            # handler below). Counting every claim here meant a campaign that is
            # routinely woken to wait for its sending window, a daily cap or the
            # next drip batch used up its five retries without ever failing, and
            # then one transient error marked the job "failed" for good.
            .values(
                status="running",
                started_at=func.coalesce(AdsCampaignJob.started_at, now),
                worker_id=f"{socket.gethostname()}:{os.getpid()}",
                updated_at=now,
            )
        )
        if claim.rowcount != 1:
            return {"ok": True, "claimed": False, "job_id": job_id}
        await db.commit()

    result: dict = {"sent": 0, "skipped": 0, "state": "queued", "reasons": {}}
    async with async_session_factory() as db:
        job = (await db.execute(select(AdsCampaignJob).where(AdsCampaignJob.id == job_id))).scalar_one_or_none()
        if job is None:
            return {"ok": False, "job_id": job_id, "error": "Campaign job not found."}
        campaign = (await db.execute(select(AdsCampaign).where(AdsCampaign.id == job.campaign_id))).scalar_one_or_none()
        if campaign is None:
            job.status = "cancelled"
            job.error = "Campaign no longer exists."
            job.finished_at = now_utc()
            await db.commit()
            return {"ok": False, "job_id": job_id, "error": job.error}
        try:
            if campaign.status == "scheduled" and campaign.start_date:
                from app.services import ads_service as svc

                if (svc.as_utc(campaign.start_date) or now_utc()) > now_utc():
                    job.status = "queued"
                    job.scheduled_for = svc.as_utc(campaign.start_date)
                    job.attempts = 0
                    await db.commit()
                    return {"ok": True, "job_id": job_id, "state": "scheduled"}
                svc.transition_campaign(
                    db, campaign, "active", reason="Scheduled start time reached", actor="system"
                )
                svc.log_activity(db, "campaign_started", campaign_id=campaign.id, detail="Scheduled start reached")

            if campaign.status != "active":
                await refresh_job_status(db, job)
                job.attempts = 0
                await db.commit()
                return {"ok": True, "job_id": job_id, "state": campaign.status}

            from app.services import ads_service as svc

            result = await svc.dispatch_campaign(db, campaign, limit=batch, send_inline=send_inline)
            await svc.sync_delivery(db, campaign.id)
            await refresh_job_status(db, job, result=result)
            job.attempts = 0
            await db.commit()
            return {"ok": True, "job_id": job_id, **result, "job_status": job.status}
        except Exception as exc:  # noqa: BLE001 - persist failures for the operator
            logger.exception("Campaign job %s failed", job_id)
            await db.rollback()
            async with async_session_factory() as err_db:
                job = (await err_db.execute(select(AdsCampaignJob).where(AdsCampaignJob.id == job_id))).scalar_one_or_none()
                if job is not None:
                    # One more consecutive failure (the claim no longer counts).
                    job.attempts = int(job.attempts or 0) + 1
                    job.error = "Campaign processing failed. The worker will retry automatically; check system health if the problem continues."
                    job.updated_at = now_utc()
                    if job.attempts >= job.max_retries:
                        job.status = "failed"
                        job.finished_at = now_utc()
                        campaign = (await err_db.execute(select(AdsCampaign).where(AdsCampaign.id == job.campaign_id))).scalar_one_or_none()
                        if campaign is not None:
                            campaign.last_error = job.error
                    else:
                        job.status = "queued"
                        job.scheduled_for = now_utc() + timedelta(seconds=min(60 * (2 ** (job.attempts - 1)), 3600))
                    await err_db.commit()
            return {"ok": False, "job_id": job_id, "error": "Campaign processing failed; it will be retried automatically."}


async def process_due_jobs(*, send_inline: bool = False, batch: int = 25, limit: int = 20) -> dict:
    """Poll durable due work; return summary suitable for worker/system health."""
    from app.database import async_session_factory

    async with async_session_factory() as db:
        recovered = await create_missing_jobs(db)
        ids = list((await db.execute(
            select(AdsCampaignJob.id).where(
                AdsCampaignJob.status == "queued",
                AdsCampaignJob.scheduled_for.is_not(None),
                AdsCampaignJob.scheduled_for <= now_utc(),
            ).order_by(AdsCampaignJob.scheduled_for, AdsCampaignJob.created_at).limit(limit)
        )).scalars().all())
        if recovered:
            await db.commit()
    processed = 0
    failed = 0
    sent = 0
    for job_id in ids:
        outcome = await process_campaign_job(job_id, send_inline=send_inline, batch=batch)
        processed += int(outcome.get("claimed", True))
        failed += int(not outcome.get("ok", True))
        sent += int(outcome.get("sent") or 0)
    return {
        "recovered": recovered, "due": len(ids), "processed": processed,
        "failed": failed, "sent": sent,
    }


async def list_campaign_jobs(db: AsyncSession, campaign_id: int, *, limit: int = 20) -> list[dict]:
    rows = list((await db.execute(
        select(AdsCampaignJob).where(AdsCampaignJob.campaign_id == campaign_id)
        .order_by(AdsCampaignJob.created_at.desc()).limit(limit)
    )).scalars().all())
    return [serialize_job(row) for row in rows]
