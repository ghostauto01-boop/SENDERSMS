"""Opportunity: moving a chat (SMS or email) onto the Opportunity page and back.

An Opportunity is a deliberate label on a conversation (``status="opportunity"``).
These tests pin the API the Opportunity page and the chat actions rely on:

1. ``mark-opportunity`` and the generic ``/status`` endpoint set the label on an
   SMS conversation, and moving it back out lands on ``unread`` when messages
   are still unread (so the unread badge survives the move).
2. The SMS list filters by ``status=opportunity`` across both channels and
   reports per-label ``counts`` for the filter tabs.
3. The email status endpoint accepts ``opportunity``.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.user import User
from app.security.auth import get_current_user


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    async def _get_db():
        yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="tester", password_hash="x", role="admin", is_active=True
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, session
    app.dependency_overrides.clear()
    await session.close()
    await engine.dispose()


async def _seed(session, *, phone, channel, status="active", unread=0, email=None):
    contact = Contact(first_name=phone[-4:], phone_number=phone, email=email)
    session.add(contact)
    await session.flush()
    conv = Conversation(
        contact_id=contact.id, channel=channel, status=status, unread_count=unread,
        last_message_preview="hello",
    )
    session.add(conv)
    await session.commit()
    return conv


@pytest.mark.asyncio
async def test_mark_opportunity_then_list_and_counts(client):
    ac, session = client
    sms = await _seed(session, phone="+2348030000001", channel="sms", status="unread", unread=2)
    email = await _seed(session, phone="+2348030000002", channel="email", status="active",
                        email="lead@acme-corp.test")
    await _seed(session, phone="+2348030000003", channel="sms", status="active")

    r = await ac.post(f"/api/v1/inbox/conversations/{sms.id}/mark-opportunity")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "opportunity"

    # The status endpoint moves the email thread onto the page too.
    r = await ac.post(f"/api/v1/email/inbox/conversations/{email.id}/status",
                      json={"status": "opportunity"})
    assert r.status_code == 200, r.text

    listed = await ac.get("/api/v1/inbox/conversations",
                          params={"status": "opportunity", "channel": "all"})
    ids = {item["id"] for item in listed.json()["items"]}
    assert ids == {sms.id, email.id}

    everything = (await ac.get("/api/v1/inbox/conversations", params={"channel": "all"})).json()
    counts = everything["counts"]
    assert counts["all"] == 3
    assert counts["opportunity"] == 2
    assert counts["active"] == 1
    assert counts["unread"] == 1  # the SMS thread still has unread messages


@pytest.mark.asyncio
async def test_moving_out_keeps_the_unread_badge(client):
    ac, session = client
    conv = await _seed(session, phone="+2348030000004", channel="sms", status="opportunity", unread=3)

    r = await ac.post(f"/api/v1/inbox/conversations/{conv.id}/status", json={"status": "active"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "unread"

    await session.refresh(conv)
    assert conv.status == "unread"
    assert conv.unread_count == 3


@pytest.mark.asyncio
async def test_moving_out_of_a_read_thread_goes_to_active(client):
    ac, session = client
    conv = await _seed(session, phone="+2348030000005", channel="sms", status="opportunity", unread=0)

    r = await ac.post(f"/api/v1/inbox/conversations/{conv.id}/status", json={"status": "active"})
    assert r.json()["status"] == "active"


@pytest.mark.asyncio
async def test_status_endpoint_rejects_unknown_labels(client):
    ac, session = client
    conv = await _seed(session, phone="+2348030000006", channel="sms")

    r = await ac.post(f"/api/v1/inbox/conversations/{conv.id}/status", json={"status": "vip"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_email_status_endpoint_accepts_opportunity(client):
    ac, session = client
    email = await _seed(session, phone="+2348030000007", channel="email", status="unread",
                        unread=1, email="buyer@acme-corp.test")

    r = await ac.post(f"/api/v1/email/inbox/conversations/{email.id}/status",
                      json={"status": "opportunity"})
    assert r.status_code == 200, r.text
    await session.refresh(email)
    assert email.status == "opportunity"
