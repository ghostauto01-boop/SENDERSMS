"""Calendar API — meetings, reminders and day/week/month scheduling.

Book from the Calendar page or straight from an inbox chat ("Book a meeting"):
invites and reminders go out as personalized SMS through the same
template/short-code engine as campaigns, and booking a contact moves them into
the ``meeting`` pipeline stage so the whole app stays in sync.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.meeting import MEETING_STATUSES, MEETING_TYPES, Meeting
from app.models.user import User
from app.security.auth import get_current_user
from app.services.meeting_service import (
    DEFAULT_INVITE_BODY,
    DEFAULT_REMINDER_BODY,
    _contacts_map,
    ensure_conversation,
    refresh_meeting,
    render_meeting_message,
    resolve_message_body,
    send_invite,
    send_reminder,
    serialize_meeting,
)

router = APIRouter()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #

class MeetingCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    event_type: str = "meeting"
    starts_at: datetime
    ends_at: Optional[datetime] = None
    duration_minutes: int = Field(default=30, ge=5, le=1440)
    all_day: bool = False
    location: Optional[str] = None
    meeting_link: Optional[str] = None
    contact_ids: list[int] = []
    contact_id: Optional[int] = None
    conversation_id: Optional[int] = None
    campaign_id: Optional[int] = None
    tags: list[str] = []
    send_invite_sms: bool = True
    invite_template_id: Optional[int] = None
    invite_body: Optional[str] = None
    send_sms_reminder: bool = True
    reminder_minutes: list[int] = [60, 15]
    reminder_template_id: Optional[int] = None
    reminder_body: Optional[str] = None
    outcome_notes: Optional[str] = None


class MeetingUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    event_type: Optional[str] = None
    status: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    duration_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    all_day: Optional[bool] = None
    location: Optional[str] = None
    meeting_link: Optional[str] = None
    contact_ids: Optional[list[int]] = None
    conversation_id: Optional[int] = None
    campaign_id: Optional[int] = None
    tags: Optional[list[str]] = None
    send_invite_sms: Optional[bool] = None
    invite_template_id: Optional[int] = None
    invite_body: Optional[str] = None
    send_sms_reminder: Optional[bool] = None
    reminder_minutes: Optional[list[int]] = None
    reminder_template_id: Optional[int] = None
    reminder_body: Optional[str] = None
    outcome_notes: Optional[str] = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _clean_tags(tags: list[str] | None) -> list[str]:
    out: list[str] = []
    for tag in tags or []:
        name = str(tag or "").strip()
        if name and name not in out and len(name) <= 100:
            out.append(name)
    return out


def _clean_minutes(minutes: list[int] | None) -> list[int]:
    out: list[int] = []
    for item in minutes or []:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= value <= 10080 and value not in out:  # up to 7 days out
            out.append(value)
    return sorted(out, reverse=True)


def _require_utc(value: datetime) -> datetime:
    # Browsers send local time with an offset; naive input is assumed UTC.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _get_meeting(db: AsyncSession, meeting_id: int) -> Meeting:
    meeting = (await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


def _parse_iso(raw: Optional[str]) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #

@router.get("/")
async def list_meetings(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=100, ge=1, le=500),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    contact_id: Optional[int] = None,
    conversation_id: Optional[int] = None,
    status: Optional[str] = None,
    event_type: Optional[str] = None,
    tag: Optional[str] = None,
    search: Optional[str] = None,
    upcoming_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List meetings with calendar-friendly range / contact / status filters."""
    query = select(Meeting)

    start = _parse_iso(date_from)
    end = _parse_iso(date_to)
    if start:
        query = query.where(Meeting.ends_at >= start)
    if end:
        query = query.where(Meeting.starts_at <= end)
    if upcoming_only:
        query = query.where(
            Meeting.starts_at >= datetime.now(timezone.utc),
            Meeting.status.in_(("scheduled", "confirmed")),
        )
    if contact_id:
        from app.models.meeting import MeetingAttendee
        query = query.join(MeetingAttendee, MeetingAttendee.meeting_id == Meeting.id).where(
            or_(Meeting.contact_id == contact_id, MeetingAttendee.contact_id == contact_id)
        )
    if conversation_id:
        query = query.where(Meeting.conversation_id == conversation_id)
    if status and status != "all":
        query = query.where(Meeting.status == status)
    if event_type and event_type != "all":
        query = query.where(Meeting.event_type == event_type)
    if tag:
        # Tags are a JSON list column; match the quoted name to avoid
        # substring collisions ("vip" matching "vip-gold").
        query = query.where(Meeting.tags_json.ilike(f'%"{tag}"%'))
    if search:
        term = f"%{search}%"
        query = query.where(or_(
            Meeting.title.ilike(term),
            Meeting.description.ilike(term),
            Meeting.location.ilike(term),
        ))

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Meeting.starts_at.asc()).offset((page - 1) * per_page).limit(per_page)
    meetings = (await db.execute(query)).scalars().all()

    contact_ids: set[int] = set()
    for meeting in meetings:
        if meeting.contact_id:
            contact_ids.add(meeting.contact_id)
        for link in meeting.attendees or []:
            contact_ids.add(link.contact_id)
    contacts = await _contacts_map(db, contact_ids)

    return {
        "total": total,
        "items": [serialize_meeting(m, contacts) for m in meetings],
    }


@router.get("/upcoming")
async def upcoming_meetings(
    limit: int = Query(default=5, ge=1, le=50),
    contact_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Next few meetings — powers the dashboard widget and the inbox panel."""
    params: dict = {"page": 1, "per_page": limit, "upcoming_only": True}
    if contact_id:
        params["contact_id"] = contact_id
    return await list_meetings(db=db, current_user=current_user, **params)  # type: ignore[arg-type]


@router.get("/day")
async def day_agenda(
    date: str = Query(..., description="Calendar day as YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Everything on one calendar day, plus a count for the badge."""
    try:
        day = datetime.strptime(date.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    from datetime import timedelta
    start = day
    end = day + timedelta(days=1)
    result = await list_meetings(
        page=1, per_page=200,
        date_from=start.isoformat(), date_to=end.isoformat(),
        db=db, current_user=current_user,
    )
    return {"date": date.strip(), "count": result["total"], "items": result["items"]}


@router.get("/tags")
async def meeting_tags(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every tag used on meetings, with usage counts for the filter chips."""
    rows = (await db.execute(
        select(Meeting.tags_json).where(Meeting.tags_json.isnot(None))
    )).scalars().all()
    counts: dict[str, int] = {}
    for raw in rows:
        try:
            values = json.loads(raw or "[]")
        except (ValueError, TypeError):
            continue
        if isinstance(values, list):
            for name in values:
                label = str(name or "").strip()
                if label:
                    counts[label] = counts.get(label, 0) + 1
    return {
        "items": [
            {"name": name, "count": count}
            for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
        ]
    }


@router.get("/{meeting_id}")
async def get_meeting(
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    meeting = await _get_meeting(db, meeting_id)
    contact_ids = {link.contact_id for link in meeting.attendees or []}
    if meeting.contact_id:
        contact_ids.add(meeting.contact_id)
    contacts = await _contacts_map(db, contact_ids)
    return serialize_meeting(meeting, contacts)


@router.get("/{meeting_id}/preview")
async def preview_meeting_message(
    meeting_id: int,
    kind: str = Query(default="invite", description="invite or reminder"),
    contact_id: Optional[int] = None,
    template_id: Optional[int] = None,
    body: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Render the invite/reminder exactly as an attendee would receive it."""
    from app.utils.phone import count_sms_segments

    meeting = await _get_meeting(db, meeting_id)
    if kind not in ("invite", "reminder"):
        raise HTTPException(status_code=422, detail="kind must be invite or reminder")

    base_body, _ = await resolve_message_body(
        db,
        template_id=template_id if template_id is not None else (
            meeting.invite_template_id if kind == "invite" else meeting.reminder_template_id
        ),
        custom_body=body if body is not None else (
            meeting.invite_body if kind == "invite" else meeting.reminder_body
        ),
        fallback=DEFAULT_INVITE_BODY if kind == "invite" else DEFAULT_REMINDER_BODY,
    )

    contact = None
    target_id = contact_id or meeting.contact_id
    if target_id:
        contact = (await db.execute(
            select(Contact).where(Contact.id == target_id)
        )).scalar_one_or_none()
    text = await render_meeting_message(db, base_body, contact, meeting, extra={"meeting_when": "soon"})
    char_count, segment_count = count_sms_segments(text)
    return {
        "kind": kind, "body": text,
        "char_count": char_count, "segment_count": segment_count,
    }


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #

@router.post("/", status_code=201)
async def create_meeting(
    data: MeetingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.meeting_service import _sync_attendees, _touch_contact_for_booking

    if data.event_type not in MEETING_TYPES:
        raise HTTPException(status_code=422, detail=f"event_type must be one of {', '.join(MEETING_TYPES)}")

    starts_at = _require_utc(data.starts_at)
    ends_at = _require_utc(data.ends_at) if data.ends_at else None
    if ends_at is None:
        from datetime import timedelta
        ends_at = starts_at + timedelta(minutes=data.duration_minutes)
    if ends_at <= starts_at:
        raise HTTPException(status_code=422, detail="ends_at must be after starts_at")

    contact_ids = list(data.contact_ids or [])
    if data.contact_id and data.contact_id not in contact_ids:
        contact_ids.insert(0, data.contact_id)

    conversation_id = data.conversation_id
    if conversation_id is not None:
        conv = (await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )).scalar_one_or_none()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conv.contact_id and conv.contact_id not in contact_ids:
            contact_ids.insert(0, conv.contact_id)

    # Booking from an inbox chat without an explicit conversation still links
    # the thread, so the meeting shows up in that chat's info panel.
    if conversation_id is None and contact_ids:
        first = (await db.execute(
            select(Contact).where(Contact.id == contact_ids[0])
        )).scalar_one_or_none()
        if first:
            conversation_id = (await ensure_conversation(db, first.id)).id

    meeting = Meeting(
        title=data.title.strip(),
        description=(data.description or "").strip() or None,
        event_type=data.event_type,
        status="scheduled",
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=bool(data.all_day),
        location=(data.location or "").strip() or None,
        meeting_link=(data.meeting_link or "").strip() or None,
        conversation_id=conversation_id,
        campaign_id=data.campaign_id,
        tags_json=json.dumps(_clean_tags(data.tags)) if data.tags else None,
        send_invite_sms=bool(data.send_invite_sms),
        invite_template_id=data.invite_template_id,
        invite_body=(data.invite_body or "").strip() or None,
        send_sms_reminder=bool(data.send_sms_reminder),
        reminder_minutes_json=json.dumps(_clean_minutes(data.reminder_minutes))
        if data.send_sms_reminder else None,
        reminder_template_id=data.reminder_template_id,
        reminder_body=(data.reminder_body or "").strip() or None,
        outcome_notes=(data.outcome_notes or "").strip() or None,
        created_by=current_user.username,
    )
    db.add(meeting)
    await db.flush()

    contacts = await _sync_attendees(db, meeting, contact_ids)
    for contact in contacts.values():
        await _touch_contact_for_booking(db, contact)
    await db.flush()
    meeting = await refresh_meeting(db, meeting.id)

    invite_result = {"sent": 0, "skipped": 0}
    if meeting.send_invite_sms and meeting.contact_id:
        try:
            invite_result = await send_invite(db, meeting)
        except Exception as exc:
            # The booking itself stands even if the gateway is down right now;
            # the invite can be re-sent from the calendar or the chat.
            invite_result = {"sent": 0, "skipped": 0, "error": str(exc)[:200]}

    await db.flush()
    meeting = await refresh_meeting(db, meeting.id)
    payload = serialize_meeting(meeting, contacts)
    payload["invite"] = invite_result
    return payload


@router.put("/{meeting_id}")
async def update_meeting(
    meeting_id: int,
    data: MeetingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.meeting_service import _sync_attendees, _touch_contact_for_booking

    meeting = await _get_meeting(db, meeting_id)
    patch = data.model_dump(exclude_unset=True)

    if patch.get("event_type") and patch["event_type"] not in MEETING_TYPES:
        raise HTTPException(status_code=422, detail=f"event_type must be one of {', '.join(MEETING_TYPES)}")
    if patch.get("status") and patch["status"] not in MEETING_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(MEETING_STATUSES)}")

    contact_ids = patch.pop("contact_ids", None)

    starts_at = _require_utc(patch["starts_at"]) if patch.get("starts_at") else meeting.starts_at
    ends_at = _require_utc(patch["ends_at"]) if patch.get("ends_at") else None
    if ends_at is None and patch.get("starts_at"):
        # Keep the original length when the start moves but no new end is given.
        ends_at = starts_at + (meeting.ends_at - meeting.starts_at)
    if ends_at is not None and ends_at <= starts_at:
        raise HTTPException(status_code=422, detail="ends_at must be after starts_at")

    scalar_fields = (
        "title", "description", "event_type", "status", "all_day", "location",
        "meeting_link", "conversation_id", "campaign_id", "send_invite_sms",
        "invite_template_id", "invite_body", "send_sms_reminder",
        "reminder_template_id", "reminder_body", "outcome_notes",
    )
    for field in scalar_fields:
        if field in patch:
            value = patch[field]
            if isinstance(value, str):
                value = value.strip() or None
                if field == "title" and not value:
                    raise HTTPException(status_code=422, detail="title cannot be empty")
            setattr(meeting, field, value)
    meeting.starts_at = starts_at
    if ends_at is not None:
        meeting.ends_at = ends_at
    if patch.get("duration_minutes") and not patch.get("ends_at") and not patch.get("starts_at"):
        from datetime import timedelta
        meeting.ends_at = meeting.starts_at + timedelta(minutes=patch["duration_minutes"])
    if "tags" in patch:
        cleaned = _clean_tags(patch["tags"] or [])
        meeting.tags_json = json.dumps(cleaned) if cleaned else None
    if "reminder_minutes" in patch:
        cleaned = _clean_minutes(patch["reminder_minutes"] or [])
        meeting.reminder_minutes_json = json.dumps(cleaned) if cleaned else None

    # A rescheduled meeting gets its reminders back: windows that haven't
    # opened yet under the new time fire again.
    if patch.get("starts_at"):
        meeting.reminders_sent_json = None
        meeting.invite_sent = False

    contacts: dict[int, Contact] = {}
    if contact_ids is not None:
        contacts = await _sync_attendees(db, meeting, contact_ids)
        for contact in contacts.values():
            await _touch_contact_for_booking(db, contact)
    await db.flush()
    meeting = await refresh_meeting(db, meeting.id)
    if not contacts:
        wanted = {link.contact_id for link in meeting.attendees or []}
        if meeting.contact_id:
            wanted.add(meeting.contact_id)
        contacts = await _contacts_map(db, wanted)

    return serialize_meeting(meeting, contacts)


@router.delete("/{meeting_id}", status_code=204)
async def delete_meeting(
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    meeting = await _get_meeting(db, meeting_id)
    await db.delete(meeting)
    await db.flush()


async def _set_status(
    db: AsyncSession, meeting_id: int, status: str, outcome_notes: Optional[str] = None
) -> dict:
    meeting = await _get_meeting(db, meeting_id)
    meeting.status = status
    if outcome_notes is not None:
        meeting.outcome_notes = outcome_notes.strip() or None
    # A finished meeting graduates the attendees; a cancelled one leaves the
    # pipeline alone.
    if status == "completed" and meeting.contact_id:
        contact = (await db.execute(
            select(Contact).where(Contact.id == meeting.contact_id)
        )).scalar_one_or_none()
        if contact and (contact.lead_status or "") in ("new", "contacted", "replied", "meeting"):
            contact.lead_status = "contacted"
    await db.flush()
    wanted = {link.contact_id for link in meeting.attendees or []}
    if meeting.contact_id:
        wanted.add(meeting.contact_id)
    return serialize_meeting(meeting, await _contacts_map(db, wanted))


@router.post("/{meeting_id}/confirm")
async def confirm_meeting(
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _set_status(db, meeting_id, "confirmed")


@router.post("/{meeting_id}/complete")
async def complete_meeting(
    meeting_id: int,
    outcome_notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _set_status(db, meeting_id, "completed", outcome_notes)


@router.post("/{meeting_id}/cancel")
async def cancel_meeting(
    meeting_id: int,
    outcome_notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _set_status(db, meeting_id, "cancelled", outcome_notes)


@router.post("/{meeting_id}/no-show")
async def no_show_meeting(
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _set_status(db, meeting_id, "no_show")


@router.post("/{meeting_id}/send-invite")
async def resend_invite(
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """(Re)send the personalized SMS invite to every attendee."""
    meeting = await _get_meeting(db, meeting_id)
    if meeting.status in ("cancelled", "completed"):
        raise HTTPException(status_code=409, detail=f"Cannot invite to a {meeting.status} meeting")
    result = await send_invite(db, meeting)
    return {"success": True, **result}


@router.post("/{meeting_id}/send-reminder")
async def send_reminder_now(
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send the reminder text to every attendee right now."""
    meeting = await _get_meeting(db, meeting_id)
    if meeting.status in ("cancelled", "completed", "no_show"):
        raise HTTPException(status_code=409, detail=f"Cannot remind a {meeting.status} meeting")
    result = await send_reminder(db, meeting)
    return {"success": True, **result}


@router.get("/tags/all")
async def all_tags_alias(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Alias kept for older clients; same payload as GET /calendar/tags."""
    return await meeting_tags(db, current_user)
