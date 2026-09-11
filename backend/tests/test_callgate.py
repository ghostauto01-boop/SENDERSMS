"""CallGate (phone calls): provider, config, start-call and webhooks."""

import hashlib
import hmac
import json
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.call import CallLog
from app.models.contact import Contact

SECRET = "test-call-signing-key"


def sign(body: bytes, ts: str) -> str:
    return hmac.new(SECRET.encode(), body + ts.encode(), hashlib.sha256).hexdigest()


def envelope(event: str, payload: dict, evt_id: str) -> dict:
    return {"deviceId": "dev-1", "event": event, "id": evt_id,
            "webhookId": "wh-1", "payload": payload}


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
    from app.config import settings
    monkeypatch.setattr(settings, "CALLGATE_WEBHOOK_SECRET", SECRET, raising=False)
    monkeypatch.setattr(settings, "SMSGATE_WEBHOOK_ALLOW_UNSIGNED", False, raising=False)

    from app.main import app
    app.dependency_overrides[get_db] = lambda: db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_client(client):
    from app.main import app
    from app.security.auth import get_current_user
    from app.models.user import User

    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="tester", email="t@example.com", password_hash="x",
        role="admin", is_active=True,
    )
    yield client
    app.dependency_overrides.pop(get_current_user, None)


async def post_call_webhook(client, body: dict, secret: str = SECRET):
    raw = json.dumps(body).encode()
    ts = str(int(time.time()))
    return await client.post(
        "/api/v1/webhooks/callgate",
        content=raw,
        headers={"Content-Type": "application/json",
                 "X-Signature": sign(raw, ts) if secret else "bad",
                 "X-Timestamp": ts},
    )


class TestProviderHelpers:
    def test_base_normalization(self):
        from app.providers.callgate import _base
        assert _base("192.168.1.5") == "http://192.168.1.5/api/v1"
        assert _base("192.168.1.5:8084") == "http://192.168.1.5:8084/api/v1"
        assert _base("http://192.168.1.5:8084/api/v1") == "http://192.168.1.5:8084/api/v1"
        assert _base("") == ""

    def test_is_configured(self):
        from app.providers.callgate import is_configured
        assert is_configured("http://1.2.3.4:8084/api/v1", "u", "p") is True
        assert is_configured("", "u", "p") is False
        assert is_configured("http://1.2.3.4:8084/api/v1", "", "p") is False

    @pytest.mark.asyncio
    async def test_start_call_no_config(self):
        from app.providers.callgate import start_call_direct
        r = await start_call_direct("+2348031234567", base_url="", username="", password="")
        assert r["success"] is False
        assert "not configured" in r["error"]


class TestCallConfig:
    @pytest.mark.asyncio
    async def test_save_and_read_config(self, auth_client):
        r = await auth_client.put("/api/v1/calls/config", json={
            "base_url": "192.168.1.5:8084", "username": "admin",
            "password": "s3cret", "dial_mode": "callgate",
        })
        assert r.status_code == 200, r.text
        assert r.json()["configured"] is True

        r = await auth_client.get("/api/v1/calls/config")
        assert r.status_code == 200
        data = r.json()
        assert data["username"] == "admin"
        assert data["password_set"] is True
        # Password itself must never leak through the API.
        assert "s3cret" not in r.text

    @pytest.mark.asyncio
    async def test_bad_dial_mode_rejected(self, auth_client):
        r = await auth_client.put("/api/v1/calls/config", json={"dial_mode": "smoke"})
        assert r.status_code == 400


class TestStartCall:
    @pytest.mark.asyncio
    async def test_direct_mode_returns_tel_url(self, auth_client, db):
        c = Contact(phone_number="+2348031234567", first_name="Ada", country="Nigeria")
        db.add(c)
        await db.flush()

        await auth_client.put("/api/v1/calls/config", json={"dial_mode": "direct"})
        r = await auth_client.post("/api/v1/calls/start", json={"contact_id": c.id})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["success"] is True
        assert data["mode"] == "direct"
        assert data["tel_url"] == "tel:+2348031234567"

        log = (await db.execute(select(CallLog))).scalar_one()
        assert log.contact_id == c.id
        assert log.status == "direct_dial"

    @pytest.mark.asyncio
    async def test_unconfigured_falls_back_to_direct(self, auth_client, db):
        r = await auth_client.post("/api/v1/calls/start",
                                   json={"phone_number": "+2348031234567"})
        assert r.status_code == 200
        assert r.json()["mode"] == "direct"
        assert r.json()["tel_url"].startswith("tel:")

    @pytest.mark.asyncio
    async def test_missing_contact_404(self, auth_client):
        r = await auth_client.post("/api/v1/calls/start", json={"contact_id": 99999})
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_call_logs_list(self, auth_client, db):
        db.add(CallLog(phone_number="+2348031234567", direction="outgoing", status="ended"))
        await db.flush()
        r = await auth_client.get("/api/v1/calls/logs")
        assert r.status_code == 200
        assert r.json()["total"] == 1


class TestCallWebhooks:
    @pytest.mark.asyncio
    async def test_bad_signature_rejected(self, client):
        r = await post_call_webhook(client, envelope("call:started", {"phoneNumber": "0803"}, "e1"),
                                    secret="")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_ringing_started_ended_flow(self, client, db):
        db.add(Contact(phone_number="+2348031234567", first_name="Ada", country="Nigeria"))
        await db.flush()

        r = await post_call_webhook(client, envelope("call:ringing",
                                                     {"phoneNumber": "+2348031234567"}, "e-ring"))
        assert r.status_code == 200
        row = (await db.execute(select(CallLog))).scalar_one()
        assert row.status == "ringing"

        r = await post_call_webhook(client, envelope("call:started",
                                                     {"phoneNumber": "+2348031234567"}, "e-start"))
        assert r.status_code == 200
        await db.refresh(row)
        assert row.status == "started"
        assert row.started_at is not None

        r = await post_call_webhook(client, envelope("call:ended",
                                                     {"phoneNumber": "+2348031234567"}, "e-end"))
        assert r.status_code == 200
        await db.refresh(row)
        assert row.status == "ended"
        assert row.ended_at is not None
        assert row.duration_seconds is not None

    @pytest.mark.asyncio
    async def test_duplicate_delivery_ignored(self, client, db):
        body = envelope("call:ended", {"phoneNumber": "+2348031234567"}, "e-dup")
        assert (await post_call_webhook(client, body)).status_code == 200
        r = await post_call_webhook(client, body)
        assert r.json().get("duplicate") is True
        rows = (await db.execute(select(CallLog))).scalars().all()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_unknown_event_acknowledged(self, client):
        r = await post_call_webhook(client, envelope("call:whatever", {}, "e-x"))
        assert r.status_code == 200
        assert r.json()["ok"] is True
