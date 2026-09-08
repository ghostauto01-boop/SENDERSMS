"""
Contacts API routes.
"""

import csv
import io
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, or_, delete as sa_delete, update as sa_update
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contact import Contact, Tag, ContactTag
from app.models.contact_list import ContactList
from app.models.user import User
from app.schemas.contact import ContactCreate, ContactUpdate, ContactOut, ContactListOut, BulkAction
from app.security.auth import get_current_user
from app.utils.phone import normalize_nigerian_number

router = APIRouter()

LEAD_STATUSES = [
    "new", "contacted", "replied", "interested",
    "follow-up", "meeting", "customer", "not_interested", "closed",
]


async def _tag_names(db: AsyncSession, contact_id: int) -> list[str]:
    """Tag names for one contact, ordered and stable for the UI/export."""
    rows = await db.execute(
        select(Tag.name)
        .join(ContactTag, ContactTag.tag_id == Tag.id)
        .where(ContactTag.contact_id == contact_id)
        .order_by(Tag.name.asc())
    )
    return list(rows.scalars().all())


async def _serialize_contact(db: AsyncSession, contact: Contact) -> dict:
    """Contact -> dict with tags attached, without an async lazy load."""
    data = {k: v for k, v in vars(contact).items() if not k.startswith("_")}
    data["tags"] = await _tag_names(db, contact.id)
    return ContactOut.model_validate(data).model_dump(mode="json")


def _apply_contact_filters(query, search: Optional[str], lead_status: Optional[str], tag: Optional[str]):
    """Apply the shared search / status / tag filters to a contact query."""
    if search:
        search_term = f"%{search}%"
        clauses = [
            Contact.first_name.ilike(search_term),
            Contact.last_name.ilike(search_term),
            Contact.business_name.ilike(search_term),
            Contact.phone_number.ilike(search_term),
            Contact.email.ilike(search_term),
            Contact.city.ilike(search_term),
        ]
        # Numbers are stored as +234..., but users type 0803... A literal LIKE
        # on the typed form matches nothing, so also try the equivalent
        # spellings of the same number.
        from app.utils.phone import phone_search_variants
        clauses += [
            Contact.phone_number.ilike(f"%{v}%") for v in phone_search_variants(search)
        ]
        query = query.where(or_(*clauses))

    if lead_status:
        query = query.where(Contact.lead_status == lead_status)

    if tag:
        query = query.join(ContactTag, Contact.id == ContactTag.contact_id).join(Tag, ContactTag.tag_id == Tag.id).where(Tag.name == tag)

    return query


@router.get("/", response_model=ContactListOut)
async def list_contacts(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    search: Optional[str] = None,
    lead_status: Optional[str] = None,
    tag: Optional[str] = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List contacts with pagination, search, filter, and sort."""
    query = _apply_contact_filters(select(Contact), search, lead_status, tag)

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Sort
    sort_col = getattr(Contact, sort_by, Contact.created_at)
    if sort_dir == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    # Paginate
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    contacts = result.scalars().all()

    items = [await _serialize_contact(db, c) for c in contacts]
    return ContactListOut(total=total, items=items)


@router.get("/tags/", response_model=dict)
async def list_tags(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every tag in use, with contact counts — powers tag pickers app-wide.

    Declared before ``/{contact_id}`` so the literal path wins over the
    parameter route.
    """
    rows = (
        await db.execute(
            select(Tag.name, func.count(ContactTag.id))
            .outerjoin(ContactTag, ContactTag.tag_id == Tag.id)
            .group_by(Tag.id, Tag.name)
            .order_by(Tag.name.asc())
        )
    ).all()
    return {
        "items": [{"name": name, "count": count} for name, count in rows],
    }


@router.get("/{contact_id}", response_model=ContactOut)
async def get_contact(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single contact."""
    result = await db.execute(select(Contact).where(Contact.id == contact_id))
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return await _serialize_contact(db, contact)


@router.post("/", response_model=ContactOut, status_code=201)
async def create_contact(
    data: ContactCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new contact."""
    # Normalize phone number
    normalized = normalize_nigerian_number(data.phone_number)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid Nigerian phone number")

    # Check for duplicates
    existing = await db.execute(select(Contact).where(Contact.phone_number == normalized))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Contact with this phone number already exists")

    contact = Contact(
        first_name=data.first_name,
        last_name=data.last_name,
        business_name=data.business_name,
        phone_number=normalized,
        email=data.email,
        city=data.city,
        state=data.state,
        country=data.country or "Nigeria",
        website=data.website,
        industry=data.industry,
        source=data.source,
        lead_status=data.lead_status,
        notes=data.notes,
        custom_fields=data.custom_fields,
    )
    db.add(contact)
    await db.flush()
    await db.refresh(contact)
    return await _serialize_contact(db, contact)


@router.put("/{contact_id}", response_model=ContactOut)
async def update_contact(
    contact_id: int,
    data: ContactUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a contact."""
    result = await db.execute(select(Contact).where(Contact.id == contact_id))
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(contact, key, value)
    contact.updated_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(contact)
    return await _serialize_contact(db, contact)


def _is_missing_table_error(exc: BaseException) -> bool:
    """True when a cleanup statement hit a table that is not in this DB.

    Tests create a minimal SQLite schema, and some optional tables (meetings,
    ads, campaign follow-up logs, ...) are only registered once their model
    module is imported. A permanent delete must still be able to run there; in
    production every table is created by ``init_db`` so nothing is skipped.
    """
    message = str(exc).lower()
    return (
        "no such table" in message
        or "does not exist" in message
        or "relation does not exist" in message
    )


async def _safe_delete(db: AsyncSession, statement):
    """Delete rows, tolerating a table that was never created in this DB."""
    try:
        await db.execute(statement)
    except (OperationalError, ProgrammingError) as exc:
        if _is_missing_table_error(exc):
            return
        raise


async def _safe_update(db: AsyncSession, statement):
    """Update rows, tolerating a table that was never created in this DB."""
    try:
        await db.execute(statement)
    except (OperationalError, ProgrammingError) as exc:
        if _is_missing_table_error(exc):
            return
        raise


async def _delete_contact_permanently(db: AsyncSession, contact: Contact) -> None:
    """Hard-delete a contact and every row that references it.

    The ORM only cascades ``Contact.tags`` and ``Contact.list_memberships``.
    Messages, conversations, follow-ups, campaign rows, scheduled messages,
    meetings, campaign follow-up logs and SMS Ads rows all hold a raw
    ``contacts.id`` value with no ORM relationship (or no ``ondelete``
    cascade), so on Postgres a bare ``db.delete(contact)`` raised a
    foreign-key violation and on SQLite it silently left orphan rows behind
    (broken inbox threads, stale campaign state, dangling calendar events).
    This removes/clears them all in dependency order so a delete is truly
    permanent.
    """
    from app.models.conversation import Conversation, Message
    from app.models.followup import FollowUp
    from app.models.campaign import CampaignContact
    from app.models.campaign_followup import CampaignFollowUpLog
    from app.models.scheduled import ScheduledMessage
    from app.models.suppression import SuppressionEntry
    from app.models.meeting import Meeting, MeetingAttendee
    from app.models.ads import AdsAssignment, AdsFollowUpTask, AdsCalendarEvent, AdsEvent

    contact_id = contact.id

    # Scheduled messages first: they can point at outgoing Message rows via
    # ``message_id`` as well as at the contact / list themselves.
    await _safe_delete(db, sa_delete(ScheduledMessage).where(ScheduledMessage.contact_id == contact_id))
    # Outgoing/incoming messages reference both the contact and its threads;
    # conversations are unique per contact.
    await _safe_delete(db, sa_delete(Message).where(Message.contact_id == contact_id))
    await _safe_delete(db, sa_delete(Conversation).where(Conversation.contact_id == contact_id))
    # Follow-up / campaign state.
    await _safe_delete(db, sa_delete(FollowUp).where(FollowUp.contact_id == contact_id))
    await _safe_delete(db, sa_delete(CampaignContact).where(CampaignContact.contact_id == contact_id))
    await _safe_delete(db, sa_delete(CampaignFollowUpLog).where(CampaignFollowUpLog.contact_id == contact_id))
    await _safe_delete(db, sa_delete(SuppressionEntry).where(SuppressionEntry.contact_id == contact_id))

    # Calendar / meetings: remove the attendee link and detach the primary
    # contact so the meeting record (and any other attendees) survives.
    await _safe_delete(db, sa_delete(MeetingAttendee).where(MeetingAttendee.contact_id == contact_id))
    await _safe_delete(
        db,
        sa_delete(Meeting).where(Meeting.contact_id == contact_id, Meeting.id.notin_(
            select(MeetingAttendee.meeting_id)
        )),
    )
    await _safe_update(
        db,
        sa_update(Meeting)
        .where(Meeting.contact_id == contact_id)
        .values(contact_id=None),
    )

    # SMS Ads Manager: assignments and follow-up tasks belong to this contact
    # and must go; calendar/analytics rows keep their history with the contact
    # detached (they have no FK, so this is what makes them safe to delete).
    await _safe_delete(db, sa_delete(AdsAssignment).where(AdsAssignment.contact_id == contact_id))
    await _safe_delete(db, sa_delete(AdsFollowUpTask).where(AdsFollowUpTask.contact_id == contact_id))
    await _safe_delete(db, sa_delete(AdsCalendarEvent).where(AdsCalendarEvent.contact_id == contact_id))
    await _safe_update(
        db,
        sa_update(AdsEvent)
        .where(AdsEvent.contact_id == contact_id)
        .values(contact_id=None),
    )

    # Tag links and list memberships are removed by the ORM's own
    # delete-orphan cascade when the contact row is deleted below.
    await db.delete(contact)
    await db.flush()


@router.delete("/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Permanently delete a contact and everything tied to it."""
    result = await db.execute(select(Contact).where(Contact.id == contact_id))
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    await _delete_contact_permanently(db, contact)


@router.post("/bulk")
async def bulk_action(
    data: BulkAction,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Perform bulk action on contacts."""
    result = await db.execute(select(Contact).where(Contact.id.in_(data.contact_ids)))
    contacts = result.scalars().all()

    if data.action == "delete":
        for c in contacts:
            await _delete_contact_permanently(db, c)
    elif data.action == "status":
        for c in contacts:
            c.lead_status = data.value or "new"
    elif data.action == "tag":
        for c in contacts:
            # Find or create tag
            tag_result = await db.execute(select(Tag).where(Tag.name == data.value))
            tag = tag_result.scalar_one_or_none()
            if not tag:
                tag = Tag(name=data.value)
                db.add(tag)
                await db.flush()

            # Add tag if not exists
            existing_ct = await db.execute(
                select(ContactTag).where(
                    ContactTag.contact_id == c.id,
                    ContactTag.tag_id == tag.id,
                )
            )
            if not existing_ct.scalar_one_or_none():
                ct = ContactTag(contact_id=c.id, tag_id=tag.id)
                db.add(ct)

    await db.flush()
    return {"success": True, "affected": len(contacts)}


@router.post("/import/csv")
async def import_csv(
    file: UploadFile = File(...),
    list_id: Optional[int] = Form(None),
    new_list_name: Optional[str] = Form(None),
    skip_duplicates: bool = Form(True),
    column_mapping: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Import contacts from CSV file, optionally into a contact list.

    ``list_id`` / ``skip_duplicates`` / ``column_mapping`` arrive as multipart
    form fields (the UI uploads the file and these together), so they must be
    declared with ``Form`` -- previously ``list_id`` was a query parameter and
    the browser's form field never reached it, so imports always landed with
    no list attached.

    ``new_list_name`` creates the destination list as part of the same upload,
    so an import never has to be interrupted to go and create a list first.
    """
    from app.services.csv_service import CSVImportService, detect_column_mapping

    content = await file.read()

    target_list: ContactList | None = None
    # Fail fast with a clean 404 if the target list does not exist, instead of
    # importing the contacts and silently dropping the list attachment.
    if list_id is not None:
        list_result = await db.execute(select(ContactList).where(ContactList.id == list_id))
        target_list = list_result.scalar_one_or_none()
        if not target_list:
            raise HTTPException(status_code=404, detail="List not found")
    elif new_list_name and new_list_name.strip():
        target_list = ContactList(name=new_list_name.strip()[:255])
        db.add(target_list)
        await db.flush()
        list_id = target_list.id

    # Auto-detect column mapping from the real CSV headers (case-insensitive).
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="No headers found in CSV")

    column_mapping_map = detect_column_mapping(list(reader.fieldnames))

    # Merge any mapping the user chose in the "map columns" step, so manual
    # overrides are respected on top of the auto-detection.
    if column_mapping:
        try:
            client_mapping = json.loads(column_mapping)
        except (ValueError, TypeError):
            client_mapping = {}
        for header, field in client_mapping.items():
            # An explicit blank/"ignore" means the user chose not to import
            # that column. Non-empty custom:<key> targets preserve arbitrary
            # CSV data in Contact.custom_fields.
            column_mapping_map[str(header).strip().lower()] = field or "ignore"

    from app.services.csv_service import split_tags
    tag_list = split_tags(tags) if tags else []

    service = CSVImportService(db)
    result = await service.validate_and_import(
        content, column_mapping_map, list_id, skip_duplicates, tags=tag_list
    )

    # Register every column this file introduced so it is immediately usable as
    # a {{short code}} and visible on the Variables page. Best-effort: a
    # registry hiccup must never fail an import that already stored contacts.
    new_variables = 0
    try:
        from app.services.variable_service import sync_variables_from_contacts

        sync = await sync_variables_from_contacts(db)
        new_variables = sync.get("discovered", 0)
    except Exception:
        pass

    return {
        "new_variables": new_variables,
        "imported": result.imported,
        "skipped": result.skipped,
        "invalid": result.invalid,
        "duplicates": result.duplicates,
        "total_rows": result.total_rows,
        "errors": result.errors[:50],  # Limit error report
        "imported_ids": result.imported_contact_ids,
        "list": (
            {
                "id": target_list.id,
                "name": target_list.name,
                "contact_count": target_list.contact_count,
            }
            if target_list
            else None
        ),
    }


@router.get("/export/csv")
async def export_csv(
    search: Optional[str] = None,
    lead_status: Optional[str] = None,
    tag: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export all matching contacts to a CSV file."""
    query = _apply_contact_filters(select(Contact), search, lead_status, tag)
    query = query.order_by(Contact.created_at.desc())

    result = await db.execute(query)
    contacts = result.scalars().all()

    columns = [
        "first_name", "last_name", "business_name", "phone_number", "email",
        "city", "state", "country", "website", "industry", "source", "lead_status",
        "notes",
    ]

    # Include the union of imported custom fields so an export/re-import is
    # lossless and users can inspect fields such as pain_point or account_tier.
    parsed_custom: list[dict] = []
    custom_columns: list[str] = []
    for contact in contacts:
        try:
            values = json.loads(contact.custom_fields or "{}")
            values = values if isinstance(values, dict) else {}
        except (ValueError, TypeError):
            values = {}
        parsed_custom.append(values)
        for key in values:
            if key not in columns and key not in custom_columns:
                custom_columns.append(key)
    # Tags round-trip through the export so a re-import keeps them.
    all_columns = columns + ["tags"] + custom_columns

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(all_columns)
    for contact, custom in zip(contacts, parsed_custom):
        tag_names = ", ".join(await _tag_names(db, contact.id))
        writer.writerow(
            [getattr(contact, col) or "" for col in columns]
            + [tag_names]
            + [custom.get(col, "") for col in custom_columns]
        )

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=contacts.csv"},
    )


@router.get("/{contact_id}/activity")
async def get_contact_activity(
    contact_id: int,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get contact activity timeline."""
    from app.models.conversation import Message

    msg_result = await db.execute(
        select(Message)
        .where(Message.contact_id == contact_id)
        .order_by(Message.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    messages = msg_result.scalars().all()

    return {
        "total": len(messages),
        "items": [
            {
                "id": m.id,
                "direction": m.direction,
                "body": m.body[:200],
                "status": m.status,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    }
