"""Tests for the Dmobili callback endpoint (inbound SMS + delivery reports).

The two-way promise of the second gateway only works if pushed callbacks
land in the same conversations, dedup safely, respect the shared secret,
and update delivery states for messages that went out through Dmobili.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base, get_db
from app.models.conversation import Conversation, Message

SECRET = "dmobili-callback-secret"


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
async def client(db, monkeypatch):
    monkeypatch.setattr(settings, "DMOBILI_WEBHOOK_SECRET", SECRET, raising=False)
    monkeypatch.setattr(settings, "DMOBILI_WEBHOOK_ALLOW_UNSIGNED", False, raising=False)
    from app.main import app
    app.dependency_overrides[get_db] = lambda: db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class TestSecret:
    @pytest.mark.asyncio
    async def test_missing_secret_rejected(self, client):
        r = await client.post("/api/v1/webhooks/dmobili", json={"from": "+2348031111111", "message": "hi"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_secret_rejected(self, client):
        r = await client.post(
            "/api/v1/webhooks/dmobili?secret=nope",
            json={"from": "+2348031111111", "message": "hi"},
        )
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_unconfigured_secret_is_503(self, client, monkeypatch):
        monkeypatch.setattr(settings, "DMOBILI_WEBHOOK_SECRET", None, raising=False)
        r = await client.post("/api/v1/webhooks/dmobili", json={"from": "+2348031111111", "message": "hi"})
        assert r.status_code == 503

    @pytest.mark.asyncio
    async def test_get_probe_ok(self, client):
        r = await client.get("/api/v1/webhooks/dmobili")
        assert r.status_code == 200
        assert r.json()["ok"] is True


class TestInbound:
    @pytest.mark.asyncio
    async def test_inbound_lands_in_conversation(self, client, db):
        r = await client.post(
            f"/api/v1/webhooks/dmobili?secret={SECRET}",
            json={"from": "+2348031111111", "to": "2348099999999",
                  "message": "Yes, I am interested", "message_id": "in-42"},
        )
        assert r.status_code == 200
        assert r.json().get("stored") is True

        msgs = (await db.execute(select(Message).where(Message.direction == "incoming"))).scalars().all()
        assert len(msgs) == 1
        assert msgs[0].body == "Yes, I am interested"
        assert msgs[0].provider == "dmobili"
        convs = (await db.execute(select(Conversation))).scalars().all()
        assert len(convs) == 1

    @pytest.mark.asyncio
    async def test_form_encoded_inbound(self, client, db):
        r = await client.post(
            f"/api/v1/webhooks/dmobili?secret={SECRET}",
            data={"sender": "+2348032222222", "text": "form body", "msgid": "in-43"},
        )
        assert r.status_code == 200
        msgs = (await db.execute(select(Message).where(Message.direction == "incoming"))).scalars().all()
        assert len(msgs) == 1
        assert msgs[0].body == "form body"

    @pytest.mark.asyncio
    async def test_duplicate_inbound_not_double_stored(self, client, db):
        payload = {"from": "+2348031111111", "message": "repeat", "message_id": "in-44", "event_id": "evt-1"}
        await client.post(f"/api/v1/webhooks/dmobili?secret={SECRET}", json=payload)
        r = await client.post(f"/api/v1/webhooks/dmobili?secret={SECRET}", json=payload)
        assert r.status_code == 200
        assert r.json().get("duplicate") is True
        msgs = (await db.execute(select(Message).where(Message.direction == "incoming"))).scalars().all()
        assert len(msgs) == 1


class TestDeliveryReports:
    @pytest.mark.asyncio
    async def test_dlr_updates_outgoing_message(self, client, db):
        # Seed an outgoing message as Dmobili sent it.
        from app.models.contact import Contact
        c = Contact(phone_number="+2348031234567", country="Nigeria", lead_status="new", source="manual")
        db.add(c)
        await db.flush()
        conv = Conversation(contact_id=c.id, status="active")
        db.add(conv)
        await db.flush()
        m = Message(conversation_id=conv.id, contact_id=c.id, direction="outgoing",
                    body="hello", status="sent", provider="dmobili",
                    provider_message_id="dm-500", idempotency_key="k1")
        db.add(m)
        await db.commit()

        r = await client.post(
            f"/api/v1/webhooks/dmobili?secret={SECRET}",
            json={"message_id": "dm-500", "status": "DELIVERED"},
        )
        assert r.status_code == 200
        assert r.json().get("matched") is True

        fresh = (await db.execute(select(Message).where(Message.provider_message_id == "dm-500"))).scalar_one()
        assert fresh.status == "delivered"
        assert fresh.delivered_at is not None

    @pytest.mark.asyncio
    async def test_rejected_dlr_marks_failed(self, client, db):
        from app.models.contact import Contact
        c = Contact(phone_number="+2348031234568", country="Nigeria", lead_status="new", source="manual")
        db.add(c)
        await db.flush()
        conv = Conversation(contact_id=c.id, status="active")
        db.add(conv)
        await db.flush()
        m = Message(conversation_id=conv.id, contact_id=c.id, direction="outgoing",
                    body="hello", status="sent", provider="dmobili",
                    provider_message_id="dm-501", idempotency_key="k2")
        db.add(m)
        await db.commit()

        r = await client.post(
            f"/api/v1/webhooks/dmobili?secret={SECRET}",
            json={"message_id": "dm-501", "status": "REJECTED"},
        )
        assert r.status_code == 200
        fresh = (await db.execute(select(Message).where(Message.provider_message_id == "dm-501"))).scalar_one()
        assert fresh.status == "failed"
