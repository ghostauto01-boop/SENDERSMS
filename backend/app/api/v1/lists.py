"""Contact Lists API routes."""

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.user import User
from app.security.auth import get_current_user

router = APIRouter()


async def _find_list(db: AsyncSession, list_id: int) -> ContactList:
    result = await db.execute(select(ContactList).where(ContactList.id == list_id))
    contact_list = result.scalar_one_or_none()
    if not contact_list:
        raise HTTPException(status_code=404, detail="List not found")
    return contact_list


async def _sync_contact_count(db: AsyncSession, contact_list: ContactList) -> int:
    """Keep the cached count honest after membership changes."""
    result = await db.execute(
        select(func.count(ContactListMember.id)).where(ContactListMember.list_id == contact_list.id)
    )
    count = result.scalar() or 0
    contact_list.contact_count = count
    return count


@router.get("/")
async def list_lists(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all contact lists with live membership counts.

    ``contact_count`` used to be read only from a cached column. Deleting a
    contact or an older failed import could leave that value at zero even when
    memberships existed. The correlated count makes the Lists tab reflect the
    actual members every time it loads.
    """
    filters = []
    if search:
        filters.append(ContactList.name.ilike(f"%{search}%"))

    total_query = select(func.count()).select_from(ContactList)
    if filters:
        total_query = total_query.where(*filters)
    total = (await db.execute(total_query)).scalar() or 0

    member_count = (
        select(func.count(ContactListMember.id))
        .where(ContactListMember.list_id == ContactList.id)
        .correlate(ContactList)
        .scalar_subquery()
    )
    query = select(ContactList, member_count.label("actual_contact_count"))
    if filters:
        query = query.where(*filters)
    query = (
        query.order_by(ContactList.updated_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    rows = (await db.execute(query)).all()

    items = []
    for contact_list, actual_count in rows:
        # Repair the cache opportunistically as well; campaigns and stats from
        # older code paths may still inspect the model column directly.
        if contact_list.contact_count != actual_count:
            contact_list.contact_count = actual_count
        items.append(
            {
                "id": contact_list.id,
                "name": contact_list.name,
                "description": contact_list.description,
                "contact_count": actual_count,
                "created_at": contact_list.created_at.isoformat(),
                "updated_at": contact_list.updated_at.isoformat(),
            }
        )

    return {"total": total, "items": items}


@router.post("/", status_code=201)
async def create_list(
    name: str = Query(..., min_length=1, max_length=255),
    description: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new contact list."""
    contact_list = ContactList(name=name.strip(), description=description)
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
    contact_list = await _find_list(db, list_id)

    if name:
        contact_list.name = name.strip()
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
    contact_list = await _find_list(db, list_id)
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
    await _find_list(db, list_id)
    query = (
        select(Contact)
        .join(ContactListMember, Contact.id == ContactListMember.contact_id)
        .where(ContactListMember.list_id == list_id)
    )

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Contact.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    contacts = (await db.execute(query)).scalars().all()

    return {
        "total": total,
        "items": [
            {
                "id": contact.id,
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "business_name": contact.business_name,
                "phone_number": contact.phone_number,
                "lead_status": contact.lead_status,
            }
            for contact in contacts
        ],
    }


@router.post("/{list_id}/contacts")
async def add_contacts_to_list(
    list_id: int,
    contact_ids: list[int],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add existing contacts to a list."""
    contact_list = await _find_list(db, list_id)
    requested_ids = set(contact_ids)
    if not requested_ids:
        return {"success": True, "added": 0, "contact_count": await _sync_contact_count(db, contact_list)}

    valid_ids = set(
        (await db.execute(select(Contact.id).where(Contact.id.in_(requested_ids)))).scalars().all()
    )
    missing_ids = requested_ids - valid_ids
    if missing_ids:
        missing = ", ".join(str(contact_id) for contact_id in sorted(missing_ids))
        raise HTTPException(status_code=404, detail=f"Contact not found: {missing}")

    existing_ids = set(
        (
            await db.execute(
                select(ContactListMember.contact_id).where(
                    ContactListMember.list_id == list_id,
                    ContactListMember.contact_id.in_(valid_ids),
                )
            )
        ).scalars().all()
    )
    new_ids = valid_ids - existing_ids
    for contact_id in new_ids:
        db.add(ContactListMember(list_id=list_id, contact_id=contact_id))

    await db.flush()
    count = await _sync_contact_count(db, contact_list)
    await db.flush()
    return {"success": True, "added": len(new_ids), "contact_count": count}


@router.post("/{list_id}/contacts/remove")
async def remove_contacts_from_list(
    list_id: int,
    contact_ids: list[int] = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove contacts from a list without deleting the contacts themselves."""
    contact_list = await _find_list(db, list_id)
    requested_ids = set(contact_ids)
    removed = 0
    if requested_ids:
        result = await db.execute(
            sa_delete(ContactListMember).where(
                ContactListMember.list_id == list_id,
                ContactListMember.contact_id.in_(requested_ids),
            )
        )
        removed = result.rowcount or 0

    count = await _sync_contact_count(db, contact_list)
    await db.flush()
    return {"success": True, "removed": removed, "contact_count": count}


@router.get("/{list_id}/stats")
async def get_list_stats(
    list_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get statistics for a list."""
    contact_list = await _find_list(db, list_id)
    count = await _sync_contact_count(db, contact_list)

    status_query = (
        select(Contact.lead_status, func.count(Contact.id))
        .join(ContactListMember, Contact.id == ContactListMember.contact_id)
        .where(ContactListMember.list_id == list_id)
        .group_by(Contact.lead_status)
    )
    status_result = await db.execute(status_query)
    status_distribution = {row[0]: row[1] for row in status_result}

    return {
        "id": contact_list.id,
        "name": contact_list.name,
        "contact_count": count,
        "lead_status_distribution": status_distribution,
    }
