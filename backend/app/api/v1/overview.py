"""Unified campaign + inbox overview.

WHY THIS EXISTS
---------------
The app grew two campaign systems -- the classic ``campaigns`` table and the
SMS Ads Manager's ``ads_campaigns`` -- plus an inbox that knew about neither.
The Dashboard read one, the SMS Manager read the other, and the inbox read
conversations, so no single screen could answer "what is running, how is it
doing, and where did these leads come from?".

This module is the join. It reports both systems side by side, in one shape,
with numbers derived from the SAME tables the inbox renders (``messages`` and
``conversations``) so the overview and the inbox can never disagree.

It is strictly read-only and additive: no existing endpoint changes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.campaign import Campaign, CampaignContact
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.security.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

#: Statuses that mean "this campaign is working right now".
LIVE_CLASSIC = ("running", "scheduled", "paused")
LIVE_ADS = ("active", "scheduled", "paused")


def _rate(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


async def _replied_conversation_ids(db: AsyncSession) -> set[int]:
    """Conversation ids that contain at least one inbound message."""
    return set(
        (
            await db.execute(
                select(Message.conversation_id).where(Message.direction == "incoming").distinct()
            )
        ).scalars().all()
    )


async def _attributed_totals(db: AsyncSession, replied_ids: set[int]) -> dict[str, int]:
    """People-level totals, counting each conversation exactly once.

    Per-campaign rows deliberately count a lead under every campaign that
    touched them: a contact who was cold-called and then retargeted really is
    a lead for both, and each campaign's own reply rate must reflect that.

    Summing those rows, however, counts that person twice. The overview shows
    the per-campaign numbers and the roll-up on the same screen, so a naive
    sum reads as a contradiction -- "13 leads" above a list of 10 people.
    These totals answer the different question the header is really asking:
    how many distinct people are we talking to?
    """
    convs = list(
        (
            await db.execute(
                select(Conversation).where(
                    or_(
                        Conversation.campaign_id.is_not(None),
                        Conversation.last_campaign_id.is_not(None),
                        Conversation.ads_campaign_id.is_not(None),
                        Conversation.last_ads_campaign_id.is_not(None),
                    )
                )
            )
        ).scalars().all()
    )
    totals = {"leads": 0, "replied": 0, "unread": 0, "interested": 0}
    for conv in convs:
        totals["leads"] += 1
        if conv.id in replied_ids:
            totals["replied"] += 1
        if conv.unread_count or conv.status == "unread":
            totals["unread"] += 1
        if conv.status == "interested":
            totals["interested"] += 1
    return totals


async def _classic_rows(db: AsyncSession, replied_ids: set[int]) -> list[dict]:
    """Every classic campaign, with inbox-derived engagement counts."""
    campaigns = list((await db.execute(select(Campaign))).scalars().all())
    if not campaigns:
        return []

    # One pass over the message table, grouped, instead of N queries.
    sent_rows = (
        await db.execute(
            select(Message.campaign_id, Message.status, func.count(Message.id))
            .where(Message.campaign_id.is_not(None), Message.direction == "outgoing")
            .group_by(Message.campaign_id, Message.status)
        )
    ).all()
    by_campaign: dict[int, dict[str, int]] = {}
    for cid, status, n in sent_rows:
        by_campaign.setdefault(cid, {})[status or "unknown"] = n

    # Conversation attribution, again in one pass.
    convs = list(
        (
            await db.execute(
                select(Conversation).where(
                    or_(
                        Conversation.campaign_id.is_not(None),
                        Conversation.last_campaign_id.is_not(None),
                    )
                )
            )
        ).scalars().all()
    )
    engagement: dict[int, dict[str, int]] = {}
    for conv in convs:
        for cid in {conv.campaign_id, conv.last_campaign_id}:
            if not cid:
                continue
            bucket = engagement.setdefault(cid, {"leads": 0, "replied": 0, "unread": 0, "interested": 0})
            bucket["leads"] += 1
            if conv.id in replied_ids:
                bucket["replied"] += 1
            if conv.unread_count or conv.status == "unread":
                bucket["unread"] += 1
            if conv.status == "interested":
                bucket["interested"] += 1

    out = []
    for camp in campaigns:
        statuses = by_campaign.get(camp.id, {})
        delivered = statuses.get("delivered", 0)
        sent = delivered + statuses.get("sent", 0)
        failed = statuses.get("failed", 0) + statuses.get("cancelled", 0)
        queued = statuses.get("queued", 0) + statuses.get("sending", 0)
        eng = engagement.get(camp.id, {"leads": 0, "replied": 0, "unread": 0, "interested": 0})
        out.append(
            {
                "id": camp.id,
                "kind": "campaign",
                "name": camp.name,
                "description": camp.description,
                "status": camp.status,
                "is_live": camp.status in LIVE_CLASSIC,
                "audience": camp.total_contacts,
                "sent": sent,
                "delivered": delivered,
                "failed": failed,
                "queued": queued,
                "leads": eng["leads"],
                "replied": eng["replied"],
                "unread": eng["unread"],
                "interested": eng["interested"],
                "delivery_rate": _rate(delivered, sent),
                "reply_rate": _rate(eng["replied"], sent),
                "scheduled_start_at": _iso(camp.scheduled_start_at),
                "started_at": _iso(camp.started_at),
                "completed_at": _iso(camp.completed_at),
                "updated_at": _iso(camp.updated_at),
                # Deep links the UI turns into buttons.
                "inbox_url": f"/inbox?campaign_id={camp.id}",
                "replies_url": f"/inbox?campaign_id={camp.id}&replied=1",
            }
        )
    return out


async def _ads_rows(db: AsyncSession, replied_ids: set[int]) -> list[dict]:
    """Every SMS Ads Manager campaign, in the same shape as the classic ones."""
    try:
        from app.models.ads import AdsAssignment, AdsCampaign
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("overview: ads models unavailable: %s", exc)
        return []

    campaigns = list((await db.execute(select(AdsCampaign))).scalars().all())
    if not campaigns:
        return []

    assignment_rows = (
        await db.execute(
            select(
                AdsAssignment.campaign_id,
                AdsAssignment.send_status,
                AdsAssignment.delivery_status,
                func.count(AdsAssignment.id),
            ).group_by(
                AdsAssignment.campaign_id,
                AdsAssignment.send_status,
                AdsAssignment.delivery_status,
            )
        )
    ).all()
    stats: dict[int, dict[str, int]] = {}
    for cid, send_status, delivery_status, n in assignment_rows:
        bucket = stats.setdefault(
            cid, {"assigned": 0, "sent": 0, "delivered": 0, "failed": 0, "pending": 0}
        )
        bucket["assigned"] += n
        if send_status in ("sent", "delivered"):
            bucket["sent"] += n
        if (delivery_status or "") in ("delivered", "simulated"):
            bucket["delivered"] += n
        if send_status == "failed":
            bucket["failed"] += n
        if send_status == "pending":
            bucket["pending"] += n

    convs = list(
        (
            await db.execute(
                select(Conversation).where(
                    or_(
                        Conversation.ads_campaign_id.is_not(None),
                        Conversation.last_ads_campaign_id.is_not(None),
                    )
                )
            )
        ).scalars().all()
    )
    engagement: dict[int, dict[str, int]] = {}
    for conv in convs:
        for cid in {conv.ads_campaign_id, conv.last_ads_campaign_id}:
            if not cid:
                continue
            bucket = engagement.setdefault(cid, {"leads": 0, "replied": 0, "unread": 0, "interested": 0})
            bucket["leads"] += 1
            if conv.id in replied_ids:
                bucket["replied"] += 1
            if conv.unread_count or conv.status == "unread":
                bucket["unread"] += 1
            if conv.status == "interested":
                bucket["interested"] += 1

    out = []
    for camp in campaigns:
        s = stats.get(camp.id, {"assigned": 0, "sent": 0, "delivered": 0, "failed": 0, "pending": 0})
        eng = engagement.get(camp.id, {"leads": 0, "replied": 0, "unread": 0, "interested": 0})
        out.append(
            {
                "id": camp.id,
                "kind": "ads",
                "name": camp.name,
                "description": camp.description,
                "status": camp.status,
                "is_live": camp.status in LIVE_ADS,
                "objective": camp.objective,
                "audience": s["assigned"],
                "sent": s["sent"],
                "delivered": s["delivered"],
                "failed": s["failed"],
                "queued": s["pending"],
                "leads": eng["leads"],
                "replied": eng["replied"],
                "unread": eng["unread"],
                "interested": eng["interested"],
                "delivery_rate": _rate(s["delivered"], s["sent"]),
                "reply_rate": _rate(eng["replied"], s["sent"]),
                "scheduled_start_at": _iso(camp.start_date),
                "started_at": _iso(camp.start_date),
                "completed_at": _iso(camp.end_date),
                "updated_at": _iso(camp.updated_at),
                "inbox_url": f"/inbox?ads_campaign_id={camp.id}",
                "replies_url": f"/inbox?ads_campaign_id={camp.id}&replied=1",
            }
        )
    return out


@router.get("/campaigns")
async def campaign_overview(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Both campaign systems in one list, sorted by what is live right now."""
    replied_ids = await _replied_conversation_ids(db)
    rows = await _classic_rows(db, replied_ids) + await _ads_rows(db, replied_ids)
    rows.sort(key=lambda r: (not r["is_live"], -(r["sent"] or 0), r["name"]))

    def total(key: str) -> int:
        return sum(r.get(key) or 0 for r in rows)

    sent = total("sent")
    delivered = total("delivered")
    # Message counts are per-send and safe to add up. People counts are not:
    # one contact can be a lead for several campaigns, so they are recounted
    # from conversations to keep the header consistent with the list below it.
    people = await _attributed_totals(db, replied_ids)
    return {
        "items": rows,
        "totals": {
            "campaigns": len(rows),
            "live": sum(1 for r in rows if r["is_live"]),
            "audience": total("audience"),
            "sent": sent,
            "delivered": delivered,
            "failed": total("failed"),
            "queued": total("queued"),
            "leads": people["leads"],
            "replied": people["replied"],
            "unread": people["unread"],
            "interested": people["interested"],
            "delivery_rate": _rate(delivered, sent),
            "reply_rate": _rate(people["replied"], sent),
        },
    }


@router.get("/metrics")
async def unified_metrics(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Everything the application-wide overview screen needs, in one call.

    Deliberately one endpoint rather than five: the dashboard used to fire
    several requests that each recomputed overlapping numbers from different
    tables, which is how the Dashboard and the SMS Manager ended up quoting
    different reply rates for the same day.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    replied_ids = await _replied_conversation_ids(db)

    async def count_messages(*where) -> int:
        return (
            await db.execute(
                select(func.count()).select_from(select(Message).where(*where).subquery())
            )
        ).scalar() or 0

    window = (Message.created_at >= since,)
    outgoing = (Message.direction == "outgoing", *window)
    sent = await count_messages(*outgoing, Message.status.in_(("sent", "delivered")))
    delivered = await count_messages(*outgoing, Message.status == "delivered")
    failed = await count_messages(*outgoing, Message.status.in_(("failed", "cancelled")))
    queued = await count_messages(*outgoing, Message.status.in_(("queued", "sending")))
    inbound = await count_messages(Message.direction == "incoming", *window)

    total_conversations = (
        await db.execute(select(func.count()).select_from(Conversation))
    ).scalar() or 0
    unread_threads = (
        await db.execute(
            select(func.count()).select_from(
                select(Conversation)
                .where(or_(Conversation.unread_count > 0, Conversation.status == "unread"))
                .subquery()
            )
        )
    ).scalar() or 0
    interested_threads = (
        await db.execute(
            select(func.count()).select_from(
                select(Conversation).where(Conversation.status == "interested").subquery()
            )
        )
    ).scalar() or 0

    # Reply sentiment across the window, from the keyless classifier.
    sentiment = {"positive": 0, "negative": 0, "neutral": 0}
    rows = (
        await db.execute(
            select(Message.ai_sentiment, func.count(Message.id))
            .where(Message.direction == "incoming", *window)
            .group_by(Message.ai_sentiment)
        )
    ).all()
    for value, n in rows:
        key = (value or "neutral").lower()
        if key in sentiment:
            sentiment[key] += n

    total_contacts = (await db.execute(select(func.count()).select_from(Contact))).scalar() or 0
    opted_out = (
        await db.execute(
            select(func.count()).select_from(
                select(Contact).where(Contact.is_opted_out.is_(True)).subquery()
            )
        )
    ).scalar() or 0

    # Daily series: sent vs replies, zero-filled so the chart has no gaps.
    series: dict[str, dict] = {}
    for i in range(days):
        day = (since + timedelta(days=i)).date().isoformat()
        series[day] = {"date": day, "sent": 0, "replies": 0, "delivered": 0, "failed": 0}
    day_rows = (
        await db.execute(
            select(
                func.date(Message.created_at),
                Message.direction,
                Message.status,
                func.count(Message.id),
            )
            .where(*window)
            .group_by(func.date(Message.created_at), Message.direction, Message.status)
        )
    ).all()
    for day, direction, status, n in day_rows:
        key = str(day)[:10]
        bucket = series.get(key)
        if bucket is None:
            continue
        if direction == "incoming":
            bucket["replies"] += n
        else:
            if status in ("sent", "delivered"):
                bucket["sent"] += n
            if status == "delivered":
                bucket["delivered"] += n
            if status in ("failed", "cancelled"):
                bucket["failed"] += n

    campaign_data = await campaign_overview(db=db, current_user=current_user)

    return {
        "period_days": days,
        "messaging": {
            "sent": sent,
            "delivered": delivered,
            "failed": failed,
            "queued": queued,
            "replies": inbound,
            "delivery_rate": _rate(delivered, sent),
            "reply_rate": _rate(inbound, sent),
            "failure_rate": _rate(failed, sent + failed),
        },
        "inbox": {
            "conversations": total_conversations,
            "replied": len(replied_ids),
            "unread": unread_threads,
            "interested": interested_threads,
            "sentiment": sentiment,
        },
        "audience": {
            "contacts": total_contacts,
            "opted_out": opted_out,
            "opt_out_rate": _rate(opted_out, total_contacts),
        },
        "campaigns": campaign_data["totals"],
        "top_campaigns": campaign_data["items"][:8],
        "series": list(series.values()),
    }
