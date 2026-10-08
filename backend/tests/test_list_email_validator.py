"""The email validator next to the number validator: list + contacts views.

Covers ``POST /lists/{id}/validate-emails`` (the twin of ``/clean``), the
Contacts page channel views (``channel=sms`` / ``channel=email``) and the per-
list selector (``list_id``), plus ``POST /contacts/validate-emails`` for the
global button.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.user import User
from app.security.auth import get_current_user
from app.services import email_validator as ev
from app.services.email_enrichment import VERDICT_DELIVERABLE, VERDICT_UNDELIVERABLE


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
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
        id=1,
        username="tester",
        email="tester@example.com",
        password_hash="x",
        role="admin",
        is_active=True,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _stub_validator(monkeypatch):
    """Deterministic verdicts: anything starting with 'bad' is undeliverable."""

    async def fake_validate(address, *, deep=True):
        if address.lower().startswith("bad"):
            return ev.Verdict(
                address=address, verdict=VERDICT_UNDELIVERABLE,
                is_reachable="invalid", problems=["mailbox rejected by SMTP server"],
            )
        return ev.Verdict(address=address, verdict=VERDICT_DELIVERABLE, is_reachable="safe")

    monkeypatch.setattr(ev, "validate_email", fake_validate)


@pytest_asyncio.fixture
async def sample(db):
    sms_only = Contact(phone_number="+2348012345678", country="Nigeria")
    email_good = Contact(email="ada@example.com", country="Nigeria")
    email_bad = Contact(email="bad@example.com", country="Nigeria")
    both = Contact(phone_number="+2348099988777", email="chuka@example.com", country="Nigeria")
    db.add_all([sms_only, email_good, email_bad, both])
    await db.flush()
    lst = ContactList(name="Lagos prospects")
    db.add(lst)
    await db.flush()
    for c in (email_good, email_bad, both):
        db.add(ContactListMember(list_id=lst.id, contact_id=c.id))
    await db.flush()
    return {"list": lst, "sms_only": sms_only, "good": email_good,
            "bad": email_bad, "both": both}


# ---------------------------------------------------------- list validator


@pytest.mark.asyncio
async def test_validate_emails_in_list_marks_and_unlinks_dead_rows(
    client, db, sample, monkeypatch
):
    _stub_validator(monkeypatch)
    list_id = sample["list"].id
    r = await client.post(f"/api/v1/lists/{list_id}/validate-emails", params={"deep": False})
    assert r.status_code == 200
    data = r.json()
    assert data["scanned"] == 3
    assert data["deliverable"] == 2
    assert data["undeliverable"] == 1
    assert data["engine"] in ("builtin", "reacher")

    await db.refresh(sample["good"])
    await db.refresh(sample["bad"])
    assert sample["good"].email_verified is True
    assert sample["bad"].is_email_undeliverable is True
    assert sample["bad"].email_status == "invalid"

    # The dead address is email-only, so it leaves the list; the dual-channel
    # contact keeps its good phone and stays.
    members = (
        await db.execute(
            ContactListMember.__table__.select().where(
                ContactListMember.list_id == list_id
            )
        )
    ).fetchall()
    member_ids = {row.contact_id for row in members}
    assert sample["bad"].id not in member_ids
    assert sample["good"].id in member_ids
    assert sample["both"].id in member_ids
    assert data["removed_from_list"] == 1


@pytest.mark.asyncio
async def test_validate_emails_in_list_keeps_dual_channel_contacts(
    client, db, sample, monkeypatch
):
    _stub_validator(monkeypatch)

    async def all_bad(address, *, deep=True):
        return ev.Verdict(address=address, verdict=VERDICT_UNDELIVERABLE,
                          is_reachable="invalid")

    monkeypatch.setattr(ev, "validate_email", all_bad)
    list_id = sample["list"].id
    r = await client.post(f"/api/v1/lists/{list_id}/validate-emails", params={"deep": False})
    data = r.json()
    # 'both' has a working phone: quarantined for email but kept in the list.
    members = (
        await db.execute(
            ContactListMember.__table__.select().where(
                ContactListMember.list_id == list_id
            )
        )
    ).fetchall()
    member_ids = {row.contact_id for row in members}
    assert sample["both"].id in member_ids
    assert sample["good"].id not in member_ids
    assert sample["bad"].id not in member_ids
    assert data["removed_from_list"] == 2


# ---------------------------------------------------------- contacts views


@pytest.mark.asyncio
async def test_contacts_channel_views(client, sample):
    r = await client.get("/api/v1/contacts/", params={"channel": "sms"})
    ids = {c["id"] for c in r.json()["items"]}
    assert ids == {sample["sms_only"].id, sample["both"].id}

    r = await client.get("/api/v1/contacts/", params={"channel": "email"})
    ids = {c["id"] for c in r.json()["items"]}
    assert ids == {sample["good"].id, sample["bad"].id, sample["both"].id}

    r = await client.get("/api/v1/contacts/")
    assert r.json()["total"] == 4


@pytest.mark.asyncio
async def test_contacts_list_selector(client, sample):
    r = await client.get("/api/v1/contacts/", params={"list_id": sample["list"].id})
    ids = {c["id"] for c in r.json()["items"]}
    assert ids == {sample["good"].id, sample["bad"].id, sample["both"].id}
    assert sample["sms_only"].id not in ids

    # Combined with the channel view: email contacts inside this list.
    r = await client.get(
        "/api/v1/contacts/", params={"list_id": sample["list"].id, "channel": "email"}
    )
    ids = {c["id"] for c in r.json()["items"]}
    assert ids == {sample["good"].id, sample["bad"].id, sample["both"].id}

    r = await client.get(
        "/api/v1/contacts/", params={"list_id": sample["list"].id, "channel": "sms"}
    )
    ids = {c["id"] for c in r.json()["items"]}
    assert ids == {sample["both"].id}


@pytest.mark.asyncio
async def test_contacts_validate_emails_endpoint(client, db, sample, monkeypatch):
    _stub_validator(monkeypatch)
    r = await client.post(
        "/api/v1/contacts/validate-emails",
        params={"scope": "all", "channel": "email", "deep": False},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["matched"] == 3
    assert data["undeliverable"] == 1
    assert data["deliverable"] == 2

    r = await client.post("/api/v1/contacts/validate-emails", params={"deep": False})
    assert r.status_code == 400  # empty selection must never scan the world
