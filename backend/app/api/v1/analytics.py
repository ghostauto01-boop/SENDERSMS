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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get analytics overview."""
    start_date = datetime.now(timezone.utc) - timedelta(days=days)

    # Base queries
    msg_query = select(Message)
    if campaign_id:
        msg_query = msg_query.where(Message.campaign_id == campaign_id)
    msg_query = msg_query.where(Message.created_at >= start_date)

    # Total SMS
    total_sms_query = select(func.count()).select_from(msg_query.subquery())
    total_sms = (await db.execute(total_sms_query)).scalar() or 0

    # Sent
    sent_query = select(func.count()).select_from(
        msg_query.where(Message.direction == "outgoing").subquery()
    )
    sent = (await db.execute(sent_query)).scalar() or 0

    # Delivered
    delivered_query = select(func.count()).select_from(
        select(Message).where(
            Message.direction == "outgoing",
            Message.status == "delivered",
            Message.created_at >= start_date,
        ).subquery()
    )
    if campaign_id:
        delivered_query = select(func.count()).select_from(
            select(Message).where(
                Message.direction == "outgoing",
                Message.status == "delivered",
                Message.created_at >= start_date,
                Message.campaign_id == campaign_id,
            ).subquery()
        )
    delivered = (await db.execute(delivered_query)).scalar() or 0

    # Failed
    failed_query = select(func.count()).select_from(
        select(Message).where(
            Message.direction == "outgoing",
            Message.status == "failed",
            Message.created_at >= start_date,
        ).subquery()
    )
    if campaign_id:
        failed_query = select(func.count()).select_from(
            select(Message).where(
                Message.direction == "outgoing",
                Message.status == "failed",
                Message.created_at >= start_date,
                Message.campaign_id == campaign_id,
            ).subquery()
        )
    failed = (await db.execute(failed_query)).scalar() or 0

    # Replies
    replies_query = select(func.count()).select_from(
        select(Message).where(
            Message.direction == "incoming",
            Message.created_at >= start_date,
        ).subquery()
    )
    if campaign_id:
        replies_query = select(func.count()).select_from(
            select(Message).where(
                Message.direction == "incoming",
                Message.created_at >= start_date,
                Message.campaign_id == campaign_id,
            ).subquery()
        )
    replies = (await db.execute(replies_query)).scalar() or 0

    # Delivery rate
    delivery_rate = round(delivered / max(sent, 1) * 100, 1)

    # Reply rate
    reply_rate = round(replies / max(sent, 1) * 100, 1)

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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Per-day sent / delivered / failed / replies for line & bar charts."""
    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    day = _day_floor(Message.created_at)
    q = (
        select(
            day.label("day"),
            func.sum(case((Message.direction == "outgoing", 1), else_=0)).label("sent"),
            func.sum(case(
                ((Message.direction == "outgoing") & (Message.status == "delivered"), 1),
                else_=0)).label("delivered"),
            func.sum(case(
                ((Message.direction == "outgoing") & (Message.status.in_(["failed", "cancelled"])), 1),
                else_=0)).label("failed"),
            func.sum(case((Message.direction == "incoming", 1), else_=0)).label("replies"),
        )
        .where(Message.created_at >= start_date)
    )
    if campaign_id:
        q = q.where(Message.campaign_id == campaign_id)
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
    return {"period_days": days, "points": points}


@router.get("/funnel")
async def get_funnel(
    days: int = Query(default=30, ge=1, le=365),
    campaign_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Sent -> delivered -> replied -> interested funnel + failure reasons."""
    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    base = [Message.created_at >= start_date]
    if campaign_id:
        base.append(Message.campaign_id == campaign_id)

    async def _count(*conds):
        q = select(func.count()).where(*base, *conds)
        return (await db.execute(q)).scalar() or 0

    sent = await _count(Message.direction == "outgoing")
    delivered = await _count(Message.direction == "outgoing", Message.status == "delivered")
    failed = await _count(Message.direction == "outgoing",
                          Message.status.in_(["failed", "cancelled"]))
    replies = await _count(Message.direction == "incoming")
    interested = (await db.execute(
        select(func.count()).where(Contact.lead_status == "interested"))).scalar() or 0

    # Top failure reasons for the "why did sends fail" breakdown.
    rq = (
        select(Message.last_error, func.count().label("n"))
        .where(*base, Message.direction == "outgoing",
               Message.status.in_(["failed", "cancelled"]))
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
        "delivery_rate": round(delivered / max(sent, 1) * 100, 1),
        "reply_rate": round(replies / max(sent, 1) * 100, 1),
        "failure_reasons": reasons,
        "quarantine_reasons": quarantine,
    }


@router.get("/by-campaign")
async def get_by_campaign(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Per-campaign sent/delivered/replies comparison for bar charts."""
    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    q = (
        select(
            Message.campaign_id,
            func.sum(case((Message.direction == "outgoing", 1), else_=0)).label("sent"),
            func.sum(case(
                ((Message.direction == "outgoing") & (Message.status == "delivered"), 1),
                else_=0)).label("delivered"),
            func.sum(case((Message.direction == "incoming", 1), else_=0)).label("replies"),
        )
        .where(Message.created_at >= start_date, Message.campaign_id.isnot(None))
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
