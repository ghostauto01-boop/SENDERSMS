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
from sqlalchemy import select, func, or_, delete as sa_delete
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

    return ContactListOut(
        total=total,
        items=[ContactOut.model_validate(c) for c in contacts],
    )


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
    return contact


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
    return contact


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
    return contact


@router.delete("/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a contact."""
    result = await db.execute(select(Contact).where(Contact.id == contact_id))
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    await db.delete(contact)
    await db.flush()


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
            await db.delete(c)
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
    skip_duplicates: bool = Form(True),
    column_mapping: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Import contacts from CSV file, optionally into a contact list.

    ``list_id`` / ``skip_duplicates`` / ``column_mapping`` arrive as multipart
    form fields (the UI uploads the file and these together), so they must be
    declared with ``Form`` -- previously ``list_id`` was a query parameter and
    the browser's form field never reached it, so imports always landed with
    no list attached.
    """
    from app.services.csv_service import CSVImportService, detect_column_mapping

    content = await file.read()

    # Fail fast with a clean 404 if the target list does not exist, instead of
    # importing the contacts and silently dropping the list attachment.
    if list_id is not None:
        list_result = await db.execute(select(ContactList).where(ContactList.id == list_id))
        if not list_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="List not found")

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

    service = CSVImportService(db)
    result = await service.validate_and_import(content, column_mapping_map, list_id, skip_duplicates)

    return {
        "imported": result.imported,
        "skipped": result.skipped,
        "invalid": result.invalid,
        "duplicates": result.duplicates,
        "total_rows": result.total_rows,
        "errors": result.errors[:50],  # Limit error report
        "imported_ids": result.imported_contact_ids,
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
    all_columns = columns + custom_columns

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(all_columns)
    for contact, custom in zip(contacts, parsed_custom):
        writer.writerow(
            [getattr(contact, col) or "" for col in columns]
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
