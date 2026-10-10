"""Contact Lists API routes."""

from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete, func, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.contacts import (
    _apply_channel_filters,
    _apply_contact_filters,
    _delete_contacts_bulk,
    _safe_delete,
    _safe_update,
)
from app.database import get_db
from app.models.contact import Contact, ContactTag, Tag
from app.models.contact_list import ContactList, ContactListMember
from app.models.user import User
from app.schemas.contact import ContactOut
from app.security.auth import get_current_user

router = APIRouter()


class ListContactsAction(BaseModel):
    """Body for removing / permanently deleting list members.

    ``scope="all"`` acts on every member of the list (optionally narrowed by
    ``search``) on the server — the client never has to enumerate thousands of
    ids across pages, which is what made bulk actions time out on big lists.
    """

    contact_ids: list[int] = []
    scope: Literal["ids", "all"] = "ids"
    search: Optional[str] = None


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


async def _matching_member_ids(
    db: AsyncSession, list_id: int, search: Optional[str]
) -> list[int]:
    """Ids of every contact in a list that matches the shared contact search.

    Used by ``scope="all"`` actions so deleting "everyone matching" stays on
    the server even when the list holds tens of thousands of contacts.
    """
    member_ids = select(ContactListMember.contact_id).where(
        ContactListMember.list_id == list_id
    )
    query = _apply_contact_filters(select(Contact.id), search, None, None).where(
        Contact.id.in_(member_ids)
    )
    return list((await db.execute(query)).scalars().all())


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
    delete_contacts: bool = Query(False, description="Also permanently delete every contact in the list"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a list.

    By default only the list and its memberships are removed; the contacts
    themselves stay. With ``delete_contacts=true`` every phone number in the
    list is also permanently deleted before the list is removed.
    """
    from app.models.campaign import Campaign
    from app.models.scheduled import ScheduledMessage

    contact_list = await _find_list(db, list_id)

    # Scheduled sends created against this list cannot outlive it.
    await _safe_delete(
        db,
        sa_delete(ScheduledMessage).where(ScheduledMessage.list_id == list_id),
    )
    # Campaigns may reference the list; keep the campaign, detach the reference.
    await _safe_update(
        db,
        sa_update(Campaign)
        .where(Campaign.list_id == list_id)
        .values(list_id=None),
    )

    if delete_contacts:
        member_ids = list(
            (
                await db.execute(
                    select(ContactListMember.contact_id).where(
                        ContactListMember.list_id == list_id
                    )
                )
            ).scalars().all()
        )
        if member_ids:
            # Set-based cleanup — deleting every number in a 10k-contact list
            # runs a handful of statements instead of one per contact.
            await _delete_contacts_bulk(db, member_ids)

    await db.delete(contact_list)
    await db.flush()


@router.get("/{list_id}/contacts")
async def get_list_contacts(
    list_id: int,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get contacts in a list (paginated; optionally narrowed by search)."""
    await _find_list(db, list_id)
    query = (
        select(Contact)
        .join(ContactListMember, Contact.id == ContactListMember.contact_id)
        .where(ContactListMember.list_id == list_id)
    )
    if search:
        query = _apply_contact_filters(query, search, None, None)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Contact.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    contacts = (await db.execute(query)).scalars().all()

    tag_map: dict[int, list[str]] = {}
    if contacts:
        tag_rows = await db.execute(
            select(ContactTag.contact_id, Tag.name)
            .join(Tag, ContactTag.tag_id == Tag.id)
            .where(ContactTag.contact_id.in_([contact.id for contact in contacts]))
            .order_by(Tag.name.asc())
        )
        for contact_id, name in tag_rows.all():
            tag_map.setdefault(contact_id, []).append(name)

    # Keep the existing pagination envelope and its original fields, while
    # returning the same useful, typed contact shape as GET /contacts/.
    items = []
    for contact in contacts:
        values = {key: value for key, value in vars(contact).items() if not key.startswith("_")}
        values["tags"] = tag_map.get(contact.id, [])
        items.append(ContactOut.model_validate(values).model_dump(mode="json"))

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": items,
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
    requested_ids = {int(cid) for cid in contact_ids}
    if not requested_ids:
        return {"success": True, "added": 0, "contact_count": await _sync_contact_count(db, contact_list)}

    valid_ids = set(
        (await db.execute(select(Contact.id).where(Contact.id.in_(requested_ids)))).scalars().all()
    )
    missing_ids = requested_ids - valid_ids
    if missing_ids:
        missing = ", ".join(str(contact_id) for contact_id in sorted(missing_ids))
        raise HTTPException(status_code=404, detail=f"Contact not found: {missing}")

    added = await _insert_members(db, list_id, valid_ids)
    count = await _sync_contact_count(db, contact_list)
    await db.flush()
    return {"success": True, "added": added, "contact_count": count}


class AddMatchingContacts(BaseModel):
    """Body for the server-scoped bulk add.

    Instead of the client enumerating every matching contact id (slow and
    unreliable across pages), it sends the filters the user is looking at and
    the server adds every contact that matches — skipping contacts that are
    already members, so re-running the same add is always safe.
    """

    search: Optional[str] = None
    lead_status: Optional[str] = None
    channel: Optional[str] = None
    email_state: Optional[str] = None
    list_id: Optional[int] = None


async def _insert_members(db: AsyncSession, list_id: int, contact_ids) -> int:
    """Bulk-insert list memberships, skipping contacts already in the list.

    Chunked so ``IN (...)`` stays under SQLite's parameter cap even when an
    "add all 20,000 matching" lands on a SQLite-backed deployment.
    """
    ids = [int(cid) for cid in contact_ids]
    added = 0
    for chunk in _chunked_ids(ids, size=1000):
        existing = set(
            (
                await db.execute(
                    select(ContactListMember.contact_id).where(
                        ContactListMember.list_id == list_id,
                        ContactListMember.contact_id.in_(chunk),
                    )
                )
            ).scalars().all()
        )
        new_ids = [cid for cid in chunk if cid not in existing]
        if new_ids:
            db.add_all(
                [ContactListMember(list_id=list_id, contact_id=cid) for cid in new_ids]
            )
            await db.flush()
            added += len(new_ids)
    return added


@router.post("/{list_id}/contacts/add-all")
async def add_all_matching_contacts(
    list_id: int,
    data: AddMatchingContacts = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add EVERY contact matching the given search/status to a list at once.

    Powers both bulk-add surfaces: the Contacts screen's "select all N
    matching -> Add to list" and the list editor's "select all matching" in
    the add-contacts picker. Membership is deduped server-side, so this is
    idempotent and safe to retry.
    """
    contact_list = await _find_list(db, list_id)

    query = _apply_contact_filters(select(Contact.id), data.search, data.lead_status, None)
    query = _apply_channel_filters(query, data.email_state, data.channel)
    if data.list_id is not None:
        query = query.where(Contact.id.in_(select(ContactListMember.contact_id).where(ContactListMember.list_id == data.list_id)))
    ids = list((await db.execute(query)).scalars().all())

    # _insert_members skips contacts that are already in the list, so re-running
    # an "add all" (or overlapping adds) can never create duplicate memberships.
    added = await _insert_members(db, list_id, ids)
    count = await _sync_contact_count(db, contact_list)
    await db.flush()
    return {"success": True, "added": added, "matched": len(ids), "contact_count": count}


@router.post("/{list_id}/contacts/remove")
async def remove_contacts_from_list(
    list_id: int,
    data: ListContactsAction = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove contacts from a list without deleting the contacts themselves.

    ``scope="all"`` removes every matching member on the server (narrowed by
    the optional ``search`` the list view is showing), so "remove all N"
    works no matter how large the list is.
    """
    contact_list = await _find_list(db, list_id)
    removed = 0
    if data.scope == "all":
        member_ids = await _matching_member_ids(db, list_id, data.search)
        if member_ids:
            for chunk in _chunked_ids(member_ids):
                result = await _safe_delete(
                    db,
                    sa_delete(ContactListMember).where(
                        ContactListMember.list_id == list_id,
                        ContactListMember.contact_id.in_(chunk),
                    ),
                )
                if result is not None:
                    removed += result.rowcount or 0
    else:
        requested_ids = {int(cid) for cid in data.contact_ids}
        if requested_ids:
            for chunk in _chunked_ids(list(requested_ids)):
                result = await _safe_delete(
                    db,
                    sa_delete(ContactListMember).where(
                        ContactListMember.list_id == list_id,
                        ContactListMember.contact_id.in_(chunk),
                    ),
                )
                if result is not None:
                    removed += result.rowcount or 0

    count = await _sync_contact_count(db, contact_list)
    await db.flush()
    return {"success": True, "removed": removed, "contact_count": count}


@router.post("/{list_id}/contacts/delete")
async def delete_contacts_from_list_permanently(
    list_id: int,
    data: ListContactsAction = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Permanently delete phone numbers while viewing a list.

    This is the destructive action requested from the List editor: unlike
    ``/contacts/remove`` it hard-deletes the selected contacts (and their
    messages, conversations, follow-ups, tags, and list memberships) instead
    of only removing them from this list.

    ``scope="all"`` deletes every matching member on the server (narrowed by
    the optional ``search``) — the scalable path for "delete all N matching"
    on lists with thousands of members.
    """
    contact_list = await _find_list(db, list_id)

    if data.scope == "all":
        member_ids = await _matching_member_ids(db, list_id, data.search)
        if member_ids:
            await _delete_contacts_bulk(db, member_ids)
        count = await _sync_contact_count(db, contact_list)
        await db.flush()
        return {"success": True, "deleted": len(member_ids), "contact_count": count}

    requested_ids = {int(cid) for cid in data.contact_ids}
    if not requested_ids:
        count = await _sync_contact_count(db, contact_list)
        await db.flush()
        return {"success": True, "deleted": 0, "contact_count": count}

    result = await db.execute(
        select(Contact.id)
        .join(ContactListMember, Contact.id == ContactListMember.contact_id)
        .where(
            ContactListMember.list_id == list_id,
            Contact.id.in_(requested_ids),
        )
    )
    member_contact_ids = list(result.scalars().all())
    deleted = 0
    if member_contact_ids:
        deleted = await _delete_contacts_bulk(db, member_contact_ids)

    count = await _sync_contact_count(db, contact_list)
    await db.flush()
    return {"success": True, "deleted": deleted, "contact_count": count}


def _chunked_ids(ids: list[int], size: int = 400) -> list[list[int]]:
    """Batch id lists so ``IN (...)`` stays under SQLite's parameter cap."""
    return [ids[i : i + size] for i in range(0, len(ids), size)]


@router.post("/{list_id}/clean")
async def clean_list(
    list_id: int,
    remove_from_list: bool = Query(True, description="Unlink bad numbers from this list"),
    delete_contacts: bool = Query(False, description="Permanently delete bad numbers"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Scan a list and drop numbers that will fail (and still bill the SIM).

    Default: mark them undeliverable AND remove them from this list so the
    next campaign never submits them. They stay in Contacts unless
    ``delete_contacts=true``.
    """
    await _find_list(db, list_id)
    from app.services.list_hygiene import clean_contacts

    result = await clean_contacts(
        db,
        list_id=list_id,
        remove_from_list=remove_from_list,
        delete_contacts=delete_contacts,
    )
    return {"success": True, **result}


@router.post("/{list_id}/validate-emails")
async def validate_list_emails(
    list_id: int,
    remove_from_list: bool = Query(
        True, description="Unlink addresses that are confirmed undeliverable"
    ),
    delete_contacts: bool = Query(
        False, description="Permanently delete contacts with confirmed-bad addresses"
    ),
    deep: bool = Query(
        True,
        description="Live mailbox check (Reacher instance or built-in SMTP probe); "
        "false = syntax/MX/disposable checks only",
    ),
    limit: int = Query(200, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Validate every email address in a list — the email twin of ``/clean``.

    Uses the open-source Reacher validator (https://reacher.email) when
    ``REACHER_API_URL`` is configured, and the same checks built into the app
    otherwise. Confirmed-undeliverable addresses are quarantined for email
    (``is_email_undeliverable``) so no campaign will ever send to them and
    burn sender reputation; risky/unknown addresses are left usable but never
    marked verified.

    Sits next to the number validator on purpose: one click cleans a list's
    phone numbers, the next proves its addresses.
    """
    await _find_list(db, list_id)
    from app.services.email_validator import validate_contacts

    query = (
        select(Contact)
        .join(ContactListMember, Contact.id == ContactListMember.contact_id)
        .where(ContactListMember.list_id == list_id)
        .order_by(Contact.id.asc())
    )
    contacts = list((await db.execute(query.limit(limit))).scalars().all())
    result = await validate_contacts(db, contacts, deep=deep, mark=True)

    removed = deleted = 0
    if remove_from_list or delete_contacts:
        # A contact with a working phone STAYS in the list: the address is
        # already quarantined for email, and the number is still textable.
        # Only contacts who are unreachable on every channel leave.
        def _unreachable(c: Contact) -> bool:
            if not c.is_email_undeliverable:
                return False
            phone_ok = bool((c.phone_number or "").strip()) and not c.is_undeliverable
            return not phone_ok

        bad_ids = [c.id for c in contacts if _unreachable(c)]
        if bad_ids:
            if delete_contacts:
                deleted = await _delete_contacts_bulk(db, bad_ids)
                removed = deleted
            elif remove_from_list:
                await db.execute(
                    sa_delete(ContactListMember).where(
                        ContactListMember.list_id == list_id,
                        ContactListMember.contact_id.in_(bad_ids),
                    )
                )
                removed = len(bad_ids)
            await db.flush()

    await db.commit()
    return {"success": True, "removed_from_list": removed, "deleted": deleted, **result}


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
