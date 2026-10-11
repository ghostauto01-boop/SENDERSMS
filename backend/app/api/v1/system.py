"""Authenticated system diagnostics."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.ads import AdsCampaignJob
from app.models.system_error import SystemErrorRecord
from app.models.user import User
from app.security.auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)


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
    worker_path_ok = worker_ok or inline_enabled
    jobs_ok = bool(components.get("campaign_jobs", {}).get("ok"))
    fully_healthy = db_ok and worker_path_ok and jobs_ok
    return {
        "ok": fully_healthy,
        "degraded": not redis_ok or not worker_ok or not jobs_ok,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "components": components,
        "message": (
            "System is healthy." if fully_healthy and redis_ok and worker_ok else
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
