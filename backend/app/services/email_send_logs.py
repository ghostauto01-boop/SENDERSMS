"""Durable per-attempt email delivery log helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email import EmailSendLog


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _campaign_id(message) -> int | None:
    # This table's campaign_id is the Ads Manager id. Classic campaign ids are
    # from a separate namespace and may collide with Ads ids.
    return getattr(message, "ads_campaign_id", None)


async def queue_email_send_log(
    db: AsyncSession,
    *,
    campaign_id: int | None,
    contact_id: int | None,
    creative_id: int | None,
    assignment_id: int | None,
    message_id: int,
    account_id: int | None,
) -> EmailSendLog:
    """Record a durable queued entry when a campaign message is materialized."""
    row = EmailSendLog(
        campaign_id=campaign_id,
        contact_id=contact_id,
        creative_id=creative_id,
        assignment_id=assignment_id,
        message_id=message_id,
        account_id=account_id,
        status="queued",
        attempt=1,
    )
    db.add(row)
    await db.flush()
    return row


async def next_email_log_attempt(db: AsyncSession, message) -> int:
    """Return the next append-only provider attempt number for a message."""
    latest = (await db.execute(
        select(EmailSendLog).where(EmailSendLog.message_id == message.id)
        .order_by(EmailSendLog.attempt.desc()).limit(1)
    )).scalar_one_or_none()
    if latest is None:
        return 1
    if latest.status == "queued" and int(getattr(message, "retry_count", 0) or 0) == 0:
        return int(latest.attempt or 1)
    return int(latest.attempt or 0) + 1


async def begin_email_attempt(
    db: AsyncSession, message, *, attempt: int | None = None
) -> EmailSendLog:
    """Claim/create the log row for this provider attempt.

    The queued row created with an ads message is reused for attempt one. Each
    retry gets its own row, so the log is an actual attempt history, not just a
    mutable mirror of ``messages.last_error``.
    """
    attempt = max(1, int(attempt or (int(getattr(message, "retry_count", 0) or 0) + 1)))
    row = (
        await db.execute(
            select(EmailSendLog).where(
                EmailSendLog.message_id == message.id,
                EmailSendLog.attempt == attempt,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        from app.models.ads import AdsAssignment

        assignment = None
        if getattr(message, "ads_campaign_id", None):
            assignment = (
                await db.execute(
                    select(AdsAssignment).where(AdsAssignment.message_id == message.id)
                )
            ).scalar_one_or_none()
        row = EmailSendLog(
            campaign_id=_campaign_id(message),
            contact_id=message.contact_id,
            creative_id=assignment.creative_id if assignment else None,
            assignment_id=assignment.id if assignment else None,
            message_id=message.id,
            account_id=message.email_account_id,
            attempt=attempt,
            status="sending",
        )
        db.add(row)
    else:
        row.status = "sending"
        row.error_code = None
        row.error_message = None
        row.retry_at = None
        row.updated_at = _now()
    await db.flush()
    return row


async def finish_email_attempt(
    db: AsyncSession,
    message,
    *,
    status: str,
    error_code: str | int | None = None,
    error_message: str | None = None,
    retry_at: datetime | None = None,
    sent_at: datetime | None = None,
    attempt: int | None = None,
) -> EmailSendLog:
    """Set the current attempt's provider result, with user-readable detail."""
    attempt = max(1, int(attempt or (int(getattr(message, "retry_count", 0) or 0) + 1)))
    row = (
        await db.execute(
            select(EmailSendLog).where(
                EmailSendLog.message_id == message.id,
                EmailSendLog.attempt == attempt,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = await begin_email_attempt(db, message, attempt=attempt)
    row.status = status
    row.error_code = str(error_code)[:80] if error_code is not None else None
    row.error_message = (str(error_message)[:2000] if error_message else None)
    row.retry_at = retry_at
    row.sent_at = sent_at
    row.account_id = getattr(message, "email_account_id", None) or row.account_id
    row.updated_at = _now()
    await db.flush()
    return row


async def update_logs_for_message(
    db: AsyncSession,
    message,
    *,
    status: str,
    error_message: str | None = None,
    error_code: str | int | None = None,
    sent_at: datetime | None = None,
) -> int:
    """Apply asynchronous provider delivery/bounce status to the latest attempt.

    Earlier attempts stay immutable in the log, so a successful retry does not
    erase the provider error that caused it.
    """
    row = (await db.execute(
        select(EmailSendLog).where(EmailSendLog.message_id == message.id)
        .order_by(EmailSendLog.attempt.desc()).limit(1)
    )).scalar_one_or_none()
    if row is None:
        await finish_email_attempt(
            db, message, status=status, error_code=error_code,
            error_message=error_message, sent_at=sent_at,
        )
        return 1
    row.status = status
    row.error_code = str(error_code)[:80] if error_code is not None else row.error_code
    row.error_message = (str(error_message)[:2000] if error_message else None)
    row.retry_at = None
    row.sent_at = sent_at or row.sent_at
    row.updated_at = _now()
    await db.flush()
    return 1


async def record_skipped_assignment(
    db: AsyncSession,
    *,
    campaign_id: int,
    contact_id: int,
    creative_id: int | None,
    assignment_id: int,
    error_message: str,
) -> EmailSendLog:
    """Record a contact skipped by the last-moment suppression/consent check."""
    row = (
        await db.execute(
            select(EmailSendLog).where(
                EmailSendLog.assignment_id == assignment_id,
                EmailSendLog.attempt == 1,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = EmailSendLog(
            campaign_id=campaign_id,
            contact_id=contact_id,
            creative_id=creative_id,
            assignment_id=assignment_id,
            status="skipped",
            attempt=1,
            error_message=error_message[:2000],
        )
        db.add(row)
    else:
        row.status = "skipped"
        row.error_message = error_message[:2000]
        row.updated_at = _now()
    await db.flush()
    return row


async def summarize_campaign(db: AsyncSession, campaign_id: int) -> dict:
    """Summarize the latest state per recipient, while retaining retry totals."""
    from app.models.conversation import Message

    attempts = list((await db.execute(
        select(EmailSendLog).where(EmailSendLog.campaign_id == campaign_id)
        .order_by(EmailSendLog.created_at, EmailSendLog.id)
    )).scalars().all())
    latest_by_recipient: dict[tuple[str, int], EmailSendLog] = {}
    for row in attempts:
        if row.message_id is not None:
            key = ("message", row.message_id)
        elif row.assignment_id is not None:
            key = ("assignment", row.assignment_id)
        else:
            key = ("log", row.id)
        previous = latest_by_recipient.get(key)
        if previous is None or (row.attempt, row.id) > (previous.attempt, previous.id):
            latest_by_recipient[key] = row

    known_ids = select(EmailSendLog.message_id).where(
        EmailSendLog.campaign_id == campaign_id,
        EmailSendLog.message_id.is_not(None),
    )
    legacy = list((await db.execute(
        select(Message.status).where(
            Message.ads_campaign_id == campaign_id,
            Message.direction == "outgoing",
            Message.id.not_in(known_ids),
        )
    )).scalars().all())
    statuses = [row.status for row in latest_by_recipient.values()] + [
        "delivered" if status == "delivered" else "sent" if status == "sent" else status
        for status in legacy
    ]
    return {
        "sent": sum(status in ("sent", "delivered") for status in statuses),
        "delivered": statuses.count("delivered"),
        "bounced": statuses.count("bounced"),
        "failed": statuses.count("failed"),
        "deferred": statuses.count("deferred"),
        "retrying": statuses.count("retrying"),
        "skipped": statuses.count("skipped"),
        "queued": statuses.count("queued") + statuses.count("sending"),
        "total": len(statuses),
        "attempts": len(attempts),
        "retries": sum(max(int(row.attempt or 1) - 1, 0) for row in attempts),
    }
