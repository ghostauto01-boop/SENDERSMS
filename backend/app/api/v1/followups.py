"""Follow-ups API routes."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.followup import FollowUp
from app.models.campaign import CampaignContact
from app.models.contact import Contact
from app.models.user import User
from app.schemas.followup import FollowUpCreate
from app.security.auth import get_current_user
from app.utils.datetime import local_day_utc_bounds
from app.utils.naming import contact_display_name

router = APIRouter()


def _utc_iso(value: datetime | None) -> str | None:
    """Serialize database timestamps consistently, including on SQLite."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _followup_item(followup: FollowUp, contact: Contact | None) -> dict:
    """Return the stable shape consumed by the follow-ups page."""
    return {
        "id": followup.id,
        "contact_id": followup.contact_id,
        "contact_name": (
            contact_display_name(contact, contact.phone_number)
            if contact
            else "Unknown"
        ),
        "contact_phone": contact.phone_number if contact else None,
        "campaign_id": followup.campaign_id,
        "sequence_id": followup.sequence_id,
        "sequence_step_order": followup.sequence_step_order,
        "status": followup.status,
        "scheduled_at": _utc_iso(followup.scheduled_at),
        "executed_at": _utc_iso(followup.executed_at),
        "message_text": followup.message_text,
        #: "sms" (default) or "email" — an email follow-up also carries a subject.
        "channel": followup.channel or "sms",
        "subject": followup.subject,
        "email_account_id": followup.email_account_id,
        "contact_email": contact.email if contact else None,
        "attempt_count": followup.attempt_count,
        "max_attempts": followup.max_attempts,
        "notify_on_due": bool(followup.notify_on_due),
        "last_error": followup.last_error,
    }


@router.get("/")
async def list_followups(
    view: str = Query(
        default="due_today",
        description="due_today (the page's default), overdue, upcoming, pending (every follow-up "
                    "not yet sent, whatever day it is due), completed, skipped, all"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    channel: Optional[str] = Query(default="sms", description="sms | email | all"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List follow-ups based on view, for one channel (defaults to SMS).

    The defaults are the Follow-ups page's: today's SMS follow-ups. Anything that wants "my
    follow-ups" -- the assistant tool does -- asks for ``view=pending&channel=all``; a
    follow-up scheduled for next week, or an email one, is not in the default view.
    """
    from app.utils.channels import validate_channel

    today_start, today_end = local_day_utc_bounds()

    query = select(FollowUp)
    wanted = validate_channel(channel, default="sms")
    if wanted != "all":
        # Pre-channel rows are SMS, so they stay visible on the SMS side.
        query = query.where(func.coalesce(FollowUp.channel, "sms") == wanted)

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
    elif view == "pending":
        query = query.where(FollowUp.status == "pending")
    elif view == "completed":
        query = query.where(FollowUp.status.in_(["sent", "delivered"]))
    elif view == "skipped":
        query = query.where(FollowUp.status.in_(["skipped", "cancelled", "failed"]))
    elif view != "all":
        raise HTTPException(
            status_code=422,
            detail="view must be one of due_today, overdue, upcoming, pending, completed, skipped, all",
        )

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(FollowUp.scheduled_at.asc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    followups = result.scalars().all()

    items = []
    for followup in followups:
        contact_result = await db.execute(select(Contact).where(Contact.id == followup.contact_id))
        contact = contact_result.scalar_one_or_none()
        items.append(_followup_item(followup, contact))

    return {
        "total": total, "items": items, "page": page, "per_page": per_page,
        "next_page": page + 1 if page * per_page < total else None,
    }


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_followup(
    data: FollowUpCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a manual follow-up SMS for a contact."""
    contact_result = await db.execute(select(Contact).where(Contact.id == data.contact_id))
    contact = contact_result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    channel = (data.channel or "sms").lower()
    if channel not in ("sms", "email"):
        raise HTTPException(status_code=422, detail="channel must be 'sms' or 'email'")
    email_sender_id = data.email_account_id
    if channel == "email":
        if contact.is_email_opted_out:
            raise HTTPException(status_code=400, detail="Contact has opted out of email")
        if not (contact.email or "").strip():
            raise HTTPException(status_code=400, detail="Contact has no email address")
        if not (data.subject or "").strip():
            raise HTTPException(status_code=422, detail="An email follow-up needs a subject")
        from app.services import email_service

        sender = (
            await email_service.get_sender(db, data.email_account_id)
            if data.email_account_id is not None
            else await email_service.get_default_sender(db)
        )
        if data.email_account_id is not None and sender is None:
            raise HTTPException(status_code=404, detail="Selected email sender not found")
        # A follow-up can be prepared before a provider is connected. Keep the
        # sender unset so delivery can resolve the current default when it runs.
        if sender is not None:
            email_sender_id = email_service.sender_id(sender)
    elif contact.is_opted_out:
        raise HTTPException(status_code=400, detail="Cannot create a follow-up for an opted-out contact")

    scheduled_at = data.scheduled_at.astimezone(timezone.utc)
    if scheduled_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Follow-up time must be in the future")

    followup = FollowUp(
        contact_id=contact.id,
        status="pending",
        scheduled_at=scheduled_at,
        message_text=data.message_text,
        channel=channel,
        subject=(data.subject or "").strip() or None,
        email_account_id=email_sender_id,
        notify_on_due=data.notify_on_due,
        max_attempts=data.max_attempts,
    )
    db.add(followup)
    await db.flush()
    await db.refresh(followup)
    return _followup_item(followup, contact)


@router.delete("/{followup_id}", status_code=204)
async def delete_followup(
    followup_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a follow-up that has not gone out.

    A follow-up that was sent stays in the history (409). Removing one that belongs to a
    campaign ends that contact's automation, exactly as skipping it does -- the campaign must not
    keep waiting on a follow-up that no longer exists.
    """
    followup = (await db.execute(select(FollowUp).where(FollowUp.id == followup_id))).scalar_one_or_none()
    if not followup:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    if followup.status in ("sent", "delivered"):
        raise HTTPException(
            status_code=409,
            detail=f"This follow-up was already {followup.status}; it stays in the history.",
        )
    if followup.status == "sending":
        raise HTTPException(status_code=409, detail="This follow-up is being sent right now.")
    if followup.campaign_contact_id:
        campaign_contact = (
            await db.execute(
                select(CampaignContact).where(CampaignContact.id == followup.campaign_contact_id)
            )
        ).scalar_one_or_none()
        if campaign_contact:
            campaign_contact.status = "completed"
            campaign_contact.next_action_at = None
    await db.delete(followup)
    await db.flush()


@router.post("/{followup_id}/send-now")
async def send_followup_now(
    followup_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send a pending follow-up immediately."""
    result = await db.execute(select(FollowUp).where(FollowUp.id == followup_id))
    followup = result.scalar_one_or_none()
    if not followup:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    if followup.status != "pending":
        raise HTTPException(status_code=409, detail="Only pending follow-ups can be sent")

    from app.tasks.campaign_tasks import process_followup
    from app.tasks.queue import QueueUnavailable, enqueue

    try:
        enqueue(process_followup, followup_id)
    except QueueUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"success": True, "message": "Follow-up queued for sending"}


@router.post("/{followup_id}/skip")
async def skip_followup(
    followup_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Skip a pending follow-up."""
    result = await db.execute(select(FollowUp).where(FollowUp.id == followup_id))
    followup = result.scalar_one_or_none()
    if not followup:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    if followup.status != "pending":
        raise HTTPException(status_code=409, detail="Only pending follow-ups can be skipped")

    followup.status = "skipped"
    if followup.campaign_contact_id:
        campaign_contact = (
            await db.execute(
                select(CampaignContact).where(
                    CampaignContact.id == followup.campaign_contact_id
                )
            )
        ).scalar_one_or_none()
        if campaign_contact:
            # Skipping the scheduled continuation ends automation for this
            # contact; leaving it queued would keep the campaign running forever.
            campaign_contact.status = "completed"
            campaign_contact.next_action_at = None
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
        if new_dt.tzinfo is None or new_dt.utcoffset() is None:
            raise ValueError
        new_dt = new_dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid datetime format")
    if new_dt <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Follow-up time must be in the future")

    followup.scheduled_at = new_dt
    followup.status = "pending"
    followup.last_error = None
    if followup.campaign_contact_id:
        campaign_contact = (
            await db.execute(
                select(CampaignContact).where(
                    CampaignContact.id == followup.campaign_contact_id
                )
            )
        ).scalar_one_or_none()
        if campaign_contact:
            campaign_contact.status = "queued"
            campaign_contact.next_action_at = new_dt
    await db.flush()
    return {"success": True, "scheduled_at": _utc_iso(followup.scheduled_at)}
