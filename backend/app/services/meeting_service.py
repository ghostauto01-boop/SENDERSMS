"""Meeting / calendar service — booking, SMS invites and reminders.

Everything is rendered through the same template + short-code engine as
campaigns and the inbox, so a reminder can use ``{{first_name}}`` as well as
the meeting placeholders (``{{meeting_date}}``, ``{{meeting_time}}`` ...).

The reminder sweep (``process_due_meeting_reminders``) is called from the
inline poller in ``app.main`` so reminders fire even when no Celery worker is
awake. Each (meeting, minutes_before) pair is recorded in
``reminders_sent_json`` so it fires exactly once.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.meeting import Meeting, MeetingAttendee
from app.models.template import Template
from app.utils.naming import contact_display_name

logger = logging.getLogger(__name__)


DEFAULT_INVITE_BODY = (
    "Hi {{first_name}}, you're booked in: {{meeting_title}} on {{meeting_date}} "
    "at {{meeting_time}} {{meeting_location}}Reply YES to confirm."
)

DEFAULT_REMINDER_BODY = (
    "Hi {{first_name}}, reminder: {{meeting_title}} {{meeting_when}} "
    "({{meeting_date}} at {{meeting_time}}) {{meeting_location}}"
)

# Lead statuses that a meeting booking must never overwrite: a customer stays a
# customer, and a closed/not-interested contact stays that way.
_PROTECTED_LEAD_STATUSES = {"customer", "closed", "not_interested"}


def _parse_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except (ValueError, TypeError):
        return []


def _parse_json_dict(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def meeting_tags(meeting: Meeting) -> list[str]:
    return [str(t) for t in _parse_json_list(meeting.tags_json) if str(t).strip()]


def reminder_minutes(meeting: Meeting) -> list[int]:
    out: list[int] = []
    for item in _parse_json_list(meeting.reminder_minutes_json):
        try:
            minutes = int(item)
        except (TypeError, ValueError):
            continue
        if minutes > 0 and minutes not in out:
            out.append(minutes)
    return sorted(out, reverse=True)


def reminders_sent(meeting: Meeting) -> dict[str, str]:
    return {str(k): str(v) for k, v in _parse_json_dict(meeting.reminders_sent_json).items()}


def meeting_placeholders(meeting: Meeting) -> dict[str, str]:
    """Extra ``{{meeting_*}}`` values merged into the render context."""
    starts = _as_utc(meeting.starts_at)
    return {
        "meeting_title": meeting.title or "",
        "meeting_date": starts.strftime("%a, %d %b %Y"),
        "meeting_time": starts.strftime("%I:%M %p").lstrip("0"),
        "meeting_datetime": starts.strftime("%a, %d %b %Y at %I:%M %p").replace(" 0", " "),
        "meeting_location": f"at {meeting.location}." if meeting.location else "",
        "meeting_link": meeting.meeting_link or "",
    }


async def render_meeting_message(
    db: AsyncSession,
    body: str,
    contact: Contact | None,
    meeting: Meeting,
    extra: dict[str, str] | None = None,
) -> str:
    """Render invite/reminder text with contact + meeting placeholders."""
    from app.services.variable_service import variable_maps
    from app.utils.templating import render_template

    aliases, fallbacks = await variable_maps(db)
    values = meeting_placeholders(meeting)
    values.setdefault("meeting_when", "soon")
    if extra:
        values.update(extra)
    return render_template(body, contact, aliases=aliases, fallbacks=fallbacks, **values)


async def resolve_message_body(
    db: AsyncSession, *, template_id: int | None, custom_body: str | None, fallback: str
) -> tuple[str, Template | None]:
    """Pick the message text: custom body wins, then template, then default."""
    from app.services.template_service import get_active_template
    template = await get_active_template(db, template_id)
    if custom_body and custom_body.strip():
        return custom_body, template
    if template is not None:
        return template.body, template
    return fallback, None


def serialize_meeting(meeting: Meeting, contacts_by_id: dict[int, Contact] | None = None) -> dict:
    attendees: list[dict] = []
    for link in meeting.attendees or []:
        contact = (contacts_by_id or {}).get(link.contact_id)
        attendees.append({
            "id": link.id,
            "contact_id": link.contact_id,
            "status": link.status,
            "notified": bool(link.notified),
            "name": contact_display_name(contact) if contact else f"Contact #{link.contact_id}",
            "phone": contact.phone_number if contact else "",
        })
    primary = (contacts_by_id or {}).get(meeting.contact_id or 0)
    return {
        "id": meeting.id,
        "title": meeting.title,
        "description": meeting.description,
        "event_type": meeting.event_type,
        "status": meeting.status,
        "starts_at": meeting.starts_at.isoformat(),
        "ends_at": meeting.ends_at.isoformat(),
        "all_day": bool(meeting.all_day),
        "location": meeting.location,
        "meeting_link": meeting.meeting_link,
        "contact_id": meeting.contact_id,
        "contact_name": contact_display_name(primary) if primary else None,
        "conversation_id": meeting.conversation_id,
        "campaign_id": meeting.campaign_id,
        "tags": meeting_tags(meeting),
        "send_invite_sms": bool(meeting.send_invite_sms),
        "invite_template_id": meeting.invite_template_id,
        "invite_body": meeting.invite_body,
        "invite_sent": bool(meeting.invite_sent),
        "send_sms_reminder": bool(meeting.send_sms_reminder),
        "reminder_minutes": reminder_minutes(meeting),
        "reminder_template_id": meeting.reminder_template_id,
        "reminder_body": meeting.reminder_body,
        "reminders_sent": reminders_sent(meeting),
        "outcome_notes": meeting.outcome_notes,
        "attendees": attendees,
        "created_at": meeting.created_at.isoformat(),
        "updated_at": meeting.updated_at.isoformat(),
    }


async def _contacts_map(db: AsyncSession, contact_ids: set[int]) -> dict[int, Contact]:
    if not contact_ids:
        return {}
    rows = (await db.execute(
        select(Contact).where(Contact.id.in_(contact_ids))
    )).scalars().all()
    return {c.id: c for c in rows}


async def _meeting_attendees(db: AsyncSession, meeting_id: int) -> list[MeetingAttendee]:
    """Attendee rows via an explicit query — never an implicit lazy load.

    Touching ``meeting.attendees`` on a just-flushed object triggers a lazy
    SELECT outside of greenlet context (MissingGreenlet on async sessions),
    so every write path goes through this instead.
    """
    return list((await db.execute(
        select(MeetingAttendee).where(MeetingAttendee.meeting_id == meeting_id)
    )).scalars().all())


async def _sync_attendees(db: AsyncSession, meeting: Meeting, contact_ids: list[int]) -> dict[int, Contact]:
    """Replace the attendee list with exactly these contacts (deduped, valid)."""
    wanted: list[int] = []
    for cid in contact_ids or []:
        try:
            cid_int = int(cid)
        except (TypeError, ValueError):
            continue
        if cid_int not in wanted:
            wanted.append(cid_int)
    contacts = await _contacts_map(db, set(wanted))
    valid = [cid for cid in wanted if cid in contacts]

    existing = {link.contact_id: link for link in await _meeting_attendees(db, meeting.id)}
    for cid in valid:
        if cid not in existing:
            db.add(MeetingAttendee(meeting_id=meeting.id, contact_id=cid))
    for cid, link in existing.items():
        if cid not in valid:
            await db.delete(link)
    await db.flush()

    meeting.contact_id = valid[0] if valid else None
    return contacts


async def refresh_meeting(db: AsyncSession, meeting_id: int) -> Meeting:
    """Re-select a meeting so ``attendees`` is eagerly loaded for serialize."""
    return (await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )).scalar_one()


async def _touch_contact_for_booking(db: AsyncSession, contact: Contact) -> None:
    """Move a booked contact into the meeting stage (unless protected)."""
    if contact and (contact.lead_status or "new") not in _PROTECTED_LEAD_STATUSES:
        contact.lead_status = "meeting"


async def send_invite(
    db: AsyncSession, meeting: Meeting, *, only_contact_ids: set[int] | None = None
) -> dict:
    """Send the personalized SMS invite to every attendee (once each)."""
    from app.services.sms_service import SMSService

    body, template = await resolve_message_body(
        db,
        template_id=meeting.invite_template_id,
        custom_body=meeting.invite_body,
        fallback=DEFAULT_INVITE_BODY,
    )
    links = await _meeting_attendees(db, meeting.id)
    contacts = await _contacts_map(db, {link.contact_id for link in links})
    svc = SMSService(db)
    sent = 0
    skipped = 0
    for link in links:
        if only_contact_ids is not None and link.contact_id not in only_contact_ids:
            continue
        contact = contacts.get(link.contact_id)
        if contact is None:
            skipped += 1
            continue
        text = await render_meeting_message(db, body, contact, meeting)
        msg = await svc.send_message(contact_id=contact.id, body=text)
        if msg is None:
            skipped += 1
            continue
        link.notified = True
        sent += 1
    if template is not None and sent:
        template.use_count = (template.use_count or 0) + 1
    if sent:
        meeting.invite_sent = True
    await db.flush()
    return {"sent": sent, "skipped": skipped}


async def send_reminder(
    db: AsyncSession, meeting: Meeting, *, minutes_before: int | None = None
) -> dict:
    """Send the reminder text now to every attendee."""
    from app.services.sms_service import SMSService

    body, template = await resolve_message_body(
        db,
        template_id=meeting.reminder_template_id,
        custom_body=meeting.reminder_body,
        fallback=DEFAULT_REMINDER_BODY,
    )
    links = await _meeting_attendees(db, meeting.id)
    contacts = await _contacts_map(db, {link.contact_id for link in links})
    svc = SMSService(db)
    sent = 0
    skipped = 0
    for link in links:
        contact = contacts.get(link.contact_id)
        if contact is None:
            skipped += 1
            continue
        # {{meeting_when}} reads naturally in reminders ("in 1 hour"...).
        extra = {"meeting_when": _human_window(minutes_before) if minutes_before else "soon"}
        text = await render_meeting_message(db, body, contact, meeting, extra=extra)
        msg = await svc.send_message(contact_id=contact.id, body=text)
        if msg is None:
            skipped += 1
        else:
            sent += 1
    if template is not None and sent:
        template.use_count = (template.use_count or 0) + 1
    if minutes_before:
        done = reminders_sent(meeting)
        done[str(minutes_before)] = datetime.now(timezone.utc).isoformat()
        meeting.reminders_sent_json = json.dumps(done)
    await db.flush()
    return {"sent": sent, "skipped": skipped}


def _human_window(minutes: int) -> str:
    if minutes >= 1440:
        days = round(minutes / 1440)
        return f"in {days} day{'s' if days != 1 else ''}"
    if minutes >= 60:
        hours = round(minutes / 60)
        return f"in {hours} hour{'s' if hours != 1 else ''}"
    return f"in {minutes} minute{'s' if minutes != 1 else ''}"


async def ensure_conversation(db: AsyncSession, contact_id: int) -> Conversation:
    conv = (await db.execute(
        select(Conversation).where(Conversation.contact_id == contact_id)
        .order_by(Conversation.id).limit(1)
    )).scalars().first()
    if conv is None:
        conv = Conversation(contact_id=contact_id, status="active")
        db.add(conv)
        await db.flush()
    return conv


async def process_due_meeting_reminders() -> dict:
    """Fire every reminder whose window has opened and hasn't been sent.

    A reminder for "N minutes before" is due once ``now >= starts_at - N``.
    Only scheduled/confirmed future meetings with SMS reminders enabled are
    considered, and each (meeting, N) pair fires exactly once.
    """
    from app.database import async_session_factory

    totals = {"checked": 0, "sent": 0, "skipped": 0}
    async with async_session_factory() as db:
        now = datetime.now(timezone.utc)
        rows = (await db.execute(
            select(Meeting).where(
                Meeting.status.in_(("scheduled", "confirmed")),
                # Integer-backed boolean: compare with 1, never `== True`
                # (PostgreSQL rejects `integer = boolean`; see template_service).
                Meeting.send_sms_reminder == 1,
                Meeting.starts_at > now,
            ).order_by(Meeting.starts_at.asc()).limit(100)
        )).scalars().all()
        for meeting in rows:
            minutes_list = reminder_minutes(meeting)
            if not minutes_list:
                continue
            totals["checked"] += 1
            done = reminders_sent(meeting)
            starts = _as_utc(meeting.starts_at)
            for minutes in minutes_list:
                if str(minutes) in done:
                    continue
                window_open = (starts - now).total_seconds() <= minutes * 60
                if not window_open:
                    continue
                try:
                    result = await send_reminder(db, meeting, minutes_before=minutes)
                    totals["sent"] += result["sent"]
                    totals["skipped"] += result["skipped"]
                except Exception as exc:
                    logger.warning("Meeting reminder %s (%s min) failed: %s", meeting.id, minutes, exc)
        await db.commit()
    return totals
