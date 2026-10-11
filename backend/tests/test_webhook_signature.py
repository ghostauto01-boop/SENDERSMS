"""Tests for SMS-Gate webhook signature verification.

Regression cover for the unauthenticated-webhook vulnerability: anyone could
POST a forged sms:received event and inject fake conversations.
"""

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
from app.models.webhook import WebhookEvent
from app.providers.smsgate import SMSGateProvider

SECRET = "test-signing-key"


def sign(body: bytes, timestamp: str, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body + timestamp.encode(), hashlib.sha256).hexdigest()


class TestWebhookSignature:
    def test_valid_signature_accepted(self):
        body = b'{"event":"sms:received","payload":{"message":"hi"}}'
        ts = str(int(time.time()))
        assert SMSGateProvider.validate_webhook_signature(body, sign(body, ts), ts, SECRET)

    def test_tampered_body_rejected(self):
        body = b'{"event":"sms:received","payload":{"message":"hi"}}'
        ts = str(int(time.time()))
        signature = sign(body, ts)
        tampered = b'{"event":"sms:received","payload":{"message":"INJECTED"}}'
        assert not SMSGateProvider.validate_webhook_signature(tampered, signature, ts, SECRET)

    def test_wrong_secret_rejected(self):
        body = b'{"event":"system:ping"}'
        ts = str(int(time.time()))
        signature = sign(body, ts, "attacker-guess")
        assert not SMSGateProvider.validate_webhook_signature(body, signature, ts, SECRET)

    def test_missing_signature_rejected(self):
        body = b'{"event":"system:ping"}'
        ts = str(int(time.time()))
        assert not SMSGateProvider.validate_webhook_signature(body, "", ts, SECRET)

    def test_missing_secret_rejected(self):
        body = b'{"event":"system:ping"}'
        ts = str(int(time.time()))
        assert not SMSGateProvider.validate_webhook_signature(body, sign(body, ts), ts, "")

    def test_replayed_old_timestamp_rejected(self):
        """A captured-and-replayed request must not stay valid forever."""
        body = b'{"event":"sms:received"}'
        old = str(int(time.time()) - 3600)
        assert not SMSGateProvider.validate_webhook_signature(body, sign(body, old), old, SECRET)

    def test_future_timestamp_rejected(self):
        body = b'{"event":"sms:received"}'
        future = str(int(time.time()) + 3600)
        assert not SMSGateProvider.validate_webhook_signature(
            body, sign(body, future), future, SECRET
        )

    def test_non_numeric_timestamp_rejected(self):
        body = b'{"event":"sms:received"}'
        assert not SMSGateProvider.validate_webhook_signature(body, "deadbeef", "not-a-time", SECRET)

    def test_signature_is_case_insensitive_hex(self):
        body = b'{"event":"system:ping"}'
        ts = str(int(time.time()))
        assert SMSGateProvider.validate_webhook_signature(
            body, sign(body, ts).upper(), ts, SECRET
        )

    def test_string_body_accepted(self):
        """Callers passing str instead of bytes should still verify."""
        body = '{"event":"system:ping"}'
        ts = str(int(time.time()))
        signature = sign(body.encode(), ts)
        assert SMSGateProvider.validate_webhook_signature(body, signature, ts, SECRET)


# ---------------------------------------------------------------------------
# Refused deliveries must be visible
# ---------------------------------------------------------------------------
#
# The signature gate fails closed, which is right — but a deployment whose
# SMSGATE_WEBHOOK_SECRET was never set answers 503 to every delivery it
# receives, and the inbox stays empty with nothing in the app to explain it.
# These tests pin the fix: the refusal is recorded (bounded, without the
# unverified content) so "no SMS arrive" is diagnosable from the app itself.


@pytest_asyncio.fixture
async def webhook_env(monkeypatch):
    """In-memory database + the real app, with the signing key configurable."""
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "SMSGATE_WEBHOOK_SECRET", "", raising=False)
    monkeypatch.setattr(settings, "SMSGATE_WEBHOOK_ALLOW_UNSIGNED", False, raising=False)
    monkeypatch.setattr(settings, "APP_ENV", "production", raising=False)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    async def _get_db():
        yield session

    app.dependency_overrides[get_db] = _get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, session, settings
    app.dependency_overrides.clear()
    await session.close()
    await engine.dispose()


def _delivery() -> dict:
    return {"deviceId": "dev-1", "event": "sms:received", "id": "evt-x",
            "payload": {"messageId": "m-x", "message": "STOP", "sender": "+2348000000000"}}


@pytest.mark.asyncio
async def test_a_missing_signing_key_is_recorded_not_silently_dropped(webhook_env):
    client, session, settings = webhook_env

    response = await client.post("/api/v1/webhooks/smsgateway", json=_delivery())
    assert response.status_code == 503
    assert "signing not configured" in response.text.lower()

    events = (await session.execute(select(WebhookEvent))).scalars().all()
    assert len(events) == 1
    event = events[0]
    assert event.status == "rejected"
    assert event.event_type == "sms:received"
    assert "SMSGATE_WEBHOOK_SECRET" in event.error
    # Nothing from the unverified body is kept, and no message is created.
    assert "STOP" not in (event.payload or "")
    assert event.payload and "redacted" in event.payload

    # A device that keeps retrying (or a stranger hammering the endpoint) must
    # not be able to fill the table: one row per window, not one per request.
    for _ in range(3):
        await client.post("/api/v1/webhooks/smsgateway", json=_delivery())
    assert len((await session.execute(select(WebhookEvent))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_a_bad_signature_is_recorded_with_the_actionable_reason(webhook_env):
    client, session, settings = webhook_env
    settings.SMSGATE_WEBHOOK_SECRET = SECRET

    raw = json.dumps(_delivery()).encode()
    response = await client.post(
        "/api/v1/webhooks/smsgateway", content=raw,
        headers={"Content-Type": "application/json", "X-Signature": "0" * 64,
                 "X-Timestamp": str(int(time.time()))},
    )
    assert response.status_code == 401

    events = (await session.execute(select(WebhookEvent))).scalars().all()
    assert [e.status for e in events] == ["rejected"]
    assert "Signing Key" in events[0].error


@pytest.mark.asyncio
async def test_a_valid_delivery_is_not_recorded_as_rejected(webhook_env):
    client, session, settings = webhook_env
    settings.SMSGATE_WEBHOOK_SECRET = SECRET

    raw = json.dumps(_delivery()).encode()
    stamp = str(int(time.time()))
    response = await client.post(
        "/api/v1/webhooks/smsgateway", content=raw,
        headers={"Content-Type": "application/json", "X-Signature": sign(raw, stamp),
                 "X-Timestamp": stamp},
    )
    assert response.status_code == 200, response.text
    events = (await session.execute(select(WebhookEvent))).scalars().all()
    assert [e.status for e in events] == ["processed"]
