"""
Follow-ups API routes.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.followup import FollowUp
from app.models.contact import Contact
from app.models.user import User
from app.security.auth import get_current_user
from app.utils.naming import contact_display_name

router = APIRouter()


@router.get("/")
async def list_followups(
    view: str = Query(default="due_today"),  # due_today, overdue, upcoming, completed, skipped
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List follow-ups based on view."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    query = select(FollowUp)

    if view == "due_today":
        query = query.where(
            FollowUp.status == "pending",
            FollowUp.scheduled_at >= today_start,
            FollowUp.scheduled_at < today_end,
        )
    elif view == "overdue":
        query = query.where(
            FollowUp.status == "pending",
            FollowUp.scheduled_at < today_start,
        )
    elif view == "upcoming":
        query = query.where(
            FollowUp.status == "pending",
            FollowUp.scheduled_at >= today_end,
        )
    elif view == "completed":
        query = query.where(FollowUp.status.in_(["sent", "delivered"]))
    elif view == "skipped":
        query = query.where(FollowUp.status.in_(["skipped", "cancelled", "failed"]))

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(FollowUp.scheduled_at.asc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    followups = result.scalars().all()

    items = []
    for f in followups:
        contact_result = await db.execute(select(Contact).where(Contact.id == f.contact_id))
        contact = contact_result.scalar_one_or_none()
        items.append({
            "id": f.id,
            "contact_id": f.contact_id,
            "contact_name": (
                contact_display_name(contact, contact.phone_number)
            ) if contact else "Unknown",
            "campaign_id": f.campaign_id,
            "sequence_step_order": f.sequence_step_order,
            "status": f.status,
            "scheduled_at": f.scheduled_at.isoformat() if f.scheduled_at else None,
            "executed_at": f.executed_at.isoformat() if f.executed_at else None,
            "message_text": f.message_text[:100] if f.message_text else None,
            "attempt_count": f.attempt_count,
        })

    return {"total": total, "items": items}


@router.post("/{followup_id}/send-now")
async def send_followup_now(
    followup_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send a follow-up immediately."""
    result = await db.execute(select(FollowUp).where(FollowUp.id == followup_id))
    followup = result.scalar_one_or_none()
    if not followup:
        raise HTTPException(status_code=404, detail="Follow-up not found")

    from app.tasks.campaign_tasks import process_followup
    from app.tasks.queue import QueueUnavailable, enqueue

    try:
        enqueue(process_followup, followup_id)
    except QueueUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {"success": True, "message": "Follow-up queued for sending"}


@router.post("/{followup_id}/skip")
async def skip_followup(
    followup_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Skip a follow-up."""
    result = await db.execute(select(FollowUp).where(FollowUp.id == followup_id))
    followup = result.scalar_one_or_none()
    if not followup:
        raise HTTPException(status_code=404, detail="Follow-up not found")

    followup.status = "skipped"
    await db.flush()
    return {"success": True}


@router.post("/{followup_id}/reschedule")
async def reschedule_followup(
    followup_id: int,
    new_time: str,  # ISO datetime
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reschedule a follow-up."""
    result = await db.execute(select(FollowUp).where(FollowUp.id == followup_id))
    followup = result.scalar_one_or_none()
    if not followup:
        raise HTTPException(status_code=404, detail="Follow-up not found")

    try:
        new_dt = datetime.fromisoformat(new_time.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid datetime format")

    followup.scheduled_at = new_dt
    followup.status = "pending"
    await db.flush()
    return {"success": True, "scheduled_at": followup.scheduled_at.isoformat()}
