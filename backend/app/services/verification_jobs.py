"""Queued email verification.

WHY A QUEUE
-----------
Verifying a mailbox is an SMTP conversation (seconds each, often a timeout). Checking
1,297 contacts five at a time from the browser meant leaving a tab open for half an hour,
and a closed tab lost the run. A job is a durable row: it snapshots the contact ids,
processes them in chunks on the server, writes every verdict onto the contact as it goes,
publishes its progress, and -- because ``processed`` is the index into the snapshot --
resumes exactly where it stopped after a restart.

The worker runs in this process (an asyncio task), the same way the CSV importer does:
the app is deployed on hosts where a separate Celery worker is not always present.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.contact import Contact
from app.models.verification_job import EmailVerificationJob
from app.services import email_service
from app.services import email_validator as ev
from app.services.email_enrichment import (
    VERDICT_DELIVERABLE,
    VERDICT_RISKY,
    VERDICT_UNDELIVERABLE,
)
from app.utils.contact_identity import normalize_email

logger = logging.getLogger(__name__)

#: Contacts processed (and committed) at a time.
CHUNK = 25
#: Simultaneous mailbox checks. Each is a short-lived SMTP connection from this server.
CONCURRENCY = 10

ACTIVE = ("queued", "running")

_tasks: set[asyncio.Task] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def spawn(job_id: str) -> None:
    """Run a job in the background of the running event loop."""
    task = asyncio.get_running_loop().create_task(run_job(job_id))
    _tasks.add(task)  # keep a reference: a bare task can be garbage collected mid-run
    task.add_done_callback(_tasks.discard)


def job_dict(job: EmailVerificationJob) -> dict:
    return {
        "id": job.id,
        "status": job.status,
        "total": job.total,
        "processed": job.processed,
        "remaining": max(job.total - job.processed, 0),
        "valid": job.valid,
        "invalid": job.invalid,
        "risky": job.risky,
        "unknown": job.unknown,
        "skipped": job.skipped,
        "deep": job.deep,
        "recheck": job.recheck,
        "engine": job.engine,
        "smtp_enabled": job.smtp_enabled,
        "smtp_reason": job.smtp_reason,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


async def get_job(db: AsyncSession, job_id: str) -> Optional[EmailVerificationJob]:
    return (
        await db.execute(select(EmailVerificationJob).where(EmailVerificationJob.id == job_id))
    ).scalar_one_or_none()


async def create_job(
    db: AsyncSession,
    contact_ids: list[int],
    *,
    owner_id: int | None,
    deep: bool = True,
    recheck: bool = False,
) -> EmailVerificationJob:
    """Snapshot the contacts worth checking: those with an address, and (unless
    ``recheck``) not already proven deliverable."""
    wanted: list[int] = []
    if contact_ids:
        rows = (
            await db.execute(
                select(Contact.id, Contact.email, Contact.email_verified)
                .where(Contact.id.in_(contact_ids))
            )
        ).all()
        by_id = {row.id: row for row in rows}
        for cid in contact_ids:  # keep the caller's order
            row = by_id.get(cid)
            if row is None or not (row.email or "").strip():
                continue
            if row.email_verified and not recheck:
                continue
            wanted.append(cid)
    job = EmailVerificationJob(
        id=str(uuid.uuid4()),
        owner_id=owner_id,
        status="queued",
        deep=deep,
        recheck=recheck,
        contact_ids_json=json.dumps(wanted),
        total=len(wanted),
    )
    db.add(job)
    await db.flush()
    return job


async def cancel_job(db: AsyncSession, job: EmailVerificationJob) -> EmailVerificationJob:
    if job.status in ACTIVE:
        job.status = "cancelled"
        job.completed_at = _now()
        await db.flush()
    return job


async def pending_job_ids() -> list[str]:
    """Jobs a restart left unfinished (queued, or running when the process died)."""
    async with async_session_factory() as db:
        rows = await db.execute(
            select(EmailVerificationJob.id)
            .where(EmailVerificationJob.status.in_(ACTIVE))
            .order_by(EmailVerificationJob.created_at)
        )
        return list(rows.scalars().all())


async def resume_pending() -> int:
    """Called at startup: pick every unfinished job up again."""
    ids = await pending_job_ids()
    for job_id in ids:
        spawn(job_id)
    return len(ids)


async def _check(semaphore: asyncio.Semaphore, address: str, deep: bool) -> ev.Verdict:
    async with semaphore:
        try:
            return await asyncio.wait_for(ev.validate_email(address, deep=deep), timeout=70)
        except Exception:  # noqa: BLE001 -- one bad address must not lose the rest
            return ev.Verdict(address=address, problems=["Check unavailable; retry this address"])


async def run_job(job_id: str, *, max_chunks: int | None = None) -> None:
    """Process a job to the end (or ``max_chunks`` chunks -- used to test resuming)."""
    async with async_session_factory() as db:
        job = await get_job(db, job_id)
        if job is None or job.status not in ACTIVE:
            return
        try:
            await _run(db, job, max_chunks)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Verification job %s failed", job_id)
            await db.rollback()
            job = await get_job(db, job_id)
            if job is not None and job.status in ACTIVE:
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {str(exc)[:500]}"
                job.completed_at = _now()
                await db.commit()


async def _run(db: AsyncSession, job: EmailVerificationJob, max_chunks: int | None) -> None:
    ids: list[int] = json.loads(job.contact_ids_json or "[]")
    capability = await ev.smtp_capability()
    job.status = "running"
    job.started_at = job.started_at or _now()
    job.engine = "reacher" if ev.reacher_configured() else "builtin"
    job.smtp_enabled = bool(capability["enabled"])
    job.smtp_reason = capability["reason"]
    await db.commit()
    # Without a way to ask the mailbox, a deep check would only spend time on SMTP
    # timeouts to arrive at "unknown". Check syntax and MX (instant) and say why.
    deep = bool(job.deep) and bool(capability["enabled"])
    semaphore = asyncio.Semaphore(CONCURRENCY)
    chunks = 0

    while job.processed < len(ids):
        await db.refresh(job)  # a cancel arrives as a status change from another session
        if job.status != "running":
            return
        window = ids[job.processed: job.processed + CHUNK]
        contacts = (
            await db.execute(select(Contact).where(Contact.id.in_(window)))
        ).scalars().all()
        checked_addresses = {c.id: normalize_email(c.email) for c in contacts if (c.email or "").strip()}
        verdicts = await asyncio.gather(*(
            _check(semaphore, address, deep) for address in checked_addresses.values()
        ))
        by_contact = dict(zip(checked_addresses.keys(), verdicts))

        # Re-read AFTER the network checks, and never apply a verdict to an address that
        # was edited while it was being checked.
        fresh = (
            await db.execute(
                select(Contact).where(Contact.id.in_(window))
                .execution_options(populate_existing=True)
            )
        ).scalars().all()
        for contact in fresh:
            verdict = by_contact.get(contact.id)
            if verdict is None or normalize_email(contact.email) != checked_addresses.get(contact.id):
                job.skipped += 1
                continue
            ev.apply_verdict(contact, verdict)
            if verdict.verdict == VERDICT_DELIVERABLE:
                job.valid += 1
            elif verdict.verdict == VERDICT_UNDELIVERABLE:
                job.invalid += 1
            elif verdict.verdict == VERDICT_RISKY:
                job.risky += 1
            else:
                job.unknown += 1
        job.skipped += len(window) - len(fresh)  # deleted while queued
        job.processed += len(window)
        await db.commit()
        chunks += 1
        if max_chunks is not None and chunks >= max_chunks:
            return

    job.status = "done"
    job.completed_at = _now()
    await db.commit()
