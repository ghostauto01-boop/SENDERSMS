"""Regression: carrier opt-outs must never crash the SMS-Gate webhook.

THE BUG (reported from production logs)
---------------------------------------
    POST /api/v1/webhooks/smsgateway  500
    duplicate key value violates unique constraint
    "ix_suppression_entries_phone_number"
    ... followed by PendingRollbackError on every later statement

Two things were wrong:

1. Inbound "STOP"-style carriers send their opt-out notices from *sender IDs*
   (``AIRTEL-EODN``, ``MTN``). Those are not phone numbers. Nothing is ever
   sent "to AIRTEL-EODN", so putting it in the suppression list has no
   effect — but the second row for the same sender ID did collide with the
   first and raised IntegrityError.
2. That exception escaped mid-transaction, so the session was left aborted and
   the handler's own error bookkeeping failed too, turning a logged warning
   into an HTTP 500 that the device retries for two days.

These tests pin the fixed behaviour: the same carrier event can arrive any
number of times, the webhook still answers 200, and a real phone number that
texts STOP is suppressed exactly once.
"""

import hashlib
import hmac
import json
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact
from app.models.suppression import SuppressionEntry
from app.services.suppression_service import add_suppression, is_diallable, normalise

SECRET = "test-signing-key"


def sign(body: bytes, ts: str) -> str:
    return hmac.new(SECRET.encode(), body + ts.encode(), hashlib.sha256).hexdigest()


def envelope(event: str, payload: dict, evt_id: str) -> dict:
    return {"deviceId": "dev-1", "event": event, "id": evt_id, "webhookId": "wh-1", "payload": payload}


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

    monkeypatch.setattr(settings, "SMSGATE_WEBHOOK_SECRET", SECRET, raising=False)
    monkeypatch.setattr(settings, "SMSGATE_WEBHOOK_ALLOW_UNSIGNED", False, raising=False)

    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def post(client, body: dict):
    raw = json.dumps(body).encode()
    ts = str(int(time.time()))
    return await client.post(
        "/api/v1/webhooks/smsgateway",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Signature": sign(raw, ts),
            "X-Timestamp": ts,
        },
    )


# ---------------------------------------------------------------------------
# The pure helper
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sender,expected",
    [
        ("+2348012345678", True),
        ("08031234567", True),
        ("234 803 123 4567", True),
        ("+44 20 7946 0958", True),
        ("AIRTEL-EODN", False),
        ("MTN", False),
        ("GTBank", False),
        ("32665", False),  # a short code is a sender, never a recipient
        ("", False),
        (None, False),
    ],
)
def test_only_real_numbers_are_suppressible(sender, expected):
    assert is_diallable(sender) is expected


def test_normalise_collapses_formatting():
    assert normalise("0803 123 4567") == normalise("08031234567")
    assert normalise("+234 803 123 4567") == "+2348031234567"


# ---------------------------------------------------------------------------
# The webhook
# ---------------------------------------------------------------------------
async def test_duplicate_carrier_opt_out_does_not_500(client, db):
    """The exact log line from the report: AIRTEL-EODN, twice, five minutes apart."""
    payload = {
        "sender": "AIRTEL-EODN",
        "message": "STOP",
        "recipient": "+2348000000000",
        "messageId": "carrier-1",
        "receivedAt": "2026-02-01T09:00:00.000+01:00",
    }
    first = await post(client, envelope("sms:received", payload, "evt-airtel-1"))
    assert first.status_code == 200, first.text

    payload_again = {**payload, "messageId": "carrier-2", "receivedAt": "2026-02-01T09:05:00.000+01:00"}
    second = await post(client, envelope("sms:received", payload_again, "evt-airtel-2"))
    assert second.status_code == 200, second.text
    # Nothing to suppress: a sender ID is not a recipient.
    total = (await db.execute(select(func.count(SuppressionEntry.id)))).scalar() or 0
    assert total == 0


async def test_repeated_stop_from_a_real_number_is_idempotent(client, db):
    phone = "+2348031234567"
    for index in range(3):
        response = await post(
            client,
            envelope(
                "sms:received",
                {
                    "sender": phone,
                    "message": "STOP",
                    "recipient": "+2348000000000",
                    "messageId": f"stop-{index}",
                    "receivedAt": f"2026-02-01T09:0{index}:00.000+01:00",
                },
                f"evt-stop-{index}",
            ),
        )
        assert response.status_code == 200, response.text

    rows = list((await db.execute(select(SuppressionEntry))).scalars().all())
    assert len(rows) == 1, "a repeated STOP must not create a second suppression row"
    assert normalise(rows[0].phone_number) == normalise(phone)

    contact = (
        await db.execute(select(Contact).where(Contact.phone_number == normalise(phone)))
    ).scalar_one_or_none()
    assert contact is not None and contact.is_opted_out is True


async def test_suppression_helper_is_idempotent(db):
    assert await add_suppression(db, "+2348099999999", reason="first") is True
    assert await add_suppression(db, "+2348099999999", reason="second") is False
    total = (await db.execute(select(func.count(SuppressionEntry.id)))).scalar() or 0
    assert total == 1


async def test_webhook_session_survives_a_processing_failure(client, db, monkeypatch):
    """Any exception mid-processing must leave the session usable.

    Proves the handler rolls back and still records the failure instead of
    letting PendingRollbackError escape as a 500.
    """

    async def boom(self, from_number, body, webhook_data=None):
        raise RuntimeError("simulated carrier payload we cannot parse")

    monkeypatch.setattr("app.services.sms_service.SMSService.process_inbound_message", boom)
    response = await post(
        client,
        envelope(
            "sms:received",
            {"sender": "+2348011112222", "message": "hello", "messageId": "boom-1"},
            "evt-boom-1",
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json().get("error")
    # The session is still usable afterwards (this would raise PendingRollbackError).
    assert (await db.execute(select(func.count(SuppressionEntry.id)))).scalar() == 0
