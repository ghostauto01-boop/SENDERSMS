"""Authenticated system diagnostics."""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.ads import AdsCampaignJob, AdsFollowUpTask
from app.models.followup import FollowUp
from app.models.system_error import SystemErrorRecord
from app.models.user import User
from app.security.auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

#: How old the background-worker heartbeat may get (with work due) before the
#: health page and the UI banner say "no worker is running".
WORKER_STALE_SECONDS = 5 * 60
#: A follow-up whose due time passed by more than this is "overdue", not just
#: "due" — the poller should have sent it already.
FOLLOWUP_OVERDUE_SECONDS = 15 * 60


def _serialize(row: SystemErrorRecord, *, include_traceback: bool = True) -> dict:
    result = {
        "request_id": row.request_id,
        "code": row.code,
        "exception_type": row.exception_type,
        "message": row.message,
        "method": row.method,
        "path": row.path,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if include_traceback:
        result["traceback"] = row.traceback
    return result


@router.get("/health")
async def system_health(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Database, Redis/Celery and persisted Ads-job health in one safe snapshot."""
    components: dict[str, dict] = {}
    try:
        await db.execute(select(1))
        components["database"] = {"ok": True, "message": "Database is responding."}
    except Exception as exc:  # noqa: BLE001
        components["database"] = {
            "ok": False,
            "message": "Database is unavailable. Check DATABASE_URL and database health.",
        }

    redis_ok = False
    try:
        from redis.asyncio import Redis

        client = Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=1.5,
            socket_timeout=1.5,
            decode_responses=True,
        )
        try:
            redis_ok = bool(await asyncio.wait_for(client.ping(), timeout=2))
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.info("System health Redis probe failed: %s", exc)
    components["redis"] = {
        "ok": redis_ok,
        "message": "Redis is responding." if redis_ok else "Redis is unavailable; persisted jobs and the inline poller can still recover work.",
    }

    workers: dict = {}
    try:
        from app.tasks.celery_app import celery_app

        def inspect_workers():
            inspector = celery_app.control.inspect(timeout=1)
            return inspector.ping() or {}

        workers = await asyncio.wait_for(asyncio.to_thread(inspect_workers), timeout=1.5)
    except Exception as exc:  # noqa: BLE001 - a down broker is a health result, not a 500
        logger.info("System health Celery probe failed: %s", exc)
    worker_names = sorted(workers.keys()) if isinstance(workers, dict) else []
    worker_ok = bool(worker_names)
    inline_enabled = bool(getattr(request.app.state, "inline_poller_enabled", settings.ENABLE_INLINE_POLLER))
    components["celery_workers"] = {
        "ok": worker_ok,
        "workers": worker_names,
        "message": "Celery workers are responding." if worker_ok else "No Celery worker responded to the health check.",
    }
    components["inline_poller"] = {
        "ok": inline_enabled,
        "enabled": inline_enabled,
        "message": "Inline recovery poller is enabled." if inline_enabled else "Inline poller is disabled; run a Celery worker and beat process.",
    }

    # --- Background-worker heartbeat -------------------------------------
    # Celery beat stamps worker.heartbeat_at every minute; the inline poller
    # stamps the same key on every cycle. When the newest stamp is more than
    # 5 minutes old AND work is due, nothing is processing the queue: that is
    # the "344 follow-ups due, none sent" failure mode, and the UI banner
    # turns red.
    heartbeat_at: float | None = None
    heartbeat_source = None
    try:
        from app.services.system_settings import (
            WORKER_HEARTBEAT,
            WORKER_HEARTBEAT_SOURCE,
            get_float,
            get_setting,
        )

        heartbeat_at = await get_float(db, WORKER_HEARTBEAT, 0.0) or None
        heartbeat_source = await get_setting(db, WORKER_HEARTBEAT_SOURCE)
    except Exception as exc:  # noqa: BLE001
        logger.info("System health heartbeat read failed: %s", exc)

    now_ts = time.time()
    heartbeat_age = (now_ts - heartbeat_at) if heartbeat_at else None
    due_followups = 0
    overdue_followups = 0
    try:
        from app.models.conversation import Message

        due_cutoff = datetime.now(timezone.utc) - timedelta(seconds=FOLLOWUP_OVERDUE_SECONDS)
        now_dt = datetime.now(timezone.utc)
        overdue_followups = int(
            (
                await db.execute(
                    select(func.count()).select_from(FollowUp).where(
                        FollowUp.status == "pending", FollowUp.scheduled_at <= due_cutoff
                    )
                )
            ).scalar()
            or 0
        ) + int(
            (
                await db.execute(
                    select(func.count()).select_from(AdsFollowUpTask).where(
                        AdsFollowUpTask.status == "pending", AdsFollowUpTask.due_at <= due_cutoff
                    )
                )
            ).scalar()
            or 0
        )
        due_followups = int(
            (
                await db.execute(
                    select(func.count()).select_from(FollowUp).where(
                        FollowUp.status == "pending", FollowUp.scheduled_at <= now_dt
                    )
                )
            ).scalar()
            or 0
        ) + int(
            (
                await db.execute(
                    select(func.count()).select_from(AdsFollowUpTask).where(
                        AdsFollowUpTask.status == "pending", AdsFollowUpTask.due_at <= now_dt
                    )
                )
            ).scalar()
            or 0
        ) + int(
            (
                await db.execute(
                    select(func.count()).select_from(Message).where(
                        Message.direction == "outgoing",
                        Message.status.in_(("queued", "retrying")),
                        or_(Message.retry_at.is_(None), Message.retry_at <= now_dt),
                    )
                )
            ).scalar()
            or 0
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("System health follow-up counts failed: %s", exc)

    heartbeat_stale = heartbeat_age is None or heartbeat_age > WORKER_STALE_SECONDS
    work_due = due_followups > 0
    # A stale heartbeat with nothing due is not an incident (the poller idles
    # deliberately to let the database sleep); with work due it is exactly the
    # broken state this check exists to surface.
    heartbeat_ok = (not heartbeat_stale) or (not work_due)
    components["background_worker"] = {
        "ok": worker_ok,
        "last_heartbeat_at": (
            datetime.fromtimestamp(heartbeat_at, tz=timezone.utc).isoformat()
            if heartbeat_at
            else None
        ),
        "heartbeat_source": heartbeat_source,
        "age_seconds": int(heartbeat_age) if heartbeat_age is not None else None,
        "stale": heartbeat_stale,
        "due_followups": due_followups,
        "overdue_followups": overdue_followups,
        "message": (
            "A background worker is processing jobs."
            if not heartbeat_stale
            else (
                f"No worker has run in {int((heartbeat_age or 0) // 60)} minutes and "
                f"{due_followups} item(s) are due — start a Celery worker + beat "
                "(or enable the inline poller) and use Process now to clear the backlog."
                if work_due
                else (
                    "No worker heartbeat yet, but nothing is due. The inline poller "
                    "wakes on activity."
                    if heartbeat_age is None
                    else f"No worker has run in {int(heartbeat_age // 60)} minutes; nothing is due."
                )
            )
        ),
    }

    try:
        rows = (await db.execute(
            select(AdsCampaignJob.status, func.count())
            .group_by(AdsCampaignJob.status)
        )).all()
        job_counts = {str(status): int(count) for status, count in rows}
        due_jobs = int((await db.execute(
            select(func.count()).select_from(AdsCampaignJob).where(
                AdsCampaignJob.status == "queued",
                AdsCampaignJob.scheduled_for.is_not(None),
                AdsCampaignJob.scheduled_for <= datetime.now(timezone.utc),
            )
        )).scalar() or 0)
        failed_jobs = int(job_counts.get("failed", 0))
        stale_jobs = int((await db.execute(
            select(func.count()).select_from(AdsCampaignJob).where(
                AdsCampaignJob.status == "running",
                AdsCampaignJob.updated_at < datetime.now(timezone.utc) - timedelta(minutes=10),
            )
        )).scalar() or 0)
        jobs_ok = due_jobs == 0 and stale_jobs == 0 and failed_jobs == 0
        components["campaign_jobs"] = {
            "ok": jobs_ok,
            "by_status": job_counts,
            "due": due_jobs,
            "stale_running": stale_jobs,
            "failed": failed_jobs,
            "message": (
                "Campaign jobs are healthy." if jobs_ok else
                f"{due_jobs} due job(s), {stale_jobs} stale worker claim(s), and {failed_jobs} failed job(s) need attention; the poller will recover eligible work."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.info("System health job query failed: %s", exc)
        components["campaign_jobs"] = {
            "ok": False,
            "message": "Persistent job status could not be read. Check the database schema and database health.",
        }

    db_ok = bool(components.get("database", {}).get("ok"))
    worker_path_ok = (worker_ok or inline_enabled) and heartbeat_ok
    jobs_ok = bool(components.get("campaign_jobs", {}).get("ok"))
    fully_healthy = db_ok and worker_path_ok and jobs_ok
    return {
        "ok": fully_healthy,
        "degraded": not redis_ok or not worker_ok or not jobs_ok or not heartbeat_ok,
        "due_followups": due_followups,
        "overdue_followups": overdue_followups,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "components": components,
        "message": (
            "System is healthy." if fully_healthy and redis_ok and worker_ok else
            components["background_worker"]["message"] if not heartbeat_ok and work_due else
            "System is using its recovery path; check the component messages." if db_ok and worker_path_ok else
            "No campaign worker path is currently available. Enable the inline poller or restore the Celery worker and Redis broker."
        ),
    }


@router.get("/errors")
async def read_errors(
    request_id: str | None = Query(default=None, min_length=1, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Read an error by request id, or list recent errors without stack traces."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    if request_id:
        row = (await db.execute(
            select(SystemErrorRecord).where(SystemErrorRecord.request_id == request_id)
        )).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="No stored error for this request_id")
        return _serialize(row)
    rows = list((await db.execute(
        select(SystemErrorRecord).order_by(SystemErrorRecord.created_at.desc()).limit(limit)
    )).scalars().all())
    return {"items": [_serialize(row, include_traceback=False) for row in rows]}
