"""Standalone Validator API: full scope, previews, bounded batches, safe saves."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.email import EmailSuppression
from app.models.user import User
from app.security.auth import get_current_user
from app.services import email_validator as ev
from app.api.v1.validator import BATCH_SIZE


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db):
    from app.main import app

    async def override():
        yield db

    app.dependency_overrides[get_db] = override
    app.dependency_overrides[get_current_user] = lambda: User(id=1, username="validator-test", role="admin", is_active=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def fake_email(monkeypatch):
    async def validate(address, *, deep=True):
        state = {"good": "deliverable", "bad": "undeliverable", "risk": "risky"}.get(address.split("@")[0], "unknown")
        return ev.Verdict(address=address, verdict=state, is_valid_syntax=True, problems=[f"Test {state}"])
    monkeypatch.setattr(ev, "validate_email", validate)


@pytest.mark.asyncio
async def test_status_does_not_expose_secrets(client, monkeypatch):
    monkeypatch.setattr(ev.settings, "REACHER_API_URL", "https://secret-url.invalid?key=private-value")
    monkeypatch.setattr(ev.settings, "REACHER_API_KEY", "private-api-key")
    response = await client.get("/api/v1/validator/status")
    assert response.status_code == 200
    assert response.json()["api_key_required"] is False
    assert response.json()["engine"] == "reacher"
    assert response.json()["reacher_key_configured"] is True
    assert "private" not in response.text and "secret-url" not in response.text


@pytest.mark.asyncio
async def test_self_test_is_real_and_network_free(client, monkeypatch):
    import dns.resolver
    monkeypatch.setattr(dns.resolver, "resolve", lambda *a, **k: pytest.fail("self-test must not use live DNS"))
    response = await client.post("/api/v1/validator/self-test")
    assert response.status_code == 200
    assert response.json()["passed"] is True
    assert len(response.json()["checks"]) == 4
    assert "does not test Reacher" in response.json()["notice"]


@pytest.mark.asyncio
async def test_selection_includes_every_page_and_phone_only_contacts(client, db):
    db.add_all([Contact(email=f"person{i}@example.com") for i in range(605)] + [Contact(phone_number="+2348034567891")])
    await db.flush()
    response = await client.post("/api/v1/validator/selection", json={"scope": "all"})
    data = response.json()
    assert data["total"] == 606
    assert len(data["contact_ids"]) == 606
    assert data["contact_ids"] == sorted(set(data["contact_ids"]))


@pytest.mark.asyncio
async def test_list_and_contact_filters_match_the_view(client, db):
    good = Contact(first_name="Ada", email="good@example.com", lead_status="interested")
    phone = Contact(first_name="Ada", phone_number="+2348034567891", lead_status="interested")
    other = Contact(first_name="Ada", email="outside@example.com", lead_status="interested")
    lst = ContactList(name="Current list")
    db.add_all([good, phone, other, lst])
    await db.flush()
    db.add_all([ContactListMember(list_id=lst.id, contact_id=c.id) for c in [good, phone]])
    await db.flush()
    response = await client.post("/api/v1/validator/selection", json={"scope": "list", "list_id": lst.id, "channel": "email", "search": "Ada", "lead_status": "interested", "email_state": "unverified"})
    assert response.json()["contact_ids"] == [good.id]
    response = await client.post("/api/v1/validator/selection", json={"scope": "ids", "contact_ids": [phone.id], "search": "not a match"})
    assert response.json()["contact_ids"] == [phone.id]
    assert (await client.post("/api/v1/validator/selection", json={"scope": "list"})).status_code == 400
    assert (await client.post("/api/v1/validator/selection", json={"scope": "list", "list_id": 99999})).status_code == 404
    assert (await client.post("/api/v1/validator/selection", json={"scope": "ids"})).status_code == 400


@pytest.mark.asyncio
async def test_raw_single_or_csv_preserves_invalid_and_missing_rows(client, db):
    response = await client.post("/api/v1/validator/batch", json={"deep": False, "items": [
        {"email": "not an email", "phone_number": "123", "row": 1, "name": "Invalid"},
        {"phone_number": "08034567891", "row": 2},
        {"name": "Empty row", "row": 3},
    ]})
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert [r["status"] for r in rows] == ["bad", "good", "missing"]
    assert rows[0]["input_email"] == "not an email"
    assert rows[0]["email"]["verdict"] == "undeliverable"
    assert rows[1]["phone"]["normalized"] == "+2348034567891"
    assert rows[2]["email"]["verdict"] == "missing"
    assert all(not r["saved"] for r in rows)
    assert (await db.execute(select(func.count(Contact.id)))).scalar() == 0


@pytest.mark.asyncio
async def test_default_is_read_only_and_every_verdict_is_returned(client, db, fake_email):
    contacts = [Contact(email=f"{prefix}@example.com") for prefix in ["good", "bad", "risk", "maybe"]]
    db.add_all(contacts)
    await db.commit()
    response = await client.post("/api/v1/validator/batch", json={"contact_ids": [c.id for c in contacts], "check": "email"})
    assert response.status_code == 200, response.text
    assert [r["status"] for r in response.json()["items"]] == ["good", "bad", "risky", "unknown"]
    assert all(not c.email_verified and not c.is_email_undeliverable and c.email_enriched_at is None for c in contacts)


@pytest.mark.asyncio
async def test_save_marks_only_confirmed_bad_and_never_unsubscribes_or_deletes(client, db, fake_email):
    contacts = [Contact(email=f"{prefix}@example.com", is_email_opted_out=True) for prefix in ["good", "bad", "risk", "maybe"]]
    lst = ContactList(name="Keep membership")
    db.add_all(contacts + [lst])
    await db.flush()
    db.add_all([ContactListMember(list_id=lst.id, contact_id=c.id) for c in contacts])
    db.add(EmailSuppression(email_address="good@example.com", reason="unsubscribed"))
    await db.commit()
    response = await client.post("/api/v1/validator/batch", json={"contact_ids": [c.id for c in contacts], "save": True, "check": "email"})
    assert response.status_code == 200, response.text
    good, bad, risk, maybe = contacts
    assert good.email_verified and not bad.email_verified
    assert bad.is_email_undeliverable and bad.email_status == "invalid"
    assert not risk.is_email_undeliverable and not maybe.is_email_undeliverable
    assert all(c.is_email_opted_out for c in contacts)
    assert "Email suppressed" in response.json()["items"][0]["blocked"]
    assert "Email unsubscribed" in response.json()["items"][0]["blocked"]
    assert (await db.execute(select(func.count(ContactListMember.id)))).scalar() == 4
    assert (await db.execute(select(func.count(Contact.id)))).scalar() == 4


@pytest.mark.asyncio
async def test_phone_check_never_clears_existing_blocks_and_skips_missing(client, db):
    contacts = [Contact(phone_number="+2348034567891", is_undeliverable=True, is_opted_out=True), Contact(phone_number="123"), Contact(email="unverified@example.com")]
    db.add_all(contacts)
    await db.commit()
    response = await client.post("/api/v1/validator/batch", json={"contact_ids": [c.id for c in contacts], "check": "phone", "save": True})
    assert response.status_code == 200, response.text
    assert contacts[0].is_undeliverable and contacts[0].is_opted_out
    assert contacts[1].is_undeliverable
    assert not contacts[2].is_undeliverable
    assert response.json()["items"][2]["status"] == "missing"


@pytest.mark.asyncio
async def test_deleted_id_does_not_drop_a_result(client):
    response = await client.post("/api/v1/validator/batch", json={"contact_ids": [999]})
    assert response.json()["processed"] == 1
    assert response.json()["items"][0]["status"] == "unknown"
    assert "deleted" in response.json()["items"][0]["note"]


@pytest.mark.asyncio
async def test_edited_address_does_not_get_old_verdict(client, db, monkeypatch):
    from sqlalchemy import update
    contact = Contact(email="old@example.com")
    db.add(contact)
    await db.commit()

    async def validate(address, *, deep=True):
        await db.execute(update(Contact).where(Contact.id == contact.id).values(email="new@example.com"))
        await db.commit()
        return ev.Verdict(address=address, verdict="undeliverable")
    monkeypatch.setattr(ev, "validate_email", validate)
    response = await client.post("/api/v1/validator/batch", json={"contact_ids": [contact.id], "save": True})
    assert response.status_code == 200
    assert not response.json()["items"][0]["saved"]
    assert "changed" in response.json()["items"][0]["note"]
    assert not contact.is_email_undeliverable


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {}, {"items": [{}], "save": True}, {"items": [{}], "contact_ids": [1]},
    {"contact_ids": [1, 1]}, {"items": [{}] * (BATCH_SIZE + 1)}, {"contact_ids": [-1]},
    {"items": [{"email": "a" * 1001}]}, {"items": [{}], "check": "everything"},
])
async def test_invalid_or_ambiguous_requests_are_rejected(client, payload):
    assert (await client.post("/api/v1/validator/batch", json=payload)).status_code == 422


@pytest.mark.asyncio
async def test_all_validator_routes_require_auth(client):
    from app.main import app
    app.dependency_overrides.pop(get_current_user)
    for path, method in [("status", "get"), ("self-test", "post"), ("selection", "post"), ("batch", "post")]:
        response = await getattr(client, method)(f"/api/v1/validator/{path}")
        assert response.status_code in {401, 403}, response.text
