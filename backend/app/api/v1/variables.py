"""Contact variable registry API.

Backs the Variables page: list every variable ever imported, rename its short
code, give it a fallback, and check what a message will look like before it is
sent.
"""

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contact import Contact
from app.models.user import User
from app.models.variable import ContactVariable
from app.schemas.variable import VariableCreate, VariableOut, VariableUpdate
from app.security.auth import get_current_user
from app.services.variable_service import (
    analyze_message,
    humanize,
    sync_variables_from_contacts,
)
from app.utils.templating import normalize_key

router = APIRouter()


@router.get("/")
async def list_variables(
    search: Optional[str] = None,
    source: Optional[str] = None,
    sync: bool = Query(default=True, description="Discover new columns before listing"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List every known variable.

    ``sync`` defaults to true so that opening the page immediately after a CSV
    import shows the new columns without the operator doing anything.
    """
    stats = None
    if sync:
        stats = await sync_variables_from_contacts(db)

    query = select(ContactVariable)
    if source:
        query = query.where(ContactVariable.source == source)
    rows = (await db.execute(query)).scalars().all()

    if search:
        term = search.strip().lower()
        rows = [
            row
            for row in rows
            if term in row.label.lower()
            or term in row.shortcode.lower()
            or term in row.field_key.lower()
        ]

    # Standard fields first, then the imported ones alphabetically: the list is
    # long after a wide CSV and this keeps the familiar names on top.
    rows = sorted(rows, key=lambda r: (r.source != "standard", r.label.lower()))

    return {
        "total": len(rows),
        "items": [VariableOut.model_validate(row).model_dump(mode="json") for row in rows],
        "sync": stats,
    }


@router.post("/sync")
async def sync_variables(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-scan all contacts for new variables and refresh usage counts."""
    return await sync_variables_from_contacts(db)


@router.post("/", response_model=VariableOut, status_code=201)
async def create_variable(
    data: VariableCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Declare a variable manually, ahead of the CSV that will fill it."""
    existing = (
        await db.execute(
            select(ContactVariable).where(ContactVariable.field_key == data.field_key)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "A variable with this field key already exists")

    shortcode = data.shortcode or data.field_key
    clash = (
        await db.execute(
            select(ContactVariable).where(ContactVariable.shortcode == shortcode)
        )
    ).scalar_one_or_none()
    if clash:
        raise HTTPException(409, f"The short code '{shortcode}' is already used by {clash.label}")

    variable = ContactVariable(
        field_key=data.field_key,
        label=data.label or humanize(data.field_key),
        shortcode=shortcode,
        fallback_text=data.fallback_text,
        description=data.description,
        source="imported",
    )
    db.add(variable)
    await db.flush()
    await db.refresh(variable)
    return variable


@router.put("/{variable_id}", response_model=VariableOut)
async def update_variable(
    variable_id: int,
    data: VariableUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Rename a variable's short code, label or fallback."""
    variable = (
        await db.execute(select(ContactVariable).where(ContactVariable.id == variable_id))
    ).scalar_one_or_none()
    if not variable:
        raise HTTPException(404, "Variable not found")

    updates = data.model_dump(exclude_unset=True)

    if "shortcode" in updates and updates["shortcode"]:
        shortcode = updates["shortcode"]
        clash = (
            await db.execute(
                select(ContactVariable).where(
                    ContactVariable.shortcode == shortcode,
                    ContactVariable.id != variable_id,
                )
            )
        ).scalar_one_or_none()
        if clash:
            raise HTTPException(
                409,
                f"The short code '{shortcode}' is already used by '{clash.label}'. "
                "Two variables cannot answer to the same short code.",
            )

    for key, value in updates.items():
        setattr(variable, key, value)
    await db.flush()
    await db.refresh(variable)
    return variable


@router.delete("/{variable_id}", status_code=204)
async def delete_variable(
    variable_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a variable from the registry.

    The contact data itself is untouched; only the short-code mapping goes
    away. A standard field cannot be deleted because the renderer always
    supports it.
    """
    variable = (
        await db.execute(select(ContactVariable).where(ContactVariable.id == variable_id))
    ).scalar_one_or_none()
    if not variable:
        raise HTTPException(404, "Variable not found")
    if variable.source == "standard":
        raise HTTPException(400, "Built-in variables cannot be deleted. Deactivate it instead.")
    await db.delete(variable)
    await db.flush()


@router.post("/preview")
async def preview_message(
    body: str = Query(..., min_length=1),
    contact_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Show exactly what a message becomes, and which short codes are dropped."""
    contact = None
    if contact_id:
        contact = (
            await db.execute(select(Contact).where(Contact.id == contact_id))
        ).scalar_one_or_none()
        if not contact:
            raise HTTPException(404, "Contact not found")
    return await analyze_message(db, body, contact)


@router.get("/contact/{contact_id}/profile")
async def contact_profile(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every field held for one contact, labelled with its short code.

    This is what the contact profile view renders: the standard columns plus
    every custom column the CSV brought in, so nothing imported is invisible.
    """
    contact = (
        await db.execute(select(Contact).where(Contact.id == contact_id))
    ).scalar_one_or_none()
    if not contact:
        raise HTTPException(404, "Contact not found")

    await sync_variables_from_contacts(db, limit=1)
    variables = (await db.execute(select(ContactVariable))).scalars().all()
    by_key = {v.field_key: v for v in variables}

    try:
        custom = json.loads(contact.custom_fields or "{}")
        custom = custom if isinstance(custom, dict) else {}
    except (ValueError, TypeError):
        custom = {}

    def entry(field_key: str, value, source: str):
        variable = by_key.get(field_key)
        return {
            "field_key": field_key,
            "label": variable.label if variable else humanize(field_key),
            "shortcode": variable.shortcode if variable else field_key,
            "value": "" if value is None else str(value),
            "source": source,
            "is_active": variable.is_active if variable else True,
        }

    standard_keys = [
        "first_name", "last_name", "business_name", "phone_number", "email",
        "city", "state", "country", "website", "industry", "source", "lead_status",
    ]
    fields = [entry(key, getattr(contact, key, None), "standard") for key in standard_keys]
    fields += [
        entry(normalize_key(key), value, "imported")
        for key, value in custom.items()
        if isinstance(key, str)
    ]

    from app.api.v1.contacts import _tag_names

    return {
        "contact_id": contact.id,
        "display_name": (
            " ".join(p for p in (contact.first_name, contact.last_name) if p)
            or contact.business_name
            or contact.phone_number
        ),
        "phone_number": contact.phone_number,
        "lead_status": contact.lead_status,
        "is_opted_out": contact.is_opted_out,
        "notes": contact.notes,
        "tags": await _tag_names(db, contact.id),
        "messages_sent": contact.messages_sent,
        "messages_received": contact.messages_received,
        "last_contacted_at": contact.last_contacted_at.isoformat() if contact.last_contacted_at else None,
        "last_reply_at": contact.last_reply_at.isoformat() if contact.last_reply_at else None,
        "created_at": contact.created_at.isoformat() if contact.created_at else None,
        "fields": fields,
    }
