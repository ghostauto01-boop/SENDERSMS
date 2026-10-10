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
            phone_number=f"+23480312{i:05d}",  # a valid Nigerian mobile (classify_number)
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


# --------------------------------------------------------------------------
# Email senders and message history (P0-3 / P0-5 tests)
# --------------------------------------------------------------------------

_counter = itertools.count(1)


async def make_account(db, *, name: str | None = None, daily_limit: int | None = None,
                       default: bool = False, active: bool = True):
    """A Brevo sender that ``account_is_usable`` accepts."""
    from app.models.email import EmailAccount
    from app.security.encryption import encrypt_value

    n = next(_counter)
    account = EmailAccount(
        name=name or f"Mailbox {n}",
        from_email=f"sender{n}@brandmail{n}.io",
        from_name=f"Sender {n}",
        api_key_encrypted=encrypt_value(f"key-{n}"),
        is_active=active,
        is_default=default,
        daily_limit=daily_limit,
    )
    db.add(account)
    await db.flush()
    return account


async def make_accounts(db, n: int, **kw):
    accounts = []
    for i in range(n):
        accounts.append(await make_account(db, default=(i == 0), **kw))
    return accounts


async def seed_email_messages(
    db,
    *,
    campaign_id: int | None = None,
    ads_campaign_id: int | None = None,
    account_id: int | None = None,
    sent: int = 0,
    bounced: int = 0,
    rejected: int = 0,
    in_flight: int = 0,
    complaints: int = 0,
    sent_at: datetime | None = None,
    bulk: bool = True,
):
    """Outgoing email history for one campaign.

    ``sent`` messages left the building (``sent_at`` set); of those, ``bounced``
    carry a bounce (``bounced_at``, status failed) and ``complaints`` carry a
    spam-complaint event. ``rejected`` never left (the provider refused them
    synchronously). ``in_flight`` are queued.
    """
    from app.models.conversation import Conversation, Message
    from app.models.email import EmailEvent

    n = next(_counter)
    contact = Contact(
        email=f"history{n}@somewhere{n}.io", first_name="H",
        email_verified=True, email_verified_at=datetime.now(timezone.utc), email_confidence=95,
    )
    db.add(contact)
    await db.flush()
    conv = Conversation(contact_id=contact.id, channel="email")
    db.add(conv)
    await db.flush()
    when = sent_at or datetime.now(timezone.utc)

    def _msg(tag: str, i: int, **kw):
        return Message(
            conversation_id=conv.id, contact_id=contact.id, campaign_id=campaign_id,
            ads_campaign_id=ads_campaign_id, direction="outgoing", channel="email",
            body="hi", subject="s", email_account_id=account_id, bulk_send=bulk,
            idempotency_key=f"seed-{n}-{tag}-{i}", provider="brevo", **kw,
        )

    rows = []
    for i in range(sent):
        is_bounce = i < bounced
        rows.append(_msg(
            "s", i,
            status="failed" if is_bounce else "delivered", sent_at=when,
            bounced_at=when if is_bounce else None, bounced_hard=is_bounce,
            failed_at=when if is_bounce else None,
            provider_message_id=f"prov-{n}-{i}",
        ))
    for i in range(rejected):
        rows.append(_msg("r", i, status="failed", failed_at=when,
                         last_error="400 invalid recipient"))
    for i in range(in_flight):
        rows.append(_msg("q", i, status="queued"))
    db.add_all(rows)
    await db.flush()
    for i in range(complaints):
        db.add(EmailEvent(event_type="spam", campaign_id=campaign_id,
                          ads_campaign_id=ads_campaign_id, created_at=when,
                          email_address=f"history{n}@somewhere{n}.io"))
    await db.flush()
    return rows
