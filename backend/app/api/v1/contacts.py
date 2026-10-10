"""
Contacts API routes.
"""

import base64
import csv
import hashlib
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, UploadFile, File, Form, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, or_, delete as sa_delete, update as sa_update
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contact import Contact, Tag, ContactTag
from app.models.contact_list import ContactList, ContactListMember
from app.models.import_job import ContactImportJob
from app.models.user import User
from app.schemas.contact import (
    ContactCreate, ContactUpdate, ContactOut, ContactListOut, BulkAction,
    ContactImportURLRequest, ContactEnrichRequest,
)
from app.security.auth import get_current_user
from app.utils.contact_identity import clean_phone, normalize_email

router = APIRouter()

LEAD_STATUSES = [
    "new", "contacted", "replied", "interested",
    "follow-up", "meeting", "customer", "not_interested", "closed",
]


async def _set_contact_tags(
    db: AsyncSession, contact_id: int, names: list[str], operation: str = "replace"
) -> None:
    """Apply add/remove/replace atomically while de-duplicating tag names."""
    cleaned = []
    for name in names:
        value = (name or "").strip()
        if value and value.lower() not in {item.lower() for item in cleaned}:
            cleaned.append(value[:100])
    current_rows = await db.execute(
        select(ContactTag, Tag.name)
        .join(Tag, Tag.id == ContactTag.tag_id)
        .where(ContactTag.contact_id == contact_id)
    )
    current_rows_list = current_rows.all()
    current: dict[str, list[ContactTag]] = {}
    for link, name in current_rows_list:
        current.setdefault(name.lower(), []).append(link)
    requested = {name.lower(): name for name in cleaned}

    if operation == "replace":
        for link, _name in current_rows_list:
            await db.delete(link)
        names_to_add = list(requested.values())
    elif operation == "remove":
        for key in requested:
            for link in current.get(key, []):
                await db.delete(link)
        names_to_add = []
    else:
        names_to_add = [name for key, name in requested.items() if key not in current]

    for name in names_to_add:
        tag = (await db.execute(select(Tag).where(func.lower(Tag.name) == name.lower()))).scalars().first()
        if tag is None:
            tag = Tag(name=name)
            db.add(tag)
            await db.flush()
        db.add(ContactTag(contact_id=contact_id, tag_id=tag.id))
    await db.flush()


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


def _apply_contact_filters(query, search: Optional[str], lead_status: Optional[str], tag: Optional[str], undeliverable: Optional[str] = None):
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

    if undeliverable in ("1", "true", "yes"):
        query = query.where(Contact.is_undeliverable.is_(True))
    elif undeliverable in ("0", "false", "no"):
        query = query.where(Contact.is_undeliverable.is_(False))

    return query


def _apply_channel_filters(query, email_state: Optional[str], channel: Optional[str] = None):
    """Channel-specific contact filters used by the Contacts and Email pages.

    * ``channel=sms``   — has a phone number (the SMS contact view)
    * ``channel=email`` — has an address (the email contact view)
    * ``emailable``   — has an address and has not opted out or bounced
    * ``no_email``    — has no usable address (nothing to send to)
    * ``unsubscribed``/``bounced`` — the two states that block email
    * ``verified``    — confirmed to exist by a real verification call
    * ``unverified``  — has an address nobody confirmed (includes guesses)
    * ``inferred``    — the address was pattern-guessed, never proven

    ``unverified`` and ``inferred`` exist so the UI can answer "how many
    addresses can I actually trust?" and so a send can exclude guesses, which
    is the whole reason a guess is stored with a flag instead of being passed
    off as a normal address.
    """
    if channel:
        kind = channel.strip().lower()
        if kind == "sms":
            query = query.where(Contact.phone_number.isnot(None), Contact.phone_number != "")
        elif kind == "email":
            query = query.where(Contact.email.isnot(None), Contact.email != "")
    if not email_state:
        return query
    state = email_state.lower()
    has_address = (Contact.email.isnot(None), Contact.email != "")
    if state in ("emailable", "has_email"):
        query = query.where(
            *has_address,
            Contact.is_email_opted_out.is_(False),
            Contact.is_email_undeliverable.is_(False),
        )
    elif state == "no_email":
        query = query.where(or_(Contact.email.is_(None), Contact.email == ""))
    elif state == "unsubscribed":
        query = query.where(Contact.is_email_opted_out.is_(True))
    elif state == "bounced":
        query = query.where(Contact.is_email_undeliverable.is_(True))
    elif state == "verified":
        query = query.where(*has_address, Contact.email_verified.is_(True))
    elif state == "unverified":
        query = query.where(*has_address, Contact.email_verified.is_(False))
    elif state == "inferred":
        query = query.where(*has_address, Contact.email_source == "inferred")
    return query


@router.get("/", response_model=ContactListOut)
async def list_contacts(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    search: Optional[str] = None,
    lead_status: Optional[str] = None,
    tag: Optional[str] = None,
    undeliverable: Optional[str] = None,
    #: emailable | no_email | unsubscribed | bounced | (empty = everyone)
    email_state: Optional[str] = None,
    #: sms | email — show only contacts reachable on that channel
    channel: Optional[str] = None,
    #: show only contacts belonging to this list
    list_id: Optional[int] = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    exclude_list_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List contacts with pagination, search, filter, and sort.

    ``channel`` powers the two contact views ("SMS contacts" / "Email
    contacts") and ``list_id`` the per-list view. ``exclude_list_id`` hides
    every contact that already belongs to the given list — used by the list
    editor's "add contacts" search so it only ever offers numbers the list
    does not have yet, no matter how large either set is.
    """
    query = _apply_contact_filters(select(Contact), search, lead_status, tag, undeliverable)
    query = _apply_channel_filters(query, email_state, channel)
    if list_id is not None:
        query = query.join(
            ContactListMember, Contact.id == ContactListMember.contact_id
        ).where(ContactListMember.list_id == list_id)
    if exclude_list_id is not None:
        member_ids = select(ContactListMember.contact_id).where(
            ContactListMember.list_id == exclude_list_id
        )
        query = query.where(Contact.id.not_in(member_ids))

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Sort. ``id`` breaks ties: a bulk import gives hundreds of contacts the same
    # created_at, and without a tiebreaker the database may order them differently
    # from one page to the next -- so paging could skip or repeat a contact.
    sort_col = getattr(Contact, sort_by, Contact.created_at)
    if sort_dir == "asc":
        query = query.order_by(sort_col.asc(), Contact.id.asc())
    else:
        query = query.order_by(sort_col.desc(), Contact.id.desc())

    # Paginate
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    contacts = result.scalars().all()

    # Tags for the whole page in ONE query. Serialising per-contact used to
    # run a second query for every row (25 rows = 26 queries per page); a
    # single grouped fetch keeps the payload identical at a fraction of the
    # round-trips, which is what made search feel slow on big databases.
    tag_map: dict[int, list[str]] = {}
    if contacts:
        contact_ids = [c.id for c in contacts]
        tag_rows = await db.execute(
            select(ContactTag.contact_id, Tag.name)
            .join(Tag, ContactTag.tag_id == Tag.id)
            .where(ContactTag.contact_id.in_(contact_ids))
            .order_by(Tag.name.asc())
        )
        for cid, name in tag_rows.all():
            tag_map.setdefault(cid, []).append(name)

    items = []
    for contact in contacts:
        data = {k: v for k, v in vars(contact).items() if not k.startswith("_")}
        data["tags"] = tag_map.get(contact.id, [])
        items.append(ContactOut.model_validate(data).model_dump(mode="json"))
    return ContactListOut(
        total=total,
        items=items,
        page=page,
        per_page=per_page,
        next_page=page + 1 if offset + len(items) < total else None,
    )


# --------------------------------------------------------------------------
# Cursor-paginated export
# --------------------------------------------------------------------------

#: The fixed columns of an export. Custom fields and tags follow them.
EXPORT_COLUMNS = [
    "first_name", "last_name", "business_name", "phone_number", "email",
    "city", "state", "country", "website", "industry", "source", "lead_status",
    "notes",
]


def _encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(token: str) -> dict:
    try:
        padded = token + "=" * (-len(token) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if not isinstance(data, dict) or not isinstance(data.get("last_id"), int):
            raise ValueError("shape")
        return data
    except Exception:  # noqa: BLE001 — any malformed token is the caller's mistake
        raise HTTPException(
            status_code=422,
            detail="Invalid export cursor. Use the next_cursor from the previous page, unchanged.",
        )


def _custom_values(contact: Contact) -> dict:
    try:
        values = json.loads(contact.custom_fields or "{}")
    except (ValueError, TypeError):
        return {}
    return values if isinstance(values, dict) else {}


async def _tag_map(db: AsyncSession, contact_ids: list[int]) -> dict[int, str]:
    """Comma-joined tag names for a page of contacts, in ONE query."""
    names: dict[int, list[str]] = {}
    if contact_ids:
        rows = await db.execute(
            select(ContactTag.contact_id, Tag.name)
            .join(Tag, ContactTag.tag_id == Tag.id)
            .where(ContactTag.contact_id.in_(contact_ids))
            .order_by(Tag.name.asc())
        )
        for contact_id, name in rows.all():
            names.setdefault(contact_id, []).append(name)
    return {cid: ", ".join(values) for cid, values in names.items()}


def _export_row(contact: Contact, tags: str, custom_columns: list[str]) -> list:
    custom = _custom_values(contact)
    return (
        [getattr(contact, col) or "" for col in EXPORT_COLUMNS]
        + [tags]
        + [custom.get(col, "") for col in custom_columns]
    )


def _export_query(search, lead_status, tag, email_state, channel, list_id):
    query = _apply_contact_filters(select(Contact), search, lead_status, tag)
    query = _apply_channel_filters(query, email_state, channel)
    if list_id is not None:
        query = query.join(
            ContactListMember, Contact.id == ContactListMember.contact_id
        ).where(ContactListMember.list_id == list_id)
    return query


@router.get("/export")
async def export_contacts_page(
    cursor: Optional[str] = Query(
        default=None,
        description="The next_cursor of the previous page, unchanged. Omit for the first page.",
    ),
    limit: int = Query(default=200, ge=1, le=500, description="Rows per page (maximum 500)."),
    max_bytes: int = Query(
        default=20_000, ge=1_000, le=200_000,
        description="Stop a page once its CSV text reaches this size (at least one row is "
                    "always returned). The cursor makes up the difference, so nothing is skipped.",
    ),
    format: str = Query(default="csv", pattern="^(csv|json)$",
                        description="csv: a 'csv' text per page. json: 'rows' as objects."),
    search: Optional[str] = None,
    lead_status: Optional[str] = None,
    tag: Optional[str] = None,
    email_state: Optional[str] = None,
    channel: Optional[str] = None,
    list_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export contacts a page at a time, following ``next_cursor`` until it is null.

    Every page reports ``returned`` (rows in this page), ``total`` (rows matching the
    filters), ``truncated`` (more pages follow) and ``next_cursor``. The cursor is a
    keyset on the contact id, so a contact added or removed mid-export never makes a
    page skip or repeat a row, and the columns are fixed by the first page (the union
    of custom fields at that moment) so every page has the same shape. With
    ``format=csv`` only the first page carries the header row: concatenating the
    ``csv`` of every page gives one valid CSV file.
    """
    filters = {
        "search": search, "lead_status": lead_status, "tag": tag,
        "email_state": email_state, "channel": channel, "list_id": list_id,
    }
    fingerprint = hashlib.sha1(
        json.dumps(filters, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]

    base = _export_query(**filters)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0

    if cursor:
        state = _decode_cursor(cursor)
        if state.get("f") != fingerprint:
            raise HTTPException(
                status_code=422,
                detail="This cursor was issued for different filters. Repeat the same "
                       "filters on every page, or start again without a cursor.",
            )
        last_id = state["last_id"]
        custom_columns = [str(c) for c in (state.get("columns") or [])]
    else:
        last_id = 0
        custom_columns = []
        seen: set[str] = set(EXPORT_COLUMNS) | {"tags"}
        custom_rows = await db.execute(
            select(Contact.custom_fields)
            .where(Contact.id.in_(select(base.subquery().c.id)))
            .where(Contact.custom_fields.is_not(None))
            .order_by(Contact.id.asc())
        )
        for (raw,) in custom_rows.all():
            try:
                values = json.loads(raw or "{}")
            except (ValueError, TypeError):
                continue
            for key in values if isinstance(values, dict) else []:
                if key not in seen:
                    seen.add(key)
                    custom_columns.append(key)

    columns = EXPORT_COLUMNS + ["tags"] + custom_columns
    page_query = base.where(Contact.id > last_id).order_by(Contact.id.asc()).limit(limit + 1)
    fetched = list((await db.execute(page_query)).scalars().all())
    has_more_rows = len(fetched) > limit
    contacts = fetched[:limit]
    tags = await _tag_map(db, [c.id for c in contacts])

    rows: list[list] = []
    header_text = ""
    if format == "csv" and not cursor:
        buffer = io.StringIO()
        csv.writer(buffer).writerow(columns)
        header_text = buffer.getvalue()
    used = len(header_text)
    kept: list[Contact] = []
    stopped_early = False
    for contact in contacts:
        row = _export_row(contact, tags.get(contact.id, ""), custom_columns)
        if format == "csv":
            line = io.StringIO()
            csv.writer(line).writerow(row)
            cost = len(line.getvalue())
            if kept and used + cost > max_bytes:
                stopped_early = True
                break
            used += cost
        kept.append(contact)
        rows.append(row)

    more = has_more_rows or stopped_early
    next_cursor = (
        _encode_cursor({"last_id": kept[-1].id, "columns": custom_columns, "f": fingerprint})
        if more and kept else None
    )
    body: dict = {
        "format": format,
        "columns": columns,
        "returned": len(kept),
        "total": total,
        "truncated": next_cursor is not None,
        "next_cursor": next_cursor,
    }
    if format == "json":
        body["rows"] = [dict(zip(columns, row)) for row in rows]
    else:
        out = io.StringIO()
        out.write(header_text)
        writer = csv.writer(out)
        for row in rows:
            writer.writerow(row)
        body["csv"] = out.getvalue()
    return body


@router.post("/clean")
async def clean_all_contacts(
    delete_bad: bool = Query(False, description="Permanently delete invalid/failed numbers"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Quarantine numbers that would bill the carrier for a failed SMS.

    Invalid Nigerian mobiles and numbers that already bounced are marked
    ``is_undeliverable`` so send/campaigns skip them. Pass ``delete_bad=true``
    to permanently delete those contacts instead of only skipping them.
    """
    from app.services.list_hygiene import clean_contacts

    result = await clean_contacts(db, delete_contacts=delete_bad)
    return {"success": True, **result}


@router.post("/validate-emails")
async def validate_emails(
    contact_ids: Optional[list[int]] = Query(default=None),
    scope: Optional[str] = Query(default=None, description="ids | all"),
    search: Optional[str] = None,
    lead_status: Optional[str] = None,
    channel: Optional[str] = None,
    list_id: Optional[int] = None,
    email_state: Optional[str] = None,
    deep: bool = Query(
        True,
        description="Live mailbox check (Reacher or built-in SMTP probe); "
        "false = syntax/MX/disposable checks only",
    ),
    limit: int = Query(500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Validate email addresses with the Reacher-based email validator.

    Explicit ``contact_ids`` win; otherwise the current filters (search,
    status, channel view, list selection) decide — the same selection the
    Contacts page is showing. Confirmed-undeliverable addresses are
    quarantined so every send path skips them; only confirmed-good addresses
    become ``email_verified``.
    """
    from app.services.email_validator import validate_contacts

    ids = contact_ids or []
    if scope == "all":
        query = _apply_contact_filters(select(Contact), search, lead_status, None)
        query = _apply_channel_filters(query, email_state, channel)
        if list_id is not None:
            query = query.join(
                ContactListMember, Contact.id == ContactListMember.contact_id
            ).where(ContactListMember.list_id == list_id)
    elif ids:
        query = select(Contact).where(Contact.id.in_([int(i) for i in ids]))
    else:
        raise HTTPException(status_code=400, detail="No contacts selected")
    # Only rows that actually carry an address are worth a validator call.
    query = query.where(Contact.email.isnot(None), Contact.email != "")

    contacts = list((await db.execute(query.order_by(Contact.id.asc()).limit(limit))).scalars().all())
    result = await validate_contacts(db, contacts, deep=deep, mark=True)
    result["matched"] = len(contacts)
    result["capped"] = len(contacts) >= limit
    await db.commit()
    return result


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
    """Create a new contact.

    A contact needs a phone number or an email address — not both. A phone-only
    lead and an email-only lead are equally valid records; a request with
    neither is refused, because there would be no way to reach that person.
    """
    normalized = clean_phone(data.phone_number)
    email = normalize_email(data.email)

    if data.phone_number and not normalized:
        raise HTTPException(status_code=422, detail="Invalid Nigerian phone number")
    if not normalized and not email:
        raise HTTPException(
            status_code=400,
            detail="A contact needs a phone number or an email address",
        )

    # Check for duplicates on whichever identifiers are present.
    if normalized:
        existing = await db.execute(select(Contact).where(Contact.phone_number == normalized))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Contact with this phone number already exists")
    if email:
        existing = await db.execute(
            select(Contact).where(func.lower(Contact.email) == email).limit(1)
        )
        if existing.scalars().first():
            raise HTTPException(status_code=409, detail="Contact with this email already exists")

    contact = Contact(
        first_name=data.first_name,
        last_name=data.last_name,
        business_name=data.business_name,
        phone_number=normalized,
        email=email,
        email_lower=email,
        email_source="manual" if email else None,
        city=data.city,
        state=data.state,
        country=(data.country or "").strip() or None,
        website=data.website,
        industry=data.industry,
        source=data.source,
        lead_status=data.lead_status,
        notes=data.notes,
        custom_fields=data.custom_fields,
    )
    db.add(contact)
    await db.flush()
    if data.tags:
        await _set_contact_tags(db, contact.id, data.tags, "replace")
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
    tags_supplied = "tags" in update_data
    contact_tags = update_data.pop("tags", None)
    tag_operation = update_data.pop("tag_operation", "replace")

    # Normalise the two identity fields rather than storing whatever was typed,
    # and refuse an edit that would leave the contact with no way to be reached.
    if "phone_number" in update_data:
        raw_phone = update_data["phone_number"]
        if raw_phone in (None, ""):
            # Clearing the phone is allowed only when an address remains.
            update_data["phone_number"] = None
        else:
            normalized = clean_phone(raw_phone)
            if not normalized:
                raise HTTPException(status_code=422, detail="Invalid Nigerian phone number")
            clash = await db.execute(
                select(Contact).where(
                    Contact.phone_number == normalized, Contact.id != contact_id
                )
            )
            if clash.scalar_one_or_none():
                raise HTTPException(
                    status_code=409, detail="Another contact already has this phone number"
                )
            update_data["phone_number"] = normalized

    if "email" in update_data:
        raw_email = update_data["email"]
        email = normalize_email(raw_email)
        if raw_email in (None, "") or not email:
            update_data["email"] = None
            update_data["email_lower"] = None
        else:
            clash = await db.execute(
                select(Contact).where(
                    func.lower(Contact.email) == email, Contact.id != contact_id
                ).limit(1)
            )
            if clash.scalars().first():
                raise HTTPException(
                    status_code=409, detail="Another contact already has this email"
                )
            update_data["email"] = email
            update_data["email_lower"] = email
            # A human just typed this: it supersedes any machine verdict.
            if email != (contact.email or "").lower():
                update_data["email_source"] = "manual"
                update_data["email_verified"] = False
                update_data["email_verified_at"] = None
                # What was learned about the OLD address says nothing about the new one.
                update_data["email_verdict"] = None
                update_data["email_confidence"] = None

    resulting_phone = update_data.get("phone_number", contact.phone_number)
    resulting_email = update_data.get("email", contact.email)
    if not resulting_phone and not resulting_email:
        raise HTTPException(
            status_code=400,
            detail="A contact needs a phone number or an email address",
        )

    for key, value in update_data.items():
        setattr(contact, key, value)
    contact.updated_at = datetime.now(timezone.utc)
    if tags_supplied:
        await _set_contact_tags(db, contact.id, contact_tags or [], tag_operation)

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
    """Delete rows, tolerating a table that was never created in this DB.

    Returns the statement result (or None when the table does not exist here),
    so callers can count affected rows.
    """
    try:
        return await db.execute(statement)
    except (OperationalError, ProgrammingError) as exc:
        if _is_missing_table_error(exc):
            return None
        raise


async def _safe_update(db: AsyncSession, statement):
    """Update rows, tolerating a table that was never created in this DB."""
    try:
        await db.execute(statement)
    except (OperationalError, ProgrammingError) as exc:
        if _is_missing_table_error(exc):
            return
        raise


def _chunked(values: list[int], size: int = 400) -> list[list[int]]:
    """Split id lists into query-sized batches.

    SQLite (used by the test suite) allows at most 999 bound parameters per
    statement, so ``IN (... )`` clauses must stay well below that.
    """
    return [values[i : i + size] for i in range(0, len(values), size)]


async def _delete_contacts_bulk(db: AsyncSession, contact_ids: list[int]) -> int:
    """Permanently delete many contacts with set-based statements.

    This is the scalable version of ``_delete_contact_permanently``. Deleting
    one contact at a time issued ~15 statements per contact inside a single
    transaction — deleting a 10,000-contact list ran ~150,000 queries and
    could take minutes or time out. Here every cleanup table is cleared in a
    handful of ``IN (... )`` statements (batched for SQLite's parameter
    limit) and the rows themselves are removed in bulk, so deleting 10,000
    contacts takes the same few dozen queries as deleting one.

    Returns the number of contact rows actually deleted.
    """
    from app.models.ads import AdsAssignment, AdsCalendarEvent, AdsEvent, AdsFollowUpTask
    from app.models.campaign import CampaignContact
    from app.models.campaign_followup import CampaignFollowUpLog
    from app.models.conversation import Conversation, Message
    from app.models.followup import FollowUp
    from app.models.meeting import Meeting, MeetingAttendee
    from app.models.scheduled import ScheduledMessage
    from app.models.suppression import SuppressionEntry

    ids = sorted({int(cid) for cid in contact_ids if cid is not None})
    if not ids:
        return 0
    total_deleted = 0

    # List membership rows are about to disappear; remember which lists were
    # affected so their cached member counts can be repaired in one sweep.
    affected_lists = set()
    for chunk in _chunked(ids):
        rows = (
            await db.execute(
                select(ContactListMember.list_id).where(
                    ContactListMember.contact_id.in_(chunk)
                )
            )
        ).scalars().all()
        affected_lists.update(rows)

    # Meetings/calendar: an attendee link dies with the contact; a meeting is
    # deleted only when its primary contact goes AND no other attendee keeps
    # it alive; otherwise the primary contact is detached so the record (and
    # any other attendees) survives.
    for chunk in _chunked(ids):
        await _safe_delete(
            db,
            sa_delete(MeetingAttendee).where(MeetingAttendee.contact_id.in_(chunk)),
        )
    for chunk in _chunked(ids):
        await _safe_delete(
            db,
            sa_delete(Meeting).where(
                Meeting.contact_id.in_(chunk),
                Meeting.id.notin_(
                    select(MeetingAttendee.meeting_id).where(
                        MeetingAttendee.contact_id.in_(chunk)
                    )
                ),
            ),
        )
        await _safe_update(
            db,
            sa_update(Meeting)
            .where(Meeting.contact_id.in_(chunk))
            .values(contact_id=None),
        )

    # Plain "belongs to the contact" rows — cleared with one statement per
    # table, in dependency order (messages before the conversation rows they
    # point at, etc.).
    for chunk in _chunked(ids):
        await _safe_delete(
            db, sa_delete(ScheduledMessage).where(ScheduledMessage.contact_id.in_(chunk))
        )
        await _safe_delete(db, sa_delete(Message).where(Message.contact_id.in_(chunk)))
        await _safe_delete(
            db, sa_delete(Conversation).where(Conversation.contact_id.in_(chunk))
        )
        await _safe_delete(db, sa_delete(FollowUp).where(FollowUp.contact_id.in_(chunk)))
        await _safe_delete(
            db, sa_delete(CampaignContact).where(CampaignContact.contact_id.in_(chunk))
        )
        await _safe_delete(
            db,
            sa_delete(CampaignFollowUpLog).where(
                CampaignFollowUpLog.contact_id.in_(chunk)
            ),
        )
        await _safe_delete(
            db,
            sa_delete(SuppressionEntry).where(SuppressionEntry.contact_id.in_(chunk)),
        )
        await _safe_delete(
            db, sa_delete(AdsAssignment).where(AdsAssignment.contact_id.in_(chunk))
        )
        await _safe_delete(
            db, sa_delete(AdsFollowUpTask).where(AdsFollowUpTask.contact_id.in_(chunk))
        )
        await _safe_delete(
            db, sa_delete(AdsCalendarEvent).where(AdsCalendarEvent.contact_id.in_(chunk))
        )
        await _safe_update(
            db,
            sa_update(AdsEvent).where(AdsEvent.contact_id.in_(chunk)).values(contact_id=None),
        )
        # Tag links and list memberships used to rely on the ORM's
        # delete-orphan cascade; bulk deletes bypass the ORM, so they are
        # removed explicitly here (no FK surprises on Postgres).
        await _safe_delete(
            db, sa_delete(ContactTag).where(ContactTag.contact_id.in_(chunk))
        )
        await _safe_delete(
            db,
            sa_delete(ContactListMember).where(ContactListMember.contact_id.in_(chunk)),
        )
        result = await _safe_delete(
            db, sa_delete(Contact).where(Contact.id.in_(chunk))
        )
        if result is not None and result.rowcount:
            total_deleted += result.rowcount

    if affected_lists:
        await _refresh_list_counts(db, affected_lists)
    await db.flush()
    return total_deleted


async def _refresh_list_counts(db: AsyncSession, list_ids) -> None:
    """Repair ``contact_lists.contact_count`` after membership deletions."""
    ids = list({int(list_id) for list_id in list_ids})
    if not ids:
        return
    count_subq = (
        select(func.count(ContactListMember.id))
        .where(ContactListMember.list_id == ContactList.id)
        .correlate(ContactList)
        .scalar_subquery()
    )
    await db.execute(
        sa_update(ContactList)
        .where(ContactList.id.in_(ids))
        .values(contact_count=count_subq)
    )


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

    Backed by the same set-based cleanup as bulk deletes, so a single contact
    and 10,000 contacts behave identically.
    """
    await _delete_contacts_bulk(db, [contact.id])


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
    """Perform bulk action on contacts.

    Two scopes are supported:

    - ``scope="ids"`` (default): act on ``contact_ids``.
    - ``scope="all"`` (delete only): act on *every* contact matching the
      ``search`` / ``lead_status`` / ``tag`` filters — the same filters the
      Contacts list is showing. This powers "select all N matching" in the
      UI without the client having to enumerate every id across pages.
    """
    if data.scope == "all":
        if data.action != "delete":
            raise HTTPException(
                status_code=400,
                detail="scope='all' is only supported for action='delete'",
            )
        id_query = _apply_contact_filters(
            select(Contact.id), data.search, data.lead_status, data.tag
        )
        id_query = _apply_channel_filters(id_query, data.email_state, data.channel)
        if data.list_id is not None:
            id_query = id_query.join(
                ContactListMember, Contact.id == ContactListMember.contact_id
            ).where(ContactListMember.list_id == data.list_id)
        matching_ids = list((await db.execute(id_query)).scalars().all())
        if matching_ids:
            # Set-based cleanup: one batch of statements, however many rows.
            await _delete_contacts_bulk(db, matching_ids)
        return {"success": True, "affected": len(matching_ids), "scope": "all"}

    requested_ids = list({int(cid) for cid in data.contact_ids if cid is not None})
    if not requested_ids:
        return {"success": True, "affected": 0}

    if data.action == "delete":
        result = await db.execute(select(Contact.id).where(Contact.id.in_(requested_ids)))
        existing_ids = list(result.scalars().all())
        if existing_ids:
            await _delete_contacts_bulk(db, existing_ids)
        await db.flush()
        return {"success": True, "affected": len(existing_ids)}

    result = await db.execute(select(Contact).where(Contact.id.in_(requested_ids)))
    contacts = result.scalars().all()

    if data.action == "status":
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


MAX_CONTACT_IMPORT_BYTES = 20 * 1024 * 1024


def _parse_import_json(raw: str | None, *, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid JSON in import options") from exc


def _import_job_dict(job: ContactImportJob) -> dict:
    return {
        "id": job.id,
        "status": job.status,
        "file_name": job.file_name,
        "total_rows": job.total_rows,
        "processed_rows": job.processed_rows,
        "progress_percent": round(job.processed_rows * 100 / job.total_rows, 1) if job.total_rows else 0,
        "imported": job.imported,
        "merged": job.merged,
        "duplicates": job.duplicates,
        "invalid": job.invalid,
        "error": job.error,
        "list_id": job.list_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


async def _run_contact_import_job(job_id: str) -> None:
    """Run one import in a separate session and durably publish progress."""
    from app.database import async_session_factory
    from app.services.csv_service import CSVImportService

    async with async_session_factory() as db:
        job = (await db.execute(
            select(ContactImportJob).where(ContactImportJob.id == job_id)
        )).scalar_one_or_none()
        if job is None or job.status not in ("queued", "retrying"):
            return
        content = (job.content or "").encode("utf-8")
        mapping = _parse_import_json(job.mapping_json, default={})
        tags = _parse_import_json(job.tags_json, default=[])
        auto_rules = _parse_import_json(getattr(job, "auto_tag_rules_json", None), default=[])
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await db.commit()

        async def publish_progress(processed: int, result) -> None:
            job.processed_rows = processed
            job.total_rows = max(job.total_rows, result.total_rows)
            job.imported = result.imported
            job.merged = result.merged
            job.duplicates = result.duplicates
            job.invalid = result.invalid
            job.errors_json = json.dumps(result.errors, ensure_ascii=False)
            await db.commit()

        try:
            service = CSVImportService(db)
            result = await service.validate_and_import(
                content,
                mapping,
                job.list_id,
                bool(job.skip_duplicates),
                tags=tags,
                auto_tag_rules=auto_rules,
                progress_callback=publish_progress,
            )
            job.status = "completed"
            job.total_rows = result.total_rows
            job.processed_rows = result.total_rows
            job.imported = result.imported
            job.merged = result.merged
            job.duplicates = result.duplicates
            job.invalid = result.invalid
            job.errors_json = json.dumps(result.errors, ensure_ascii=False)
            new_variables = 0
            try:
                from app.services.variable_service import sync_variables_from_contacts

                variable_sync = await sync_variables_from_contacts(db)
                new_variables = int(variable_sync.get("discovered", 0))
            except Exception:
                # Variable discovery is a convenience; it must not fail a
                # committed contact import.
                pass
            summary = result.as_dict()
            summary["errors"] = result.errors[:50]
            summary["new_variables"] = new_variables
            # Contact IDs are intentionally omitted from the persistent job
            # result; the CRM already owns those records.
            summary.pop("imported_ids", None)
            job.result_json = json.dumps(summary, ensure_ascii=False)
            job.content = None
            job.completed_at = datetime.now(timezone.utc)
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            current = (await db.execute(
                select(ContactImportJob).where(ContactImportJob.id == job_id)
            )).scalar_one_or_none()
            if current:
                current.status = "failed"
                current.error = f"{type(exc).__name__}: {str(exc)[:1000]}"
                current.content = None
                current.completed_at = datetime.now(timezone.utc)
                await db.commit()


async def _create_import_job(
    db: AsyncSession,
    user: User,
    *,
    file_name: str,
    content: bytes,
    mapping: dict[str, str] | None,
    list_id: int | None,
    new_list_name: str | None,
    skip_duplicates: bool,
    tags: list[str] | None,
    auto_tag_rules: list[dict] | None,
) -> ContactImportJob:
    from app.services.csv_service import detect_column_mapping

    decoded = content.decode("utf-8-sig", errors="replace")
    headers = next(csv.reader(io.StringIO(decoded)), [])
    if not headers:
        raise HTTPException(status_code=422, detail="No headers found in CSV")
    auto_mapping = detect_column_mapping(headers)
    if mapping:
        auto_mapping.update({str(k).strip().lower(): v for k, v in mapping.items()})

    if list_id is not None:
        target_list = (await db.execute(
            select(ContactList).where(ContactList.id == list_id)
        )).scalar_one_or_none()
        if target_list is None:
            raise HTTPException(status_code=404, detail="List not found")
    elif new_list_name and new_list_name.strip():
        target_list = ContactList(name=new_list_name.strip()[:255])
        db.add(target_list)
        await db.flush()
        list_id = target_list.id

    from app.services.csv_service import CSVImportService
    preview = await CSVImportService(db).preview_csv(content, max_rows=0)
    job = ContactImportJob(
        id=str(uuid.uuid4()),
        owner_id=user.id,
        file_name=(file_name or "contacts.csv")[:255],
        status="queued",
        total_rows=preview.get("total_rows", 0),
        content=decoded,
        mapping_json=json.dumps(auto_mapping, ensure_ascii=False),
        tags_json=json.dumps(tags or [], ensure_ascii=False),
        auto_tag_rules_json=json.dumps(auto_tag_rules or [], ensure_ascii=False),
        list_id=list_id,
        skip_duplicates=1 if skip_duplicates else 0,
    )
    db.add(job)
    await db.flush()
    return job


@router.post("/import/preview")
async def preview_csv_import(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Preview headers, mappings, first rows, and channel counts before import."""
    content = await file.read(MAX_CONTACT_IMPORT_BYTES + 1)
    if len(content) > MAX_CONTACT_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="CSV file is larger than 20 MB")
    from app.services.csv_service import CSVImportService
    return await CSVImportService(db).preview_csv(content)


@router.post("/import/jobs", status_code=202)
async def create_csv_import_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    list_id: Optional[int] = Form(None),
    new_list_name: Optional[str] = Form(None),
    skip_duplicates: bool = Form(True),
    column_mapping: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    auto_tag_rules: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Queue a CSV import and return a pollable job id immediately."""
    from app.services.csv_service import split_tags
    content = await file.read(MAX_CONTACT_IMPORT_BYTES + 1)
    if len(content) > MAX_CONTACT_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="CSV file is larger than 20 MB")
    mapping = _parse_import_json(column_mapping, default={})
    auto_rules = _parse_import_json(auto_tag_rules, default=[])
    if not isinstance(mapping, dict) or not isinstance(auto_rules, list):
        raise HTTPException(status_code=422, detail="column_mapping must be an object and auto_tag_rules an array")
    job = await _create_import_job(
        db, current_user, file_name=file.filename or "contacts.csv", content=content,
        mapping=mapping, list_id=list_id, new_list_name=new_list_name,
        skip_duplicates=skip_duplicates, tags=split_tags(tags or ""),
        auto_tag_rules=auto_rules,
    )
    await db.commit()
    background_tasks.add_task(_run_contact_import_job, job.id)
    return _import_job_dict(job)


@router.post("/import/url", status_code=202)
async def create_csv_import_job_from_url(
    data: ContactImportURLRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch a public CSV URL safely, then queue the same background importer."""
    from urllib.parse import urlsplit
    import ipaddress
    import socket
    import httpx

    parsed = urlsplit(data.url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(status_code=422, detail="URL must be a public HTTP or HTTPS address")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "metadata.google.internal"} or host.endswith((".localhost", ".local", ".internal")):
        raise HTTPException(status_code=422, detail="Private or local URLs are not allowed")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))}
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise HTTPException(status_code=422, detail="URL host must resolve to a public address")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Could not resolve the public CSV URL") from exc
    try:
        chunks: list[bytes] = []
        downloaded = 0
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            async with client.stream("GET", data.url) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    raise HTTPException(status_code=422, detail=f"CSV URL returned HTTP {response.status_code}")
                content_length = response.headers.get("content-length")
                if content_length and content_length.isdigit() and int(content_length) > MAX_CONTACT_IMPORT_BYTES:
                    raise HTTPException(status_code=413, detail="CSV file is larger than 20 MB")
                async for chunk in response.aiter_bytes():
                    downloaded += len(chunk)
                    if downloaded > MAX_CONTACT_IMPORT_BYTES:
                        raise HTTPException(status_code=413, detail="CSV file is larger than 20 MB")
                    chunks.append(chunk)
        content = b"".join(chunks)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Could not download CSV from the provided URL") from exc
    from app.services.csv_service import split_tags
    file_name = data.file_name or (host.split(".")[0] + ".csv")
    job = await _create_import_job(
        db, current_user, file_name=file_name, content=content,
        mapping=data.column_mapping, list_id=data.list_id, new_list_name=data.new_list_name,
        skip_duplicates=data.skip_duplicates, tags=split_tags(", ".join(data.tags or [])),
        auto_tag_rules=data.auto_tag_rules,
    )
    await db.commit()
    background_tasks.add_task(_run_contact_import_job, job.id)
    return _import_job_dict(job)


@router.get("/import/jobs/{job_id}")
async def read_csv_import_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = (await db.execute(
        select(ContactImportJob).where(ContactImportJob.id == job_id)
    )).scalar_one_or_none()
    if job is None or (job.owner_id != current_user.id and current_user.role != "admin"):
        raise HTTPException(status_code=404, detail="Import job not found")
    await db.refresh(job)
    result = _import_job_dict(job)
    if job.result_json:
        result["result"] = json.loads(job.result_json)
    if job.list_id is not None:
        contact_list = (await db.execute(
            select(ContactList).where(ContactList.id == job.list_id)
        )).scalar_one_or_none()
        if contact_list is not None:
            actual_count = (await db.execute(
                select(func.count(ContactListMember.id)).where(
                    ContactListMember.list_id == contact_list.id
                )
            )).scalar() or 0
            result["list"] = {
                "id": contact_list.id,
                "name": contact_list.name,
                "contact_count": actual_count,
            }
    return result


@router.get("/import/jobs/{job_id}/errors.csv")
async def download_csv_import_errors(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = (await db.execute(
        select(ContactImportJob).where(ContactImportJob.id == job_id)
    )).scalar_one_or_none()
    if job is None or (job.owner_id != current_user.id and current_user.role != "admin"):
        raise HTTPException(status_code=404, detail="Import job not found")
    try:
        errors = json.loads(job.errors_json or "[]")
    except (ValueError, TypeError):
        errors = []
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["row", "level", "error"])
    for item in errors:
        writer.writerow([item.get("row", ""), item.get("level", "error"), item.get("error", "")])
    return StreamingResponse(
        iter([buffer.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="import-errors-{job.id}.csv"'},
    )


@router.post("/import/csv")
async def import_csv(
    file: UploadFile = File(...),
    list_id: Optional[int] = Form(None),
    new_list_name: Optional[str] = Form(None),
    skip_duplicates: bool = Form(True),
    column_mapping: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    auto_tag_rules: Optional[str] = Form(None),
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

    content = await file.read(MAX_CONTACT_IMPORT_BYTES + 1)
    if len(content) > MAX_CONTACT_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="CSV file is larger than 20 MB")

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
        client_mapping = _parse_import_json(column_mapping, default={})
        if not isinstance(client_mapping, dict):
            raise HTTPException(status_code=422, detail="column_mapping must be a JSON object")
        for header, field in client_mapping.items():
            # An explicit blank/"ignore" means the user chose not to import
            # that column. Non-empty custom:<key> targets preserve arbitrary
            # CSV data in Contact.custom_fields.
            column_mapping_map[str(header).strip().lower()] = field or "ignore"

    from app.services.csv_service import split_tags
    tag_list = split_tags(tags) if tags else []
    auto_rules = _parse_import_json(auto_tag_rules, default=[])
    if not isinstance(auto_rules, list):
        raise HTTPException(status_code=422, detail="auto_tag_rules must be a JSON array")

    service = CSVImportService(db)
    result = await service.validate_and_import(
        content, column_mapping_map, list_id, skip_duplicates,
        tags=tag_list, auto_tag_rules=auto_rules,
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
        **result.as_dict(),
        "errors": result.errors[:50],  # Limit error report
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


@router.get("/enrich/status")
async def enrichment_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Which enrichment stages are available, and how much work is outstanding.

    ``providers`` lists the ones with an API key configured, so the UI can tell
    "nothing to do here, there is no key" apart from "the key is exhausted".
    """
    from app.config import settings as _settings
    from app.services import email_enrichment as enrichment

    total = (await db.execute(select(func.count(Contact.id)))).scalar() or 0
    with_email = (
        await db.execute(
            select(func.count(Contact.id)).where(
                Contact.email.isnot(None), Contact.email != ""
            )
        )
    ).scalar() or 0
    verified = (
        await db.execute(
            select(func.count(Contact.id)).where(Contact.email_verified.is_(True))
        )
    ).scalar() or 0
    unverified = (
        await db.execute(
            select(func.count(Contact.id)).where(
                Contact.email.isnot(None),
                Contact.email != "",
                Contact.email_verified.is_(False),
            )
        )
    ).scalar() or 0
    inferred = (
        await db.execute(
            select(func.count(Contact.id)).where(Contact.email_source == "inferred")
        )
    ).scalar() or 0

    return {
        "enabled": _settings.EMAIL_ENRICHMENT_ENABLED,
        "providers": sorted(enrichment.configured_providers().keys()),
        "providers_active": _settings.email_enrichment_active,
        "allow_inferred": _settings.EMAIL_ENRICHMENT_ALLOW_INFERRED,
        # Whether a guessed address may actually be emailed. It is always
        # *stored*; this is the send-time rule, reported so the UI can explain
        # why a guessed address never appears in a campaign's recipients.
        "send_inferred": _settings.EMAIL_SEND_INFERRED,
        "dns_available": enrichment._dns_available(),
        "free_stages": ["clean", "diagnose"],
        "paid_stages": ["find", "verify"],
        "contacts": {
            "total": total,
            "with_email": with_email,
            "without_email": max(total - with_email, 0),
            "verified": verified,
            "unverified": unverified,
            "inferred": inferred,
        },
    }


@router.post("/enrich")
async def enrich_emails(
    data: ContactEnrichRequest | None = Body(default=None),
    contact_ids: Optional[list[int]] = Query(default=None),
    scope: Optional[str] = Query(default=None, description="ids | all | no_email | unverified | list"),
    search: Optional[str] = Query(default=None),
    lead_status: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    allow_inferred: Optional[bool] = Query(default=None),
    limit: Optional[int] = Query(default=None, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Clean, find and verify email addresses for contacts that need it.

    New clients may send a typed JSON object. Existing clients may continue to
    pass the same selection values as query parameters; an explicit query value
    takes precedence when both forms are supplied. Selection defaults to
    explicit contact IDs so an empty/ambiguous request can never enrich the
    whole database by accident.
    """
    from app.services import email_enrichment as enrichment

    body = data.model_dump(exclude_unset=True) if data is not None else {}
    nested_filters = body.get("filters") or body.get("filter") or {}

    def selected(name: str, query_value=None, default=None):
        if query_value is not None:
            return query_value
        if body.get(name) is not None:
            return body[name]
        if nested_filters.get(name) is not None:
            return nested_filters[name]
        return default

    ids = contact_ids
    if ids is None:
        ids = body.get("contact_ids")
        if ids is None:
            ids = body.get("ids")
    selected_list_id = selected("list_id")
    requested_scope = scope or body.get("scope")
    if requested_scope is None:
        if body.get("all") or body.get("all_matching"):
            requested_scope = "all"
        elif ids:
            requested_scope = "ids"
        elif selected_list_id is not None:
            requested_scope = "list"
        else:
            requested_scope = "ids"
    elif body.get("all") or body.get("all_matching"):
        requested_scope = "all"

    filter_search = selected("search", search)
    filter_status = selected("lead_status", lead_status)
    filter_tag = selected("tag", tag)
    run_limit = limit if limit is not None else body.get("limit", 500)
    inferred = allow_inferred if allow_inferred is not None else body.get("allow_inferred")

    if requested_scope == "ids":
        ids = sorted({int(cid) for cid in (ids or []) if cid is not None})
        if not ids:
            raise HTTPException(status_code=400, detail="No contacts selected")
        query = select(Contact).where(Contact.id.in_(ids))
    else:
        query = _apply_contact_filters(select(Contact), filter_search, filter_status, filter_tag)
        if requested_scope == "no_email":
            query = query.where(or_(Contact.email.is_(None), Contact.email == ""))
        elif requested_scope == "unverified":
            query = query.where(
                Contact.email.isnot(None),
                Contact.email != "",
                Contact.email_verified.is_(False),
            )
        elif requested_scope == "list":
            if selected_list_id is None:
                raise HTTPException(status_code=422, detail="list_id is required for scope='list'")
            query = query.join(ContactListMember, Contact.id == ContactListMember.contact_id).where(
                ContactListMember.list_id == selected_list_id
            )
        elif requested_scope != "all":
            raise HTTPException(
                status_code=400,
                detail="scope must be one of: ids, all, no_email, unverified, list",
            )
        query = query.order_by(Contact.id.asc())

    contacts = list((await db.execute(query.limit(run_limit))).scalars().all())
    if not contacts:
        return {
            "success": True, "processed": 0, "matched": 0,
            "message": "Nothing to enrich for that selection",
            "items": [],
        }

    report = await enrichment.enrich_contacts(
        db, contacts, allow_inferred=inferred
    )
    report["matched"] = len(contacts)
    report["capped"] = len(contacts) >= run_limit
    await db.commit()
    return report


@router.get("/export/csv")
async def export_csv(
    search: Optional[str] = None,
    lead_status: Optional[str] = None,
    tag: Optional[str] = None,
    email_state: Optional[str] = None,
    channel: Optional[str] = None,
    list_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export all matching contacts to a CSV file."""
    query = _apply_contact_filters(select(Contact), search, lead_status, tag)
    query = _apply_channel_filters(query, email_state, channel)
    if list_id is not None:
        query = query.join(
            ContactListMember, Contact.id == ContactListMember.contact_id
        ).where(ContactListMember.list_id == list_id)
    query = query.order_by(Contact.created_at.desc())

    result = await db.execute(query)
    contacts = result.scalars().all()

    columns = list(EXPORT_COLUMNS)

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
    tag_by_contact = await _tag_map(db, [c.id for c in contacts])
    for contact, custom in zip(contacts, parsed_custom):
        tag_names = tag_by_contact.get(contact.id, "")
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


@router.post("/{contact_id}/email-opt-out")
async def email_opt_out(
    contact_id: int,
    reason: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Unsubscribe a contact from EMAIL only (their SMS consent is untouched)."""
    from app.services import email_service

    contact = (await db.execute(select(Contact).where(Contact.id == contact_id))).scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    await email_service.unsubscribe_contact(
        db,
        contact_id=contact.id,
        address=contact.email,
        reason=reason or "Manual unsubscribe",
        source="manual",
    )
    await db.commit()
    return {"success": True, "is_email_opted_out": True, "email_status": contact.email_status}


@router.post("/{contact_id}/email-opt-in")
async def email_opt_in(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-subscribe a contact to email and clear the suppression entry."""
    from app.services import email_service

    contact = (await db.execute(select(Contact).where(Contact.id == contact_id))).scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    entry = await email_service.get_suppression(db, contact.email)
    if entry is not None:
        await email_service.unsuppress_email(db, entry)
    contact.is_email_opted_out = False
    contact.email_opted_out_at = None
    contact.email_opt_out_reason = None
    if contact.email_status in ("unsubscribed", "complained"):
        contact.email_status = "active"
    await db.commit()
    return {"success": True, "is_email_opted_out": False, "email_status": contact.email_status}


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
