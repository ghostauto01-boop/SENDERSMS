"""The SMS inbox on a real PostgreSQL, which is the only place it runs in production.

The production inbox returned a 503 — rendered as *"Database unavailable:
column \"conversations.status\" must appear in the GROUP BY clause or be used in
an aggregate function"* — and the chat list came back empty. The cause was a
query that SQLite accepts and PostgreSQL rejects:

    SELECT coalesce(conversations.status, %s), count(*)
      FROM conversations
     GROUP BY coalesce(conversations.status, %s)     -- a *different* parameter

The two coalesce expressions carry different bind parameters, so PostgreSQL
cannot match them and falls back to checking the bare column. Every SQLite test
stayed green while the whole inbox was down on the deployed database.

These tests boot a real PostgreSQL (pgserver) and drive the real endpoints over
HTTP, so that class of bug fails here instead of in production. They skip when
pgserver is not installed (``pip install pgserver``, or ``-r requirements-dev.txt``).
"""

import hashlib
import hmac
import json
import time

import pytest

pgserver = pytest.importorskip("pgserver")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.security.auth import get_current_user

SECRET = "postgres-inbox-signing-key"


@pytest.fixture(scope="module")
def pg_url(tmp_path_factory):
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-inbox")))
    yield server.get_uri().replace("postgresql://", "postgresql+asyncpg://", 1)
    server.cleanup()


@pytest_asyncio.fixture
async def pg_db(pg_url):
    engine = create_async_engine(pg_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        # Every test's rows (and any DDL it needed) go away with the transaction.
        await session.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_client(pg_db, monkeypatch):
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "SMSGATE_WEBHOOK_SECRET", SECRET, raising=False)
    monkeypatch.setattr(settings, "SMSGATE_WEBHOOK_ALLOW_UNSIGNED", False, raising=False)

    async def _get_db():
        yield pg_db

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="tester", email="t@example.com", password_hash="x",
        role="admin", is_active=True,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()


async def _seed_sms_thread(db, *, phone="+2348030000001", name="Ada", status="active", unread=0):
    contact = Contact(phone_number=phone, first_name=name)
    db.add(contact)
    await db.flush()
    conv = Conversation(
        contact_id=contact.id, channel="sms", status=status, unread_count=unread,
        message_count=1, last_message_preview="hello",
    )
    db.add(conv)
    await db.flush()
    db.add(Message(
        conversation_id=conv.id, contact_id=contact.id, direction="incoming",
        channel="sms", body="Hi there", status="delivered", provider="smsgate",
        idempotency_key=f"pg-{conv.id}",
    ))
    await db.flush()
    return contact, conv


def _sign(raw: bytes, secret: str = SECRET):
    stamp = str(int(time.time()))
    digest = hmac.new(secret.encode(), raw + stamp.encode(), hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Signature": digest, "X-Timestamp": stamp}


def _inbound_envelope(evt_id: str, mid: str, sender: str, body: str) -> dict:
    from datetime import datetime, timezone

    return {
        "deviceId": "dev-1", "event": "sms:received", "id": evt_id,
        "payload": {
            "messageId": mid, "message": body, "sender": sender,
            "recipient": "+2340000000000", "simNumber": 1,
            "receivedAt": datetime.now(timezone.utc).isoformat(),
        },
    }


# ---------------------------------------------------------------------------
# Listing threads: the query that broke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listing_the_sms_inbox_works_on_postgres(pg_client, pg_db):
    """The exact request the inbox page makes. This used to be a 503."""
    await _seed_sms_thread(pg_db, phone="+2348030000001", name="Ada")
    await _seed_sms_thread(pg_db, phone="+2348030000002", name="Chidi", status="unread", unread=2)

    response = await pg_client.get("/api/v1/inbox/conversations")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert {item["contact_name"] for item in body["items"]} == {"Ada", "Chidi"}
    # Per-tab counters come from the same GROUP BY, so they are part of the
    # regression: "all" must equal the sum of the labels that exist.
    assert body["counts"]["all"] == 2
    assert body["counts"]["unread"] == 1
    assert "must appear in the GROUP BY clause" not in response.text


@pytest.mark.asyncio
async def test_threads_written_before_the_columns_existed_are_still_listed(pg_client, pg_db):
    """A database upgraded in place: rows read NULL channel/status.

    ``schema_repair`` adds missing columns nullable, so NULL is a real state on
    production data. The old ``coalesce`` in the GROUP BY existed to cope with
    it; the crash-proof rewrite has to keep coping with it.
    """
    await pg_db.execute(text("ALTER TABLE conversations ALTER COLUMN channel DROP NOT NULL"))
    await pg_db.execute(text("ALTER TABLE conversations ALTER COLUMN status DROP NOT NULL"))
    contact = Contact(phone_number="+2348030000009", first_name="Legacy")
    pg_db.add(contact)
    await pg_db.flush()
    await pg_db.execute(text(
        "INSERT INTO conversations (contact_id, channel, status, message_count, "
        "unread_count, sequence_paused, created_at, updated_at) "
        "VALUES (:cid, NULL, NULL, 0, 0, false, now(), now())"
    ), {"cid": contact.id})
    await pg_db.flush()

    response = await pg_client.get("/api/v1/inbox/conversations")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["contact_name"] for item in body["items"]] == ["Legacy"]
    # NULL is the pre-migration spelling of "active", not a bucket of its own.
    assert body["counts"]["active"] == 1
    assert body["counts"]["all"] == 1
    assert "None" not in body["counts"]


@pytest.mark.asyncio
async def test_status_channel_and_search_filters_run_on_postgres(pg_client, pg_db):
    await _seed_sms_thread(pg_db, phone="+2348030000003", name="Ada", status="active")
    await _seed_sms_thread(pg_db, phone="+2348030000004", name="Chidi", status="unread", unread=1)

    unread = (await pg_client.get("/api/v1/inbox/conversations?status=unread")).json()
    assert [i["contact_name"] for i in unread["items"]] == ["Chidi"]
    # The unread tab must still count threads that only have unread messages.
    assert unread["counts"]["all"] == 2

    searched = (await pg_client.get("/api/v1/inbox/conversations?search=Chidi")).json()
    assert [i["contact_name"] for i in searched["items"]] == ["Chidi"]

    campaigns = await pg_client.get("/api/v1/inbox/campaign-filters")
    assert campaigns.status_code == 200, campaigns.text


# ---------------------------------------------------------------------------
# Receiving SMS: webhook -> contact -> thread -> chat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_signed_inbound_sms_reaches_the_inbox_on_postgres(pg_client, pg_db):
    """The whole receive path, end to end, on the production database."""
    envelope = _inbound_envelope("evt-pg-1", "mid-pg-1", "+2348090000001", "Please send the menu")
    raw = json.dumps(envelope).encode()

    delivered = await pg_client.post("/api/v1/webhooks/smsgateway", content=raw, headers=_sign(raw))
    assert delivered.status_code == 200, delivered.text
    assert delivered.json().get("stored") is True

    listing = await pg_client.get("/api/v1/inbox/conversations")
    assert listing.status_code == 200, listing.text
    mine = [i for i in listing.json()["items"] if i["contact_phone"] == "+2348090000001"]
    assert mine, listing.text

    chat = await pg_client.get(f"/api/v1/inbox/conversations/{mine[0]['id']}")
    assert chat.status_code == 200, chat.text
    assert "Please send the menu" in chat.text

    # The device retries a delivery it never got a 200 for; a repeat must not
    # create a second thread or a duplicated message.
    again = await pg_client.post("/api/v1/webhooks/smsgateway", content=raw, headers=_sign(raw))
    assert again.status_code == 200
    after = (await pg_client.get("/api/v1/inbox/conversations")).json()
    assert len([i for i in after["items"] if i["contact_phone"] == "+2348090000001"]) == 1


@pytest.mark.asyncio
async def test_an_unsigned_delivery_is_refused_on_postgres(pg_client, pg_db):
    envelope = _inbound_envelope("evt-pg-2", "mid-pg-2", "+2348090000002", "let me in")
    response = await pg_client.post("/api/v1/webhooks/smsgateway", json=envelope)
    assert response.status_code == 401, response.text
    listing = (await pg_client.get("/api/v1/inbox/conversations")).json()
    assert not [i for i in listing["items"] if i["contact_phone"] == "+2348090000002"]


@pytest.mark.asyncio
async def test_the_inbox_answers_the_filters_the_ui_sends_on_postgres(pg_client, pg_db):
    """Every query-string combination the Inbox page builds, on PostgreSQL."""
    contact, conv = await _seed_sms_thread(pg_db, phone="+2348030000005", name="Ada", status="interested")
    from app.models.campaign import Campaign, CampaignContact

    campaign = Campaign(name="Pg campaign", status="running", channel="sms")
    pg_db.add(campaign)
    await pg_db.flush()
    conv.campaign_id = campaign.id
    conv.last_campaign_id = campaign.id
    pg_db.add(CampaignContact(campaign_id=campaign.id, contact_id=contact.id, status="replied"))
    await pg_db.flush()

    for query in (
        "?channel=sms",
        "?channel=all",
        "?channel=email",
        "?status=all&channel=sms",
        "?status=interested",
        "?replied_only=true",
        f"?campaign_id={campaign.id}",
        "?per_page=500&page=1",
        "?search=%2B2348030000005",
    ):
        response = await pg_client.get(f"/api/v1/inbox/conversations{query}")
        assert response.status_code == 200, f"{query}: {response.text}"
        assert "Database unavailable" not in response.text, query
