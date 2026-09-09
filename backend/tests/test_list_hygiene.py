"""List hygiene: never submit numbers that will bill the carrier for a fail."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.security.auth import get_current_user
from app.services.list_hygiene import classify_number, contact_is_blocked_from_send, clean_contacts
from app.services.sms_service import SMSService


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db):
    from app.main import app

    async def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="tester", email="t@x.com", password_hash="x", role="admin", is_active=True
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_classify_rejects_junk():
    assert classify_number("123")[0] is False
    assert classify_number("")[0] is False
    ok, reason = classify_number("+2348034567890")
    assert ok is True
    assert reason is None


def test_blocked_undeliverable():
    c = Contact(phone_number="+2348034567890", is_undeliverable=True, undeliverable_reason="no service")
    assert contact_is_blocked_from_send(c) == "no service"


@pytest.mark.asyncio
async def test_clean_marks_invalid_and_failed(db):
    good = Contact(phone_number="+2348034567890", country="Nigeria")
    bad = Contact(phone_number="+2348000000000", country="Nigeria")  # invalid mobile
    bounced = Contact(phone_number="+2348021112233", country="Nigeria")
    db.add_all([good, bad, bounced])
    await db.flush()
    conv = Conversation(contact_id=bounced.id, status="active")
    db.add(conv)
    await db.flush()
    db.add(Message(
        conversation_id=conv.id, contact_id=bounced.id, direction="outgoing",
        body="hi", status="failed", idempotency_key="f1",
    ))
    lst = ContactList(name="N")
    db.add(lst)
    await db.flush()
    for c in (good, bad, bounced):
        db.add(ContactListMember(list_id=lst.id, contact_id=c.id))
    await db.flush()

    result = await clean_contacts(db, list_id=lst.id, remove_from_list=True)
    assert result["scanned"] == 3
    assert result["sendable"] == 1
    assert result["removed_from_list"] >= 1
    await db.refresh(bad)
    assert bad.is_undeliverable is True
    await db.refresh(bounced)
    assert bounced.is_undeliverable is True


@pytest.mark.asyncio
async def test_send_skips_undeliverable(db, monkeypatch):
    c = Contact(phone_number="+2348034567890", country="Nigeria", is_undeliverable=True)
    db.add(c)
    await db.flush()
    called = {"n": 0}

    async def boom(*a, **k):
        called["n"] += 1
        return {"success": True}

    monkeypatch.setattr("app.providers.smsgate.send_sms_direct", boom)
    msg = await SMSService(db).send_message(c.id, "hello")
    assert msg is None
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_clean_list_endpoint(client, db):
    c = Contact(phone_number="not-a-phone", country="Nigeria")
    db.add(c)
    lst = ContactList(name="X")
    db.add(lst)
    await db.flush()
    db.add(ContactListMember(list_id=lst.id, contact_id=c.id))
    await db.flush()
    r = await client.post(f"/api/v1/lists/{lst.id}/clean")
    assert r.status_code == 200
    assert r.json()["invalid_format"] >= 1
