"""Tests for the server-scoped bulk add: POST /lists/{id}/contacts/add-all."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.user import User
from app.security.auth import get_current_user


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


async def _contacts(db, *phones, lead_status="new"):
    rows = [Contact(phone_number=p, lead_status=lead_status) for p in phones]
    db.add_all(rows)
    await db.flush()
    return rows


async def _list(db, name="Camp List"):
    contact_list = ContactList(name=name)
    db.add(contact_list)
    await db.flush()
    return contact_list


@pytest.mark.asyncio
async def test_add_all_without_filters_adds_every_contact_once(db, client):
    await _contacts(db, "+2348010000001", "+2348010000002", "+2348010000003")
    contact_list = await _list(db)

    r = await client.post(f"/api/v1/lists/{contact_list.id}/contacts/add-all", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == 3
    assert body["contact_count"] == 3

    # Re-running the same add must be idempotent (no duplicate memberships).
    r2 = await client.post(f"/api/v1/lists/{contact_list.id}/contacts/add-all", json={})
    assert r2.json()["added"] == 0
    assert r2.json()["contact_count"] == 3


@pytest.mark.asyncio
async def test_add_all_honors_search_and_excludes_existing_members(db, client):
    await _contacts(db, "+2348010000011", "+2348010000012")
    lagos = Contact(phone_number="+2348010000013", business_name="Lagos Kitchen", lead_status="new")
    db.add(lagos)
    await db.flush()
    contact_list = await _list(db)
    db.add(ContactListMember(list_id=contact_list.id, contact_id=lagos.id))
    await db.flush()

    r = await client.post(
        f"/api/v1/lists/{contact_list.id}/contacts/add-all",
        json={"search": "Lagos Kitchen"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # The one matching contact is already a member, so nothing is added.
    assert body["matched"] == 1
    assert body["added"] == 0


@pytest.mark.asyncio
async def test_add_all_filters_by_lead_status(db, client):
    await _contacts(db, "+2348010000021", "+2348010000022")
    customer = Contact(phone_number="+2348010000023", lead_status="customer")
    db.add(customer)
    await db.flush()
    contact_list = await _list(db)

    r = await client.post(
        f"/api/v1/lists/{contact_list.id}/contacts/add-all",
        json={"lead_status": "customer"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 1
    assert r.json()["contact_count"] == 1


@pytest.mark.asyncio
async def test_add_all_to_missing_list_returns_404(db, client):
    r = await client.post("/api/v1/lists/99999/contacts/add-all", json={})
    assert r.status_code == 404
