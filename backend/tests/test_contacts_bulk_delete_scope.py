"""Regression tests for "select all matching" permanent mass-delete.

The Contacts page offers select-all across every matching page (not just the
current 25). The client sends scope="all" plus the same search / lead_status /
tag filters the list is showing; the server must delete exactly the matching
set, never the whole address book.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact, ContactTag, Tag
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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()


async def _count(db) -> int:
    return (await db.execute(select(func.count()).select_from(Contact))).scalar() or 0


async def _seed_contacts(db):
    contacts = [
        Contact(phone_number="+2348031234567", first_name="Ada", lead_status="new"),
        Contact(phone_number="+2348031234568", first_name="Adaeze", lead_status="new"),
        Contact(phone_number="+2348031234569", first_name="Chidi", lead_status="customer"),
        Contact(phone_number="+2348031234570", first_name="Bola", business_name="Bola Eats", lead_status="new"),
    ]
    db.add_all(contacts)
    await db.flush()
    return contacts


@pytest.mark.asyncio
async def test_scope_all_respects_search_filter(client, db):
    await _seed_contacts(db)

    response = await client.post(
        "/api/v1/contacts/bulk",
        json={"contact_ids": [], "action": "delete", "scope": "all", "search": "Ada"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["scope"] == "all"
    assert body["affected"] == 2  # Ada + Adaeze, not Chidi or Bola

    remaining = (await db.execute(select(Contact.first_name))).scalars().all()
    assert sorted(remaining) == ["Bola", "Chidi"]


@pytest.mark.asyncio
async def test_scope_all_respects_lead_status_filter(client, db):
    await _seed_contacts(db)

    response = await client.post(
        "/api/v1/contacts/bulk",
        json={"contact_ids": [], "action": "delete", "scope": "all", "lead_status": "new"},
    )
    assert response.status_code == 200
    assert response.json()["affected"] == 3

    remaining = (await db.execute(select(Contact.lead_status))).scalars().all()
    assert remaining == ["customer"]


@pytest.mark.asyncio
async def test_scope_all_respects_tag_filter(client, db):
    contacts = await _seed_contacts(db)
    vip = Tag(name="vip")
    db.add(vip)
    await db.flush()
    db.add(ContactTag(contact_id=contacts[0].id, tag_id=vip.id))
    db.add(ContactTag(contact_id=contacts[2].id, tag_id=vip.id))
    await db.flush()

    response = await client.post(
        "/api/v1/contacts/bulk",
        json={"contact_ids": [], "action": "delete", "scope": "all", "tag": "vip"},
    )
    assert response.status_code == 200
    assert response.json()["affected"] == 2
    assert await _count(db) == 2


@pytest.mark.asyncio
async def test_scope_all_rejects_non_delete_actions(client, db):
    await _seed_contacts(db)

    response = await client.post(
        "/api/v1/contacts/bulk",
        json={"contact_ids": [], "action": "status", "scope": "all", "value": "customer"},
    )
    assert response.status_code == 400
    # Nothing was deleted or changed.
    assert await _count(db) == 4


@pytest.mark.asyncio
async def test_default_scope_still_uses_explicit_ids(client, db):
    contacts = await _seed_contacts(db)

    response = await client.post(
        "/api/v1/contacts/bulk",
        json={"contact_ids": [contacts[0].id], "action": "delete"},
    )
    assert response.status_code == 200
    assert response.json()["affected"] == 1
    assert await _count(db) == 3
