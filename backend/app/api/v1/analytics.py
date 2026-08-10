"""
Analytics API routes.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
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
