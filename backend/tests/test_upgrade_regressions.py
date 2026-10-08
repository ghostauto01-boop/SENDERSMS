"""Focused regressions for the compatibility-preserving upgrade paths."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact
from app.models.contact_list import ContactList
from app.models.user import User
from app.security.auth import get_current_user


@pytest_asyncio.fixture
async def test_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session, factory
    await engine.dispose()


@pytest_asyncio.fixture
async def client(test_db, monkeypatch):
    from app import database
    from app.main import app

    db, factory = test_db
    monkeypatch.setattr(database, "async_session_factory", factory)

    async def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1,
        username="admin",
        email="admin@example.test",
        password_hash="test",
        role="admin",
        is_active=True,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_enrichment_accepts_typed_json_and_legacy_query(client, test_db, monkeypatch):
    from app.services import email_enrichment

    db, _factory = test_db
    selected = Contact(phone_number="+2348031234567", email=None, first_name="Body")
    filtered = Contact(phone_number="+2348031234568", email=None, first_name="Query")
    db.add_all([selected, filtered])
    await db.flush()
    calls = []

    async def fake_enrich(_db, contacts, *, allow_inferred=None):
        calls.append(([contact.id for contact in contacts], allow_inferred))
        return {"success": True, "processed": len(contacts), "items": []}

    monkeypatch.setattr(email_enrichment, "enrich_contacts", fake_enrich)

    body_response = await client.post(
        "/api/v1/contacts/enrich",
        json={"contact_ids": [selected.id], "allow_inferred": True, "limit": 25},
    )
    assert body_response.status_code == 200, body_response.text
    assert body_response.json()["matched"] == 1
    assert calls[-1] == ([selected.id], True)

    query_response = await client.post(
        "/api/v1/contacts/enrich",
        params={"scope": "no_email", "search": "Query", "limit": 10},
    )
    assert query_response.status_code == 200, query_response.text
    assert query_response.json()["matched"] == 1
    assert calls[-1][0] == [filtered.id]


@pytest.mark.asyncio
async def test_enrichment_body_is_typed_and_errors_are_structured(client):
    response = await client.post(
        "/api/v1/contacts/enrich",
        json={"contact_ids": [0], "unrecognized": True},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["request_id"] == response.headers["x-request-id"]
    assert body["field"]
    assert "detail" in body  # legacy error consumer compatibility


@pytest.mark.asyncio
async def test_csv_import_job_persists_progress_and_result(client, test_db):
    db, _factory = test_db
    contact_list = ContactList(name="Queued leads")
    db.add(contact_list)
    await db.flush()

    response = await client.post(
        "/api/v1/contacts/import/jobs",
        files={"file": ("queued.csv", b"phone,first_name,email\n08031112222,Ada,ada@acme.ng\ngarbage,No,not-an-email\n08032223333,Chidi,chidi@acme.ng\n", "text/csv")},
        data={"list_id": str(contact_list.id), "tags": "prospect"},
    )
    assert response.status_code == 202, response.text
    created = response.json()
    status_response = await client.get(f"/api/v1/contacts/import/jobs/{created['id']}")
    assert status_response.status_code == 200, status_response.text
    result = status_response.json()
    assert result["status"] == "completed"
    assert result["processed_rows"] == 3
    assert result["progress_percent"] == 100
    assert result["result"]["imported"] == 2
    assert result["result"]["invalid"] == 1
    assert result["result"]["new_variables"] >= 0
    assert result["list"]["name"] == "Queued leads"

    imported_contacts = list((await db.execute(select(Contact).order_by(Contact.id))).scalars().all())
    assert len(imported_contacts) == 2
    assert [contact.country for contact in imported_contacts] == ["Nigeria", "Nigeria"]

    error_csv = await client.get(f"/api/v1/contacts/import/jobs/{created['id']}/errors.csv")
    assert error_csv.status_code == 200
    assert "row,level,error" in error_csv.text
    assert "No phone number and no email address" in error_csv.text


@pytest.mark.asyncio
async def test_ads_preflight_is_advisory_and_launch_is_idempotent(client, test_db):
    from app.models.ads import AdsAssignment, AdsCampaign, AdsCreative, AdsSet
    from app.models.contact_list import ContactListMember

    db, _factory = test_db
    contact = Contact(phone_number="+2348031234567", first_name="Launch")
    contact_list = ContactList(name="Launch audience")
    db.add_all([contact, contact_list])
    await db.flush()
    db.add(ContactListMember(list_id=contact_list.id, contact_id=contact.id))
    campaign = AdsCampaign(name="Advisory launch", objective="replies", status="draft", test_mode=True)
    db.add(campaign)
    await db.flush()
    ads_set = AdsSet(campaign_id=campaign.id, name="Set", status="active", list_ids=str(contact_list.id))
    db.add(ads_set)
    await db.flush()
    db.add(AdsCreative(set_id=ads_set.id, campaign_id=campaign.id, name="Creative", body="Hello", status="active"))
    await db.flush()

    preflight = await client.post(f"/api/v1/ads/campaigns/{campaign.id}/validate")
    assert preflight.status_code == 200
    assert preflight.json()["ok"] is True
    assert preflight.json()["errors"] == []

    first = await client.post(f"/api/v1/ads/campaigns/{campaign.id}/launch")
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "active"
    assert first.json()["audience"]["added"] == 1
    second = await client.post(f"/api/v1/ads/campaigns/{campaign.id}/launch")
    assert second.status_code == 200, second.text
    assert second.json()["idempotent"] is True
    assigned = (await db.execute(select(func.count(AdsAssignment.id)))).scalar_one()
    assert assigned == 1


@pytest.mark.asyncio
async def test_list_contacts_reuses_contact_shape_without_removing_pagination_keys(client, test_db):
    db, _factory = test_db
    contact_list = ContactList(name="Full shape")
    contact = Contact(
        phone_number="+2348031234567", email="ada@example.test",
        first_name="Ada", city="Lagos", country=None,
    )
    db.add_all([contact_list, contact])
    await db.flush()
    from app.models.contact_list import ContactListMember
    db.add(ContactListMember(list_id=contact_list.id, contact_id=contact.id))
    await db.flush()

    response = await client.get(f"/api/v1/lists/{contact_list.id}/contacts")
    assert response.status_code == 200, response.text
    body = response.json()
    assert {"total", "page", "per_page", "items"}.issubset(body)
    item = body["items"][0]
    assert item["id"] == contact.id
    assert item["email"] == "ada@example.test"
    assert item["country"] is None
    assert isinstance(item["tags"], list)


@pytest.mark.asyncio
async def test_multiple_reply_to_addresses_persist_without_changing_primary_from(client):
    created = await client.post(
        "/api/v1/email/accounts",
        json={
            "name": "Replies",
            "from_name": "Sender",
            "from_email": "sender@example.test",
            "reply_to": "replies@example.test, team@example.test",
            "api_key": "fake-key-for-schema-test",
        },
    )
    assert created.status_code == 201, created.text
    account = created.json()
    assert account["from_email"] == "sender@example.test"
    assert account["reply_to"] == "replies@example.test"
    assert account["reply_to_addresses"] == ["replies@example.test", "team@example.test"]

    patched = await client.patch(
        f"/api/v1/email/accounts/{account['id']}",
        json={"reply_to": ["new@example.test", "team@example.test"]},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["from_email"] == "sender@example.test"
    assert patched.json()["reply_to"] == "new@example.test"
    assert patched.json()["reply_to_addresses"] == ["new@example.test", "team@example.test"]


def test_reply_to_schema_keeps_legacy_single_and_accepts_multiple_addresses():
    from app.schemas.email import EmailAccountIn

    common = {"name": "Main", "from_name": "Sender", "from_email": "sender@example.test"}
    single = EmailAccountIn(**common, reply_to="replies@example.test")
    multiple = EmailAccountIn(
        **common,
        reply_to="Replies@example.test, team@example.test, replies@example.test",
    )
    assert single.reply_to == ["replies@example.test"]
    assert multiple.reply_to == ["replies@example.test", "team@example.test"]


def test_sensitive_inbound_classifier_is_narrow():
    from app.services.inbound_safety import sensitive_inbound_reason

    assert sensitive_inbound_reason("Your verification code is 482913") == "one_time_code"
    assert sensitive_inbound_reason("Card 4111 1111 1111 1111") == "payment_card_number"
    assert sensitive_inbound_reason("Please stop by tomorrow") is None
    assert sensitive_inbound_reason("I am interested in your offer") is None


@pytest.mark.asyncio
async def test_sensitive_webhook_content_is_not_persisted(client, test_db, monkeypatch):
    import hashlib
    import hmac
    import time

    from app.config import settings
    from app.models.conversation import Conversation, Message
    from app.models.webhook import WebhookEvent

    db, _factory = test_db
    secret = "privacy-regression-secret"
    monkeypatch.setattr(settings, "SMSGATE_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(settings, "SMSGATE_WEBHOOK_ALLOW_UNSIGNED", False)
    timestamp = str(int(time.time()))
    raw = (
        b'{"event":"sms:received","id":"privacy-event-1","deviceId":"dev-1",'
        b'"payload":{"messageId":"msg-1","sender":"+2348031234567",'
        b'"message":"Your verification code is 482913","receivedAt":"2026-01-01T00:00:00Z"}}'
    )
    signature = hmac.new(secret.encode(), raw + timestamp.encode(), hashlib.sha256).hexdigest()
    response = await client.post(
        "/api/v1/webhooks/smsgateway",
        content=raw,
        headers={"content-type": "application/json", "x-signature": signature, "x-timestamp": timestamp},
    )
    assert response.status_code == 200, response.text
    assert response.json()["suppressed"] is True
    event = (await db.execute(select(WebhookEvent))).scalar_one()
    assert event.status == "suppressed"
    assert "482913" not in (event.payload or "")
    assert "verification code" not in (event.payload or "").lower()
    assert (await db.execute(select(Contact))).scalars().all() == []
    assert (await db.execute(select(Conversation))).scalars().all() == []
    assert (await db.execute(select(Message))).scalars().all() == []
