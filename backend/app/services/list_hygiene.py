"""Clean contact lists so the carrier is not billed for dead numbers.

A failed SMS still costs money once it has left the phone. The only way to
stop that leak is to never submit those numbers to the gateway:

  * format-invalid / not a Nigerian mobile
  * previously failed delivery (carrier bounce, no service, invalid MSISDN)
  * opted-out (already skipped elsewhere)

This module marks those contacts ``is_undeliverable`` so every send path
skips them, and can optionally remove them from a list or delete them.
"""

from datetime import datetime, timezone

from sqlalchemy import select, func, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.contact_list import ContactListMember
from app.models.conversation import Message
from app.models.suppression import SuppressionEntry
from app.utils.phone import is_nigerian_mobile, normalize_nigerian_number


def classify_number(phone: str) -> tuple[bool, str | None]:
    """Return (ok_to_send, reason_if_not).

    Delegates to the hardened pre-send filter (fake/test patterns, foreign
    numbers, short codes, unknown prefixes) so every send path shares one
    definition of \"sendable\".
    """
    from app.services.number_filter import classify_number as _classify

    r = _classify(phone)
    if r.sendable:
        return True, None
    # Keep the legacy reason spelling for the two historical cases so stored
    # undeliverable_reason values stay comparable over time.
    reason = r.reason or "invalid_format"
    if reason == "not_mobile":
        reason = "not_nigerian_mobile"
    return False, reason


async def mark_undeliverable(contact: Contact, reason: str) -> None:
    contact.is_undeliverable = True
    contact.undeliverable_reason = (reason or "undeliverable")[:500]
    contact.delivery_fail_count = (contact.delivery_fail_count or 0) + 1
    contact.updated_at = datetime.now(timezone.utc)


async def record_delivery_failure(db: AsyncSession, contact_id: int | None, reason: str | None = None) -> None:
    """Called when a send or sms:failed webhook lands. One bounce is enough."""
    if not contact_id:
        return
    contact = (await db.execute(select(Contact).where(Contact.id == contact_id))).scalar_one_or_none()
    if not contact:
        return
    await mark_undeliverable(contact, reason or "delivery_failed")
    await db.flush()


def contact_is_blocked_from_send(contact: Contact) -> str | None:
    """Why this contact must not be submitted to the gateway, or None."""
    if getattr(contact, "is_opted_out", False):
        return "opted_out"
    if getattr(contact, "is_undeliverable", False):
        return contact.undeliverable_reason or "undeliverable"
    ok, reason = classify_number(contact.phone_number or "")
    if not ok:
        return reason
    return None


async def clean_contacts(
    db: AsyncSession,
    *,
    list_id: int | None = None,
    remove_from_list: bool = False,
    delete_contacts: bool = False,
) -> dict:
    """Scan contacts (optionally one list) and quarantine bad numbers.

    ``remove_from_list`` unlinks them from the list but keeps the contact.
    ``delete_contacts`` permanently deletes them (and their history).
    """
    query = select(Contact)
    if list_id is not None:
        query = query.join(ContactListMember, Contact.id == ContactListMember.contact_id).where(
            ContactListMember.list_id == list_id
        )
    contacts = list((await db.execute(query)).scalars().all())

    failed_ids = set(
        (
            await db.execute(
                select(Message.contact_id)
                .where(
                    Message.direction == "outgoing",
                    Message.status.in_(["failed", "cancelled"]),
                    Message.contact_id.isnot(None),
                )
                .distinct()
            )
        ).scalars().all()
    )
    suppressed = set(
        (await db.execute(select(SuppressionEntry.phone_number))).scalars().all()
    )

    invalid = 0
    bounced = 0
    already = 0
    sendable = 0
    by_reason: dict[str, int] = {}
    to_act: list[Contact] = []

    for c in contacts:
        if c.is_undeliverable:
            already += 1
            by_reason["already_undeliverable"] = by_reason.get("already_undeliverable", 0) + 1
            to_act.append(c)
            continue
        ok, reason = classify_number(c.phone_number or "")
        if not ok:
            await mark_undeliverable(c, reason or "invalid_format")
            invalid += 1
            by_reason[reason or "invalid_format"] = by_reason.get(reason or "invalid_format", 0) + 1
            to_act.append(c)
            continue
        # Explicit clean: also catch test-data lookalikes (never hard-blocks
        # a live send, but the user asked to remove them from this list).
        from app.services.number_filter import looks_like_test_data
        soft = looks_like_test_data(c.phone_number or "")
        if soft:
            await mark_undeliverable(c, soft)
            invalid += 1
            by_reason[soft] = by_reason.get(soft, 0) + 1
            to_act.append(c)
            continue
        if c.phone_number in suppressed or c.is_opted_out:
            # Opt-outs are already skipped; do not also flag as undeliverable.
            sendable += 0
            continue
        if c.id in failed_ids:
            await mark_undeliverable(c, "previous_delivery_failed")
            bounced += 1
            by_reason["previous_delivery_failed"] = by_reason.get("previous_delivery_failed", 0) + 1
            to_act.append(c)
            continue
        sendable += 1

    await db.flush()

    removed = 0
    deleted = 0
    if delete_contacts and to_act:
        from app.api.v1.contacts import _delete_contacts_bulk

        deleted = await _delete_contacts_bulk(db, [c.id for c in to_act])
    elif remove_from_list and list_id is not None and to_act:
        from sqlalchemy import delete as sa_delete

        ids = [c.id for c in to_act]
        result = await db.execute(
            sa_delete(ContactListMember).where(
                ContactListMember.list_id == list_id,
                ContactListMember.contact_id.in_(ids),
            )
        )
        removed = result.rowcount or 0
        from app.models.contact_list import ContactList

        cl = (await db.execute(select(ContactList).where(ContactList.id == list_id))).scalar_one_or_none()
        if cl is not None:
            count = (
                await db.execute(
                    select(func.count(ContactListMember.id)).where(ContactListMember.list_id == list_id)
                )
            ).scalar() or 0
            cl.contact_count = count

    await db.flush()
    return {
        "scanned": len(contacts),
        "sendable": sendable,
        "invalid_format": invalid,
        "previous_failures": bounced,
        "already_undeliverable": already,
        "quarantined": invalid + bounced,
        "by_reason": by_reason,
        "removed_from_list": removed,
        "deleted": deleted,
    }
