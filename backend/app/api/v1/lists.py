"""
Contact Lists API routes.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status, Body
from sqlalchemy import select, func, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contact_list import ContactList, ContactListMember
from app.models.contact import Contact
from app.models.user import User
from app.security.auth import get_current_user

router = APIRouter()


@router.get("/")
async def list_lists(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all contact lists."""
    query = select(ContactList)

    if search:
        query = query.where(ContactList.name.ilike(f"%{search}%"))

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(ContactList.updated_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    lists = result.scalars().all()

    return {
        "total": total,
        "items": [
            {
                "id": lst.id,
                "name": lst.name,
                "description": lst.description,
                "contact_count": lst.contact_count,
                "created_at": lst.created_at.isoformat(),
                "updated_at": lst.updated_at.isoformat(),
            }
            for lst in lists
        ],
    }


@router.post("/", status_code=201)
async def create_list(
    name: str = Query(..., min_length=1, max_length=255),
    description: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new contact list."""
    contact_list = ContactList(name=name, description=description)
    db.add(contact_list)
    await db.flush()
    await db.refresh(contact_list)
    return {
        "id": contact_list.id,
        "name": contact_list.name,
        "description": contact_list.description,
        "contact_count": contact_list.contact_count,
    }


@router.put("/{list_id}")
async def update_list(
    list_id: int,
    name: Optional[str] = Query(None, min_length=1, max_length=255),
    description: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Rename/update a list."""
    result = await db.execute(select(ContactList).where(ContactList.id == list_id))
    contact_list = result.scalar_one_or_none()
    if not contact_list:
        raise HTTPException(status_code=404, detail="List not found")

    if name:
        contact_list.name = name
    if description is not None:
        contact_list.description = description
    await db.flush()
    return {"success": True}


@router.delete("/{list_id}", status_code=204)
async def delete_list(
    list_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a list and its memberships."""
    result = await db.execute(select(ContactList).where(ContactList.id == list_id))
    contact_list = result.scalar_one_or_none()
    if not contact_list:
        raise HTTPException(status_code=404, detail="List not found")
    await db.delete(contact_list)
    await db.flush()


@router.get("/{list_id}/contacts")
async def get_list_contacts(
    list_id: int,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get contacts in a list."""
    query = (
        select(Contact)
        .join(ContactListMember, Contact.id == ContactListMember.contact_id)
        .where(ContactListMember.list_id == list_id)
    )

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Contact.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    contacts = result.scalars().all()

    return {
        "total": total,
        "items": [
            {
                "id": c.id,
                "first_name": c.first_name,
                "last_name": c.last_name,
                "business_name": c.business_name,
                "phone_number": c.phone_number,
                "lead_status": c.lead_status,
            }
            for c in contacts
        ],
    }


@router.post("/{list_id}/contacts")
async def add_contacts_to_list(
    list_id: int,
    contact_ids: list[int],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add contacts to a list."""
    result = await db.execute(select(ContactList).where(ContactList.id == list_id))
    contact_list = result.scalar_one_or_none()
    if not contact_list:
        raise HTTPException(status_code=404, detail="List not found")

    added = 0
    for cid in contact_ids:
        existing = await db.execute(
            select(ContactListMember).where(
                ContactListMember.list_id == list_id,
                ContactListMember.contact_id == cid,
            )
        )
        if not existing.scalar_one_or_none():
            member = ContactListMember(list_id=list_id, contact_id=cid)
            db.add(member)
            added += 1

    contact_list.contact_count = (contact_list.contact_count or 0) + added
    await db.flush()

    return {"success": True, "added": added}


@router.post("/{list_id}/contacts/remove")
async def remove_contacts_from_list(
    list_id: int,
    contact_ids: list[int] = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove contacts from a list."""
    await db.execute(
        sa_delete(ContactListMember).where(
            ContactListMember.list_id == list_id,
            ContactListMember.contact_id.in_(contact_ids),
        )
    )

    # Update count
    count_result = await db.execute(
        select(func.count()).select_from(ContactListMember).where(ContactListMember.list_id == list_id)
    )
    count = count_result.scalar() or 0

    lst_result = await db.execute(select(ContactList).where(ContactList.id == list_id))
    lst = lst_result.scalar_one_or_none()
    if lst:
        lst.contact_count = count

    await db.flush()
    return {"success": True, "removed": len(contact_ids)}


@router.get("/{list_id}/stats")
async def get_list_stats(
    list_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get statistics for a list."""
    result = await db.execute(select(ContactList).where(ContactList.id == list_id))
    lst = result.scalar_one_or_none()
    if not lst:
        raise HTTPException(status_code=404, detail="List not found")

    # Count by lead status
    status_query = (
        select(Contact.lead_status, func.count(Contact.id))
        .join(ContactListMember, Contact.id == ContactListMember.contact_id)
        .where(ContactListMember.list_id == list_id)
        .group_by(Contact.lead_status)
    )
    status_result = await db.execute(status_query)
    status_distribution = {row[0]: row[1] for row in status_result}

    return {
        "id": lst.id,
        "name": lst.name,
        "contact_count": lst.contact_count,
        "lead_status_distribution": status_distribution,
    }
