"""One definition of every message count, shared by every endpoint that reports one.

``dashboard/stats``, ``analytics/*``, ``email/stats`` and ``email/overview`` each used to count
messages their own way, so one database produced several sets of "sent / delivered / failed":
different SCOPE (the "SMS" analytics had no channel filter; the dashboard was all-time, analytics
30 days, email 29), a different WINDOW edge, a different meaning of DELIVERED (the dashboard wanted
``status == delivered``; email also accepted a delivery time or an open) and of FAILED (the funnel
counted a cancelled message as a failed send). They all ask this module now.

The wording is part of the contract: every response that carries these numbers includes ``scope``
(which channel, how many days) and ``definitions`` (this file's ``DEFINITIONS``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Message

#: What each number means. Quoted verbatim in the ``definitions`` of every response.
DEFINITIONS = {
    "sent": "outgoing messages created in the scope, including ones still queued or that failed",
    "delivered": "outgoing messages that reached the recipient: status delivered, a delivery time, or an open",
    "failed": "outgoing messages whose status is failed (a cancelled message is not a failure)",
    "queued": "outgoing messages waiting to go (queued, sending or retrying)",
    "replies": "incoming messages in the scope",
}

OUTGOING = Message.direction == "outgoing"
INCOMING = Message.direction == "incoming"
DELIVERED = or_(
    Message.status == "delivered", Message.delivered_at.isnot(None), Message.opened_at.isnot(None)
)
FAILED = Message.status == "failed"
CANCELLED = Message.status == "cancelled"
QUEUED = Message.status.in_(("queued", "sending", "retrying"))
OPENED = or_(Message.open_count > 0, Message.opened_at.isnot(None))
CLICKED = or_(Message.click_count > 0, Message.clicked_at.isnot(None))


def window_start(days: Optional[int]) -> Optional[datetime]:
    """Start of "the last ``days`` days": midnight UTC, ``days - 1`` days back (so today counts as
    one of them). The same instant for every endpoint, and the first bucket of every daily series."""
    if not days:
        return None
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=days - 1)


def scope(channel: Optional[str], days: Optional[int]) -> dict:
    return {"channel": channel or "all", "days": days}


def channel_condition(channel: Optional[str]):
    """``None`` for all channels, else the SQL condition for one. Rows from before the channel
    column existed read NULL and are SMS."""
    if not channel or channel == "all":
        return None
    return func.coalesce(Message.channel, "sms") == channel


def message_filters(
    *, channel: Optional[str] = None, days: Optional[int] = None,
    campaign_id: Optional[int] = None, account_id: Optional[int] = None,
) -> list:
    filters = []
    condition = channel_condition(channel)
    if condition is not None:
        filters.append(condition)
    start = window_start(days)
    if start is not None:
        filters.append(Message.created_at >= start)
    if campaign_id:
        filters.append(Message.campaign_id == campaign_id)
    if account_id:
        filters.append(Message.email_account_id == account_id)
    return filters


def rate(part: int, whole: int, *, places: int = 1) -> float:
    return round(part / whole * 100, places) if whole else 0.0


async def message_counts(
    db: AsyncSession, *, channel: Optional[str] = None, days: Optional[int] = None,
    campaign_id: Optional[int] = None, account_id: Optional[int] = None,
) -> dict:
    """Every count for one scope, in a single query."""

    def n(condition):
        return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)

    row = (
        await db.execute(
            select(
                n(OUTGOING).label("sent"),
                n(OUTGOING & DELIVERED).label("delivered"),
                n(OUTGOING & FAILED).label("failed"),
                n(OUTGOING & CANCELLED).label("cancelled"),
                n(OUTGOING & QUEUED).label("queued"),
                n(INCOMING).label("replies"),
                n(OUTGOING & OPENED).label("opened"),
                n(OUTGOING & CLICKED).label("clicked"),
                func.coalesce(func.sum(case((OUTGOING, Message.open_count), else_=0)), 0).label("opens"),
                func.coalesce(func.sum(case((OUTGOING, Message.click_count), else_=0)), 0).label("clicks"),
                func.count(func.distinct(case((OUTGOING, Message.contact_id), else_=None))).label("contacted"),
            ).where(*message_filters(channel=channel, days=days, campaign_id=campaign_id, account_id=account_id))
        )
    ).one()
    return {key: int(getattr(row, key) or 0) for key in row._fields}
