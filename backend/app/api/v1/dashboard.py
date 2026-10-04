"""
Dashboard API routes.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contact import Contact
from app.models.campaign import Campaign
from app.models.conversation import Conversation, Message
from app.models.followup import FollowUp
from app.security.auth import get_current_user
from app.utils.datetime import local_day_utc_bounds
from app.models.user import User

router = APIRouter()


def _channel_filter(query, channel):
    """Scope a Message/Conversation query to one channel.

    ``None``/``all`` leaves the query untouched (the historical behaviour), so
    every existing caller keeps seeing combined numbers until it asks for a
    specific channel.
    """
    if not channel or str(channel).lower() == "all":
        return query
    wanted = str(channel).lower()
    return query.where(func.coalesce(Message.channel, "sms") == wanted)


@router.get("/stats")
async def get_dashboard_stats(
    channel: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get dashboard summary statistics (optionally for one channel only)."""
    wanted = None if (not channel or str(channel).lower() == "all") else str(channel).lower()
    # Total contacts
    total_result = await db.execute(select(func.count(Contact.id)))
    total_contacts = total_result.scalar() or 0

    # Contacts contacted (have messages)
    contacted_result = await db.execute(
        _channel_filter(
            select(func.count(func.distinct(Message.contact_id))).where(
                Message.direction == "outgoing"
            ),
            wanted,
        )
    )
    contacted = contacted_result.scalar() or 0

    # Messages sent
    sent_result = await db.execute(
        _channel_filter(select(func.count(Message.id)).where(Message.direction == "outgoing"), wanted)
    )
    messages_sent = sent_result.scalar() or 0

    # Delivered
    delivered_result = await db.execute(
        _channel_filter(
            select(func.count(Message.id)).where(
                Message.direction == "outgoing",
                Message.status == "delivered",
            ),
            wanted,
        )
    )
    delivered = delivered_result.scalar() or 0

    # Failed
    failed_result = await db.execute(
        _channel_filter(
            select(func.count(Message.id)).where(
                Message.direction == "outgoing",
                Message.status == "failed",
            ),
            wanted,
        )
    )
    failed = failed_result.scalar() or 0

    # Replies
    replies_result = await db.execute(
        _channel_filter(select(func.count(Message.id)).where(Message.direction == "incoming"), wanted)
    )
    replies = replies_result.scalar() or 0

    # Reply rate
    reply_rate = round(replies / max(messages_sent, 1) * 100, 1)

    # Delivery rate
    delivery_rate = round(delivered / max(messages_sent, 1) * 100, 1)

    # Interested leads
    interested_result = await db.execute(
        select(func.count(Contact.id)).where(Contact.lead_status == "interested")
    )
    interested = interested_result.scalar() or 0

    # Active campaigns
    active_result = await db.execute(
        select(func.count(Campaign.id)).where(Campaign.status == "running")
    )
    active_campaigns = active_result.scalar() or 0

    # Follow-ups due today
    today_start, today_end = local_day_utc_bounds()
    due_result = await db.execute(
        select(func.count(FollowUp.id)).where(
            FollowUp.status == "pending",
            FollowUp.scheduled_at >= today_start,
            FollowUp.scheduled_at < today_end,
        )
    )
    followups_due = due_result.scalar() or 0

    # Overdue follow-ups
    overdue_result = await db.execute(
        select(func.count(FollowUp.id)).where(
            FollowUp.status == "pending",
            FollowUp.scheduled_at < today_start,
        )
    )
    overdue = overdue_result.scalar() or 0

    # Completed campaigns
    completed_result = await db.execute(
        select(func.count(Campaign.id)).where(Campaign.status == "completed")
    )
    completed_campaigns = completed_result.scalar() or 0

    # Gateway status (from config — no DB table)
    gateway_status = "configured"

    # Recent conversations
    conv_query = (
        select(Conversation, Contact.business_name, Contact.first_name, Contact.last_name)
        .join(Contact, Conversation.contact_id == Contact.id)
    )
    if wanted:
        conv_query = conv_query.where(func.coalesce(Conversation.channel, "sms") == wanted)
    conv_result = await db.execute(
        conv_query.order_by(Conversation.last_message_at.desc().nullslast()).limit(10)
    )
    recent_convos = []
    for row in conv_result:
        conv, biz_name, first, last = row
        recent_convos.append({
            "id": conv.id,
            "contact_id": conv.contact_id,
            "contact_name": biz_name or f"{first or ''} {last or ''}".strip() or "Unknown",
            "status": conv.status,
            "last_message_preview": conv.last_message_preview,
            "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
            "unread_count": conv.unread_count,
        })

    # Recent campaign activity
    camp_query = select(Campaign)
    if wanted:
        camp_query = camp_query.where(func.coalesce(Campaign.channel, "sms") == wanted)
    camp_result = await db.execute(camp_query.order_by(Campaign.updated_at.desc()).limit(5))
    recent_campaigns = []
    for camp in camp_result.scalars().all():
        recent_campaigns.append({
            "id": camp.id,
            "name": camp.name,
            "status": camp.status,
            "channel": getattr(camp, "channel", "sms") or "sms",
            "messages_sent": camp.messages_sent,
            "replies": camp.replies,
            "updated_at": camp.updated_at.isoformat(),
        })

    # Lead status distribution
    status_result = await db.execute(
        select(Contact.lead_status, func.count(Contact.id)).group_by(Contact.lead_status)
    )
    lead_distribution = {row[0]: row[1] for row in status_result}

    return {
        "total_contacts": total_contacts,
        "contacts_contacted": contacted,
        "messages_sent": messages_sent,
        "messages_delivered": delivered,
        "messages_failed": failed,
        "replies": replies,
        "reply_rate": reply_rate,
        "delivery_rate": delivery_rate,
        "interested_leads": interested,
        "active_campaigns": active_campaigns,
        "followups_due_today": followups_due,
        "overdue_followups": overdue,
        "completed_campaigns": completed_campaigns,
        "gateway_status": gateway_status,
        "recent_conversations": recent_convos,
        "recent_campaigns": recent_campaigns,
        "lead_distribution": lead_distribution,
    }


@router.get("/charts")
async def get_dashboard_charts(
    days: int = Query(default=30, ge=1, le=365),
    channel: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get dashboard chart data."""
    start_date = datetime.now(timezone.utc) - timedelta(days=days)

    # Messages per day (outgoing)
    per_day_query = (
        select(
            func.date(Message.created_at).label("day"),
            func.count(Message.id).label("count"),
        )
        .where(
            Message.direction == "outgoing",
            Message.created_at >= start_date,
        )
    )
    per_day_query = _channel_filter(per_day_query, channel)
    msg_result = await db.execute(per_day_query.group_by("day").order_by("day"))
    messages_per_day = [{"day": str(r[0]), "count": r[1]} for r in msg_result]

    # Replies per day
    reply_query = (
        select(
            func.date(Message.created_at).label("day"),
            func.count(Message.id).label("count"),
        )
        .where(
            Message.direction == "incoming",
            Message.created_at >= start_date,
        )
    )
    reply_query = _channel_filter(reply_query, channel)
    reply_result = await db.execute(reply_query.group_by("day").order_by("day"))
    replies_per_day = [{"day": str(r[0]), "count": r[1]} for r in reply_result]

    return {
        "messages_per_day": messages_per_day,
        "replies_per_day": replies_per_day,
        "days": days,
    }
