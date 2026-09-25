"""Password-only login + manual good/bad reply override (opt-out)."""
import pytest
from sqlalchemy import select

from app.models.contact import Contact
from app.models.conversation import Message
from app.models.suppression import SuppressionEntry
from tests.test_inbound_receive import db, client, auth_client, post_webhook, envelope  # noqa: F401


@pytest.fixture(autouse=True)
def _no_rate_limit():
    from app.security.rate_limit import limiter
    limiter.enabled = False
    yield
    limiter.enabled = True


@pytest.mark.asyncio
async def test_login_with_password_only(client):
    r = await client.post("/api/v1/auth/login", json={"password": "12345678"})
    assert r.status_code == 200, r.text
    assert "sendsms_session" in r.headers.get("set-cookie", "")
    me = await client.get("/api/v1/auth/me", cookies={"sendsms_session": r.cookies["sendsms_session"]})
    assert me.status_code == 200


@pytest.mark.asyncio
async def test_login_ignores_username_and_rejects_wrong_password(client):
    assert (await client.post("/api/v1/auth/login", json={"username": "whatever", "password": "12345678"})).status_code == 200
    assert (await client.post("/api/v1/auth/login", json={"password": "wrong"})).status_code == 401


@pytest.mark.asyncio
async def test_login_works_even_if_stale_admin_row_exists(client, db):
    from app.models.user import User
    db.add(User(username="admin", password_hash="stale-hash", role="admin", is_active=True))
    await db.flush()
    assert (await client.post("/api/v1/auth/login", json={"password": "12345678"})).status_code == 200


async def _inbound(client, db):
    await post_webhook(client, envelope("sms:received", {
        "messageId": "fb1", "message": "Stop texting me", "sender": "+2348012345678",
        "recipient": "+2349099999999", "simNumber": 1,
        "receivedAt": "2024-06-22T15:46:11.000+07:00"}, "evt-fb1"))
    return (await db.execute(select(Message).where(Message.direction == "incoming"))).scalar_one()


@pytest.mark.asyncio
async def test_bad_then_good_feedback(auth_client, db):
    msg = await _inbound(auth_client, db)
    r = await auth_client.post(f"/api/v1/inbox/messages/{msg.id}/feedback", json={"verdict": "bad"})
    assert r.status_code == 200, r.text
    assert r.json()["ai_sentiment"] == "negative" and r.json()["is_opted_out"] is True
    contact = (await db.execute(select(Contact))).scalar_one()
    assert contact.is_opted_out
    assert (await db.execute(select(SuppressionEntry))).scalar_one_or_none() is not None

    r = await auth_client.post(f"/api/v1/inbox/messages/{msg.id}/feedback", json={"verdict": "good"})
    assert r.status_code == 200
    assert r.json()["ai_sentiment"] == "positive" and r.json()["is_opted_out"] is False
    await db.refresh(contact)
    assert contact.lead_status == "interested"
    assert (await db.execute(select(SuppressionEntry))).scalar_one_or_none() is None
