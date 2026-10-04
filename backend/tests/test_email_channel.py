"""Email channel: schema repair, multi-account send path and channel scoping.

These tests stay off the network: the Brevo provider is monkeypatched at
``httpx.AsyncClient`` so the assertions are about *our* behaviour (which account
was charged, what landed on the message row) rather than about Brevo.
"""

import json

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.models.email import EmailAccount
from app.security.encryption import encrypt_value
from app.services import email_service


# ==========================================================================
# Schema repair: the legacy UNIQUE(contact_id) key must be widened
# ==========================================================================


async def _legacy_engine():
    """An in-memory database shaped like one created before the email release.

    ``conversations`` is dropped and recreated in its old form: no ``channel``
    column, and ``UNIQUE (contact_id)`` declared inline — exactly what an
    existing SQLite database looks like after an upgrade.
    """
    from app.schema_repair import repair_schema_sync

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DROP TABLE conversations"))
        await conn.execute(
            text(
                """
                CREATE TABLE conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contact_id INTEGER NOT NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'active',
                    unread_count INTEGER NOT NULL DEFAULT 0,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    last_message_at DATETIME,
                    last_message_preview VARCHAR(500),
                    CONSTRAINT uq_conversation_contact UNIQUE (contact_id)
                )
                """
            )
        )
    return engine, repair_schema_sync


@pytest.mark.asyncio
async def test_schema_repair_widens_legacy_conversation_unique_key():
    engine, repair_schema_sync = await _legacy_engine()
    async with engine.begin() as conn:
        applied = await conn.run_sync(repair_schema_sync, Base.metadata)
        result = await conn.execute(text("PRAGMA index_list('conversations')"))
        names = {row[1] for row in result.fetchall()}
    await engine.dispose()

    # The old key cannot survive: either it was dropped by name or the table was
    # rebuilt without it.
    assert "uq_conversation_contact" not in names
    assert any("uq_conversation_contact" in change or "rebuilt table" in change for change in applied), applied


@pytest.mark.asyncio
async def test_one_contact_can_have_both_an_sms_and_an_email_thread():
    engine, repair_schema_sync = await _legacy_engine()
    async with engine.begin() as conn:
        await conn.run_sync(repair_schema_sync, Base.metadata)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add(Contact(phone_number="+15550000001", email="a@acme-leads.io"))
        await db.commit()
        contact = (await db.execute(select(Contact))).scalar_one()

        db.add(Conversation(contact_id=contact.id, channel="sms", status="active"))
        db.add(Conversation(contact_id=contact.id, channel="email", status="active"))
        await db.commit()

        threads = (await db.execute(select(Conversation))).scalars().all()
        assert {t.channel for t in threads} == {"sms", "email"}
    await engine.dispose()


@pytest.mark.asyncio
async def test_schema_repair_keeps_existing_rows_when_rebuilding():
    """The rebuild must copy data, not recreate empty tables."""
    engine, repair_schema_sync = await _legacy_engine()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add(Contact(phone_number="+15550000009", email="kept@acme-leads.io"))
        await db.commit()
        contact = (await db.execute(select(Contact))).scalar_one()
        # Raw SQL: the legacy table has none of the newer columns yet.
        await db.execute(
            text(
                "INSERT INTO conversations (contact_id, status, message_count, unread_count) "
                "VALUES (:cid, 'unread', 7, 0)"
            ),
            {"cid": contact.id},
        )
        await db.commit()

    async with engine.begin() as conn:
        await conn.run_sync(repair_schema_sync, Base.metadata)
        rows = (await conn.execute(text("SELECT contact_id, message_count FROM conversations"))).fetchall()
    await engine.dispose()

    assert rows == [(contact.id, 7)]


# ==========================================================================
# Multi-account sending
# ==========================================================================


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


class _FakeResponse:
    def __init__(self, status_code=201, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"messageId": "<fake@brevo>"}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def _fake_brevo(monkeypatch, calls: list, fail_keys: dict | None = None):
    """Patch the provider's HTTP client; records every (api-key, sender) call."""

    fail_keys = fail_keys or {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, *, headers=None, json=None, **kwargs):
            key = headers.get("api-key")
            calls.append((key, (json or {}).get("sender", {}).get("email")))
            if key in fail_keys:
                status, payload = fail_keys[key]
                return _FakeResponse(status, payload)
            return _FakeResponse()

    monkeypatch.setattr("app.providers.brevo.httpx.AsyncClient", FakeClient)


async def _add_accounts(db, *specs):
    accounts = []
    for name, email, key, is_default in specs:
        account = EmailAccount(
            name=name, from_email=email, from_name=name.title(),
            api_key_encrypted=encrypt_value(key), is_default=is_default, is_active=True,
        )
        db.add(account)
        accounts.append(account)
    await db.commit()
    for account in accounts:
        await db.refresh(account)
    return accounts


@pytest.mark.asyncio
async def test_send_uses_the_selected_account_not_the_default(db, monkeypatch):
    calls: list = []
    _fake_brevo(monkeypatch, calls)

    primary, backup = await _add_accounts(
        db,
        ("main", "main@acme-leads.io", "key-primary", True),
        ("backup", "backup@acme-leads.io", "key-backup", False),
    )
    db.add(Contact(phone_number="+15550000002", email="lead@acme-leads.io"))
    await db.commit()
    contact = (await db.execute(select(Contact))).scalar_one()

    account = await email_service.resolve_account(db, backup.id)
    assert account.id == backup.id

    message, result = await email_service.send_now(
        db, contact, subject="Hello", text_body="Body copy", account=account,
    )
    assert result["success"] is True
    assert calls == [("key-backup", "backup@acme-leads.io")]
    assert message.channel == "email"
    assert message.provider == "brevo"
    assert message.direction == "outgoing"
    assert message.provider_message_id == "<fake@brevo>"
    assert message.subject == "Hello"

    # The email split never touches the SMS counters.
    await db.refresh(contact)
    assert (contact.messages_sent or 0) == 0


@pytest.mark.asyncio
async def test_burnt_key_falls_back_to_the_campaign_fallback_account(db, monkeypatch):
    calls: list = []
    _fake_brevo(monkeypatch, calls, fail_keys={
        "key-burnt": (401, {"message": "Key not found", "code": "unauthorized"}),
    })

    burnt, spare = await _add_accounts(
        db,
        ("burnt", "burnt@acme-leads.io", "key-burnt", True),
        ("spare", "spare@acme-leads.io", "key-spare", False),
    )
    campaign = Campaign(
        name="Email blast", channel="email", status="draft",
        email_account_id=burnt.id, fallback_email_account_id=spare.id,
    )
    db.add(campaign)
    db.add(Contact(phone_number="+15550000003", email="lead2@acme-leads.io"))
    await db.commit()
    await db.refresh(campaign)
    contact = (await db.execute(select(Contact).where(Contact.email == "lead2@acme-leads.io"))).scalar_one()

    message = await email_service.queue_email(
        db, contact, subject="Hi", text_body="Hello there",
        account=burnt, campaign_id=campaign.id,
    )
    assert message is not None
    result = await email_service.deliver(db, message)

    assert result["success"] is True
    assert [c[0] for c in calls] == ["key-burnt", "key-spare"]
    assert message.email_account_id == spare.id
    assert message.status == "sent"
    # The failed key is flagged so the operators page shows it needs replacing.
    await db.refresh(burnt)
    assert burnt.connection_status == "error"


@pytest.mark.asyncio
async def test_opted_out_contact_is_never_emailed(db, monkeypatch):
    calls: list = []
    _fake_brevo(monkeypatch, calls)

    await _add_accounts(db, ("main", "main@acme-leads.io", "key", True))
    db.add(Contact(phone_number="+15550000004", email="no@acme-leads.io", is_email_opted_out=True))
    await db.commit()
    contact = (await db.execute(select(Contact))).scalar_one()

    assert await email_service.contact_email_problem(db, contact) == "email_opted_out"

    message, result = await email_service.send_now(db, contact, subject="S", text_body="B")
    assert message is None
    assert result["success"] is False
    assert calls == []


@pytest.mark.asyncio
async def test_send_one_email_bumps_the_email_counters_only(db, monkeypatch):
    from app.tasks.sms_tasks import _send_one_email

    calls: list = []
    _fake_brevo(monkeypatch, calls)

    await _add_accounts(db, ("main", "main@acme-leads.io", "key-main", True))
    db.add(Contact(phone_number="+15550000005", email="counter@acme-leads.io"))
    await db.commit()
    contact = (await db.execute(select(Contact))).scalar_one()

    message = await email_service.queue_email(
        db, contact, subject="Counted", text_body="One email",
    )
    assert message is not None
    settled = await _send_one_email(db, message, contact)

    assert settled is False  # False == settled, matching the SMS contract
    await db.refresh(contact)
    await db.refresh(message)
    assert (contact.emails_sent or 0) == 1
    assert (contact.messages_sent or 0) == 0
    assert contact.last_emailed_at is not None
    assert message.status == "sent"


@pytest.mark.asyncio
async def test_channel_scoped_conversation_lookup(db):
    """One thread per channel: the inbox must never mix SMS and email."""

    db.add(Contact(phone_number="+15550000006", email="mix@acme-leads.io"))
    await db.commit()
    contact = (await db.execute(select(Contact))).scalar_one()

    db.add(Conversation(contact_id=contact.id, channel="sms", status="active"))
    db.add(Conversation(contact_id=contact.id, channel="email", status="active"))
    await db.commit()

    sms = (
        await db.execute(
            select(Conversation).where(
                Conversation.contact_id == contact.id, Conversation.channel == "sms"
            )
        )
    ).scalar_one()
    email = (
        await db.execute(
            select(Conversation).where(
                Conversation.contact_id == contact.id, Conversation.channel == "email"
            )
        )
    ).scalar_one()
    assert sms.id != email.id

    db.add(Message(conversation_id=email.id, contact_id=contact.id, direction="outgoing",
                   body="Hi by email", channel="email", subject="Hello",
                   idempotency_key="test-email-message-1"))
    await db.commit()
    stored = (await db.execute(select(Message))).scalar_one()
    assert stored.channel == "email"
    assert stored.subject == "Hello"
