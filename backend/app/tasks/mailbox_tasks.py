"""Celery tasks for connected mailboxes (Gmail / IMAP).

Two jobs run here, and both exist for the same reason: a reply that arrives in
the operator's mailbox has to end up in *this* app's inbox, promptly.

``sync_mailboxes``
    Polls every connected mailbox that is due, imports replies and rescues the
    ones Gmail already filed under Spam.

``import_gmail_replies``
    The manual button in Settings → Email: forces a sync of one mailbox right
    now, ignoring the poll interval. Useful straight after connecting, so the
    operator does not sit watching an empty inbox wondering whether it worked.

IMAP and SMTP are blocking, so the provider runs them in a worker thread via
``asyncio.to_thread``; that is what the generous task time limits account for.
"""

import asyncio
import logging

from app.tasks.celery_app import celery_app
from app.database import async_session_factory

logger = logging.getLogger(__name__)


def _run(coro):
    return asyncio.run(coro)


async def _sync_due() -> dict:
    from app.services import mailbox_service

    async with async_session_factory() as db:
        results = await mailbox_service.sync_due_mailboxes(db)
        await db.commit()

    summaries = [r for r in results if isinstance(r, dict)]
    return {
        "synced": len(summaries),
        "imported": sum(int(r.get("stored") or 0) for r in summaries),
        "rescued": sum(int(r.get("rescued") or 0) for r in summaries),
        "errors": [e for r in summaries for e in (r.get("errors") or [])][:10],
        "mailboxes": summaries,
    }


async def _sync_one(mailbox_id: int) -> dict:
    """Force a sync of one mailbox, ignoring its poll interval."""
    from app.services import mailbox_service

    async with async_session_factory() as db:
        mailbox = await mailbox_service.get_mailbox(db, mailbox_id)
        if mailbox is None:
            await db.commit()
            return {"success": False, "error": "mailbox not found"}
        summary = await mailbox_service.sync_mailbox(db, mailbox)
        await db.commit()
        return summary


@celery_app.task(bind=True, max_retries=1, default_retry_delay=20, time_limit=240,
                 soft_time_limit=210)
def sync_mailboxes(self):
    """Import replies for every connected mailbox whose poll interval has elapsed."""
    try:
        result = _run(_sync_due())
    except Exception as exc:  # noqa: BLE001 — one bad mailbox must not kill the beat
        logger.exception("MAILBOX: sync_mailboxes failed")
        raise self.retry(exc=exc)
    if result.get("imported") or result.get("rescued"):
        logger.info(
            "MAILBOX: imported %s message(s) from %s mailbox(es); %s rescued from spam",
            result.get("imported"), result.get("synced"), result.get("rescued"),
        )
    return result


@celery_app.task(bind=True, max_retries=1, default_retry_delay=15, time_limit=180,
                 soft_time_limit=150)
def import_gmail_replies(self, mailbox_id: int):
    """Force-sync a single mailbox (the manual 'Import replies now' button)."""
    try:
        return _run(_sync_one(mailbox_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("MAILBOX: import_gmail_replies(%s) failed", mailbox_id)
        raise self.retry(exc=exc)
