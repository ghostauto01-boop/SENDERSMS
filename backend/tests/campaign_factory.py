"""Helpers that build campaigns, lists and contacts for lifecycle tests."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember

_phones = itertools.count(1)


async def make_list(db, n: int = 2, *, name: str = "Test list", email: bool = False,
                    verified: bool = True) -> ContactList:
    """A list with ``n`` contacts (SMS-reachable, plus email when asked)."""
    lst = ContactList(name=f"{name} {next(_phones)}")
    db.add(lst)
    await db.flush()
    for _ in range(n):
        i = next(_phones)
        contact = Contact(
            phone_number=f"+23480{i:08d}",
            first_name=f"C{i}",
            email=f"person{i}@leadco{i}.io" if email else None,
            email_verified=bool(email and verified),
            email_verified_at=datetime.now(timezone.utc) if (email and verified) else None,
            email_confidence=95 if (email and verified) else None,
        )
        db.add(contact)
        await db.flush()
        db.add(ContactListMember(list_id=lst.id, contact_id=contact.id))
    await db.flush()
    return lst


async def make_campaign(db, *, status: str = "draft", n: int = 2, channel: str = "sms",
                        scheduled_start_at: datetime | None = None, **extra) -> Campaign:
    """A campaign that passes validation (list + message) in the given status."""
    lst = await make_list(db, n, email=(channel == "email"))
    campaign = Campaign(
        name=extra.pop("name", "Lifecycle campaign"),
        status=status,
        channel=channel,
        list_id=lst.id,
        message_body="Hello {{first_name}}",
        subject="Hello" if channel == "email" else None,
        scheduled_start_at=scheduled_start_at,
        **extra,
    )
    db.add(campaign)
    await db.flush()
    return campaign


def in_hours(hours: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)
