"""Regression tests for adding, counting, editing, and removing list members."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
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


async def _list_and_contacts(db):
    contact_list = ContactList(name="Prospects")
    first = Contact(phone_number="+2348031234567", first_name="Ada")
    second = Contact(phone_number="+2348031234568", first_name="Chidi")
    db.add_all([contact_list, first, second])
    await db.flush()
    return contact_list, first, second


@pytest.mark.asyncio
async def test_add_contacts_updates_live_list_count(client, db):
    contact_list, first, second = await _list_and_contacts(db)

    response = await client.post(
        f"/api/v1/lists/{contact_list.id}/contacts",
        json=[first.id, second.id],
    )
    assert response.status_code == 200
    assert response.json() == {"success": True, "added": 2, "contact_count": 2}

    listing = await client.get("/api/v1/lists/", params={"per_page": 100})
    assert listing.status_code == 200
    assert listing.json()["items"][0]["contact_count"] == 2

    members = await client.get(f"/api/v1/lists/{contact_list.id}/contacts", params={"per_page": 100})
    assert members.status_code == 200
    assert members.json()["total"] == 2
    assert {item["id"] for item in members.json()["items"]} == {first.id, second.id}


@pytest.mark.asyncio
async def test_adding_existing_member_does_not_inflate_count(client, db):
    contact_list, first, _ = await _list_and_contacts(db)
    first_add = await client.post(
        f"/api/v1/lists/{contact_list.id}/contacts", json=[first.id]
    )
    second_add = await client.post(
        f"/api/v1/lists/{contact_list.id}/contacts", json=[first.id, first.id]
    )

    assert first_add.json()["added"] == 1
    assert second_add.json()["added"] == 0
    assert second_add.json()["contact_count"] == 1


@pytest.mark.asyncio
async def test_remove_from_list_keeps_contact_and_updates_count(client, db):
    contact_list, first, second = await _list_and_contacts(db)
    await client.post(
        f"/api/v1/lists/{contact_list.id}/contacts", json=[first.id, second.id]
    )

    response = await client.post(
        f"/api/v1/lists/{contact_list.id}/contacts/remove",
        json={"contact_ids": [first.id]},
    )
    assert response.status_code == 200
    assert response.json() == {"success": True, "removed": 1, "contact_count": 1}

    # Removing from a list must not delete the contact record itself.
    assert (await db.execute(select(Contact).where(Contact.id == first.id))).scalar_one()
    memberships = (
        await db.execute(
            select(ContactListMember).where(ContactListMember.list_id == contact_list.id)
        )
    ).scalars().all()
    assert [membership.contact_id for membership in memberships] == [second.id]


@pytest.mark.asyncio
async def test_list_endpoint_repairs_stale_zero_count(client, db):
    contact_list, first, _ = await _list_and_contacts(db)
    db.add(ContactListMember(list_id=contact_list.id, contact_id=first.id))
    contact_list.contact_count = 0
    await db.flush()

    response = await client.get("/api/v1/lists/")
    assert response.status_code == 200
    assert response.json()["items"][0]["contact_count"] == 1
    assert contact_list.contact_count == 1


@pytest.mark.asyncio
async def test_add_rejects_unknown_contact_without_partial_change(client, db):
    contact_list, first, _ = await _list_and_contacts(db)
    response = await client.post(
        f"/api/v1/lists/{contact_list.id}/contacts", json=[first.id, 999]
    )
    assert response.status_code == 404

    members = await client.get(f"/api/v1/lists/{contact_list.id}/contacts")
    assert members.json()["total"] == 0
