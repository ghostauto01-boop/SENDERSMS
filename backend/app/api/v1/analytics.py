"""
Analytics API routes.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contact import Contact
from app.models.campaign import Campaign
from app.models.conversation import Message
from app.models.followup import FollowUp
from app.models.user import User
from app.security.auth import get_current_user

router = APIRouter()


@router.get("/overview")
async def get_analytics_overview(
    days: int = Query(default=30, ge=1, le=365),
    campaign_id: Optional[int] = None,
    channel: Optional[str] = Query(default="sms", description="sms | email | all"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Analytics overview for one channel (SMS unless told otherwise).

    It is the SMS analytics page's headline, and it used to count EMAIL rows too (no channel
    filter at all). The numbers now come from ``services/message_metrics`` -- the same
    definitions and window the dashboard and the email endpoints use.
    """
    from app.services import message_metrics as metrics
    from app.utils.channels import validate_channel

    wanted = validate_channel(channel, default="sms")
    scoped = None if wanted == "all" else wanted
    start_date = metrics.window_start(days)

    counts = await metrics.message_counts(db, channel=scoped, days=days, campaign_id=campaign_id)
    sent, delivered, failed, replies = (counts[k] for k in ("sent", "delivered", "failed", "replies"))
    total_sms = sent + replies

    delivery_rate = metrics.rate(delivered, max(sent, 1))
    reply_rate = metrics.rate(replies, max(sent, 1))

    # Opt-outs
    opt_query = select(func.count()).select_from(
        select(Contact).where(
            Contact.is_opted_out == True,
            Contact.opted_out_at >= start_date,
        ).subquery()
    )
    opt_outs = (await db.execute(opt_query)).scalar() or 0

    # Follow-ups
    fup_query = select(func.count()).select_from(
        select(FollowUp).where(FollowUp.created_at >= start_date).subquery()
    )
    followups = (await db.execute(fup_query)).scalar() or 0

    # Interested leads
    int_query = select(func.count()).select_from(
        select(Contact).where(Contact.lead_status == "interested").subquery()
    )
    interested = (await db.execute(int_query)).scalar() or 0

    return {
        "total_sms": total_sms,
        "sent": sent,
        "delivered": delivered,
        "failed": failed,
        "delivery_rate": delivery_rate,
        "replies": replies,
        "reply_rate": reply_rate,
        "opt_outs": opt_outs,
        "followups": followups,
        "interested_leads": interested,
        "period_days": days,
        "queued": counts["queued"],
        "scope": metrics.scope(scoped, days),
        "definitions": metrics.DEFINITIONS,
    }


# --------------------------------------------------------------------------
# Chart endpoints (single grouped query each — cheap enough to poll)
# --------------------------------------------------------------------------

def _day_floor(col):
    """Portable DATE() truncation for SQLite + Postgres."""
    from sqlalchemy import func as _f
    return _f.date(col)


@router.get("/timeseries")
async def get_timeseries(
    days: int = Query(default=30, ge=1, le=365),
    campaign_id: Optional[int] = None,
    channel: Optional[str] = Query(default="sms", description="sms | email | all"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Per-day sent / delivered / failed / replies for line & bar charts (same definitions and
    window as the overview, so the points add up to its headline)."""
    from app.services import message_metrics as metrics
    from app.utils.channels import validate_channel

    wanted = validate_channel(channel, default="sms")
    day = _day_floor(Message.created_at)
    q = (
        select(
            day.label("day"),
            func.sum(case((metrics.OUTGOING, 1), else_=0)).label("sent"),
            func.sum(case((metrics.OUTGOING & metrics.DELIVERED, 1), else_=0)).label("delivered"),
            func.sum(case((metrics.OUTGOING & metrics.FAILED, 1), else_=0)).label("failed"),
            func.sum(case((metrics.INCOMING, 1), else_=0)).label("replies"),
        )
        .where(*metrics.message_filters(
            channel=None if wanted == "all" else wanted, days=days, campaign_id=campaign_id))
    )
    q = q.group_by(day).order_by(day)
    rows = (await db.execute(q)).all()
    points = []
    for r in rows:
        sent = int(r.sent or 0)
        delivered = int(r.delivered or 0)
        failed = int(r.failed or 0)
        replies = int(r.replies or 0)
        points.append({
            "date": str(r.day),
            "sent": sent,
            "delivered": delivered,
            "failed": failed,
            "replies": replies,
            "delivery_rate": round(delivered / max(sent, 1) * 100, 1),
            "reply_rate": round(replies / max(sent, 1) * 100, 1),
        })
    return {"period_days": days, "points": points,
            "scope": metrics.scope(None if wanted == "all" else wanted, days)}


@router.get("/funnel")
async def get_funnel(
    days: int = Query(default=30, ge=1, le=365),
    campaign_id: Optional[int] = None,
    channel: Optional[str] = Query(default="sms", description="sms | email | all"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Sent -> delivered -> replied -> interested funnel + failure reasons (same definitions and
    window as the overview; a cancelled message is reported on its own, not as a failure)."""
    from app.services import message_metrics as metrics
    from app.utils.channels import validate_channel

    wanted = validate_channel(channel, default="sms")
    scoped = None if wanted == "all" else wanted
    base = metrics.message_filters(channel=scoped, days=days, campaign_id=campaign_id)
    counts = await metrics.message_counts(db, channel=scoped, days=days, campaign_id=campaign_id)
    sent, delivered, failed, replies = (counts[k] for k in ("sent", "delivered", "failed", "replies"))
    interested = (await db.execute(
        select(func.count()).where(Contact.lead_status == "interested"))).scalar() or 0

    # Top failure reasons for the "why did sends fail" breakdown.
    rq = (
        select(Message.last_error, func.count().label("n"))
        .where(*base, metrics.OUTGOING, metrics.FAILED)
        .group_by(Message.last_error).order_by(func.count().desc()).limit(8)
    )
    reasons = [{"reason": (r[0] or "Unknown")[:80], "count": int(r[1])}
               for r in (await db.execute(rq)).all()]

    # Quarantine reasons (numbers the pre-send filter is protecting you from).
    qq = (
        select(Contact.undeliverable_reason, func.count().label("n"))
        .where(Contact.is_undeliverable.is_(True))
        .group_by(Contact.undeliverable_reason).order_by(func.count().desc()).limit(8)
    )
    quarantine = [{"reason": (r[0] or "Unknown")[:80], "count": int(r[1])}
                  for r in (await db.execute(qq)).all()]

    return {
        "period_days": days,
        "stages": [
            {"stage": "Sent", "count": sent},
            {"stage": "Delivered", "count": delivered},
            {"stage": "Replied", "count": replies},
            {"stage": "Interested", "count": interested},
        ],
        "failed": failed,
        "cancelled": counts["cancelled"],
        "delivery_rate": metrics.rate(delivered, max(sent, 1)),
        "reply_rate": metrics.rate(replies, max(sent, 1)),
        "scope": metrics.scope(scoped, days),
        "failure_reasons": reasons,
        "quarantine_reasons": quarantine,
    }


@router.get("/by-campaign")
async def get_by_campaign(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=10, ge=1, le=50),
    channel: Optional[str] = Query(default="sms", description="sms | email | all"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Per-campaign sent/delivered/replies comparison for bar charts (same definitions and window
    as the overview)."""
    from app.services import message_metrics as metrics
    from app.utils.channels import validate_channel

    wanted = validate_channel(channel, default="sms")
    q = (
        select(
            Message.campaign_id,
            func.sum(case((metrics.OUTGOING, 1), else_=0)).label("sent"),
            func.sum(case((metrics.OUTGOING & metrics.DELIVERED, 1), else_=0)).label("delivered"),
            func.sum(case((metrics.INCOMING, 1), else_=0)).label("replies"),
        )
        .where(*metrics.message_filters(channel=None if wanted == "all" else wanted, days=days),
               Message.campaign_id.isnot(None))
        .group_by(Message.campaign_id).order_by(func.count().desc()).limit(limit)
    )
    rows = (await db.execute(q)).all()
    ids = [r[0] for r in rows]
    names: dict = {}
    if ids:
        for c in (await db.execute(select(Campaign).where(Campaign.id.in_(ids)))).scalars().all():
            names[c.id] = c.name
    items = []
    for cid, sent, delivered, replies in rows:
        sent = int(sent or 0)
        delivered = int(delivered or 0)
        items.append({
            "campaign_id": cid,
            "campaign_name": names.get(cid, f"Campaign #{cid}"),
            "sent": sent,
            "delivered": delivered,
            "replies": int(replies or 0),
            "delivery_rate": round(delivered / max(sent, 1) * 100, 1),
        })
    return {"period_days": days, "campaigns": items}


@router.get("/calls")
async def get_call_stats(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Call volume + outcomes for the analytics page (CallGate)."""
    from app.models.call import CallLog

    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    day = _day_floor(CallLog.created_at)
    q = (
        select(
            day.label("day"),
            func.count().label("total"),
            func.sum(case((CallLog.status == "ended", 1), else_=0)).label("connected"),
            func.sum(case((CallLog.status == "failed", 1), else_=0)).label("failed"),
        )
        .where(CallLog.created_at >= start_date)
        .group_by(day).order_by(day)
    )
    points = [{"date": str(r.day), "calls": int(r.total or 0),
               "connected": int(r.connected or 0), "failed": int(r.failed or 0)}
              for r in (await db.execute(q)).all()]
    total = sum(p["calls"] for p in points)
    connected = sum(p["connected"] for p in points)
    return {"period_days": days, "total": total, "connected": connected,
            "connect_rate": round(connected / max(total, 1) * 100, 1),
            "points": points}
