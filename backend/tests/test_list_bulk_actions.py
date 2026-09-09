"""Scale-safe deletion + scoped list actions.

Covers the paths that previously fell over on large lists: loading every
member into memory, and permanent deletes that ran one ~15-statement cleanup
per contact inside a single transaction. Everything here deletes in bulk with
set-based statements and keeps counts honest.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact, Tag, ContactTag
from app.models.contact_list import ContactList, ContactListMember
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.security.auth import get_current_user


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


async def _make_contact(db, i: int, **kw) -> Contact:
    params = dict(
        phone_number=f"+2348031{i:07d}",
        first_name=f"Name{i}",
        country="Nigeria",
    )
    params.update(kw)
    contact = Contact(**params)
    db.add(contact)
    await db.flush()
    return contact


async def _make_list_with_members(db, contact_count: int) -> tuple[ContactList, list[Contact]]:
    contact_list = ContactList(name="Big list")
    db.add(contact_list)
    await db.flush()
    contacts = [await _make_contact(db, i) for i in range(contact_count)]
    contact_list.contact_count = len(contacts)
    for contact in contacts:
        db.add(ContactListMember(list_id=contact_list.id, contact_id=contact.id))
    await db.flush()
    return contact_list, contacts


async def _add_chat_history(db, contact: Contact) -> None:
    conversation = Conversation(contact_id=contact.id, status="active")
    db.add(conversation)
    await db.flush()
    db.add_all(
        [
            Message(
                conversation_id=conversation.id,
                contact_id=contact.id,
                direction="outgoing",
                body="hello",
                idempotency_key=f"out-{contact.id}",
            ),
            Message(
                conversation_id=conversation.id,
                contact_id=contact.id,
                direction="incoming",
                body="hi back",
                idempotency_key=f"in-{contact.id}",
            ),
        ]
    )
    await db.flush()


async def _total(db, statement) -> int:
    return (await db.execute(select(func.count()).select_from(statement.subquery()))).scalar() or 0


# ------------------------------------------------------------ permanent bulk


@pytest.mark.asyncio
async def test_delete_all_list_members_is_set_based_and_cleans_references(client, db):
    contact_list, contacts = await _make_list_with_members(db, 120)
    outsider = await _make_contact(db, 9999)
    tag = Tag(name="lead")
    db.add(tag)
    await db.flush()
    for contact in contacts:
        await _add_chat_history(db, contact)
        db.add(ContactTag(contact_id=contact.id, tag_id=tag.id))

    response = await client.post(
        f"/api/v1/lists/{contact_list.id}/contacts/delete", json={"scope": "all"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] == 120
    assert body["contact_count"] == 0

    # Contacts themselves are gone, and so is everything that referenced them.
    remaining = (
        await db.execute(select(Contact).where(Contact.id.in_([c.id for c in contacts])))
    ).scalars().all()
    assert remaining == []
    assert await _total(db, select(Message)) == 0
    assert await _total(db, select(Conversation)) == 0
    assert await _total(db, select(ContactListMember)) == 0
    assert await _total(db, select(ContactTag)) == 0

    # The cached member count on the list row is repaired, not stale.
    row_count = (
        await db.execute(
            select(ContactList.contact_count).where(ContactList.id == contact_list.id)
        )
    ).scalar_one()
    assert row_count == 0

    # Unrelated contacts survive untouched.
    surviving = (
        await db.execute(select(func.count()).select_from(Contact).where(Contact.id == outsider.id))
    ).scalar_one()
    assert surviving == 1


@pytest.mark.asyncio
async def test_delete_scope_all_honors_search(client, db):
    contact_list = ContactList(name="Filtered")
    db.add(contact_list)
    await db.flush()
    members = []
    for i in range(10):
        member = await _make_contact(db, i, first_name="Ada" if i < 3 else f"Ben{i}")
        members.append(member)
        db.add(ContactListMember(list_id=contact_list.id, contact_id=member.id))
    contact_list.contact_count = len(members)
    await db.flush()

    response = await client.post(
        f"/api/v1/lists/{contact_list.id}/contacts/delete",
        json={"scope": "all", "search": "Ada"},
    )
    assert response.status_code == 200
    assert response.json()["deleted"] == 3
    assert response.json()["contact_count"] == 7

    survivors = (
        await db.execute(
            select(Contact.first_name)
            .join(ContactListMember, Contact.id == ContactListMember.contact_id)
            .where(ContactListMember.list_id == contact_list.id)
        )
    ).scalars().all()
    assert all(name != "Ada" for name in survivors)
    assert len(survivors) == 7


@pytest.mark.asyncio
async def test_remove_scope_all_only_unlinks_contacts(client, db):
    contact_list, contacts = await _make_list_with_members(db, 8)

    response = await client.post(
        f"/api/v1/lists/{contact_list.id}/contacts/remove", json={"scope": "all"}
    )
    assert response.status_code == 200
    assert response.json()["removed"] == 8
    assert response.json()["contact_count"] == 0

    # Contacts still exist — only the memberships were dropped.
    assert (
        await db.execute(
            select(func.count()).select_from(Contact).where(Contact.id.in_([c.id for c in contacts]))
        )
    ).scalar() == 8


@pytest.mark.asyncio
async def test_bulk_contact_delete_endpoint_cleans_everything(client, db):
    contact_list = ContactList(name="Mixed")
    db.add(contact_list)
    await db.flush()
    victims, keepers = [], []
    for i in range(30):
        victim = await _make_contact(db, i)
        victims.append(victim)
        await _add_chat_history(db, victim)
        db.add(ContactListMember(list_id=contact_list.id, contact_id=victim.id))
    for i in range(30, 35):
        keeper = await _make_contact(db, i)
        keepers.append(keeper)
        db.add(ContactListMember(list_id=contact_list.id, contact_id=keeper.id))
    contact_list.contact_count = len(victims) + len(keepers)
    await db.flush()

    response = await client.post(
        "/api/v1/contacts/bulk",
        json={"contact_ids": [c.id for c in victims], "action": "delete"},
    )
    assert response.status_code == 200
    assert response.json()["affected"] == 30

    assert await _total(db, select(Message)) == 0
    list_count = (
        await db.execute(
            select(ContactList.contact_count).where(ContactList.id == contact_list.id)
        )
    ).scalar_one()
    assert list_count == 5
    # The memberships of the survivors are still there.
    assert (
        await db.execute(
            select(func.count()).select_from(ContactListMember).where(
                ContactListMember.list_id == contact_list.id
            )
        )
    ).scalar() == 5
    for keeper in keepers:
        still_there = (
            await db.execute(
                select(func.count()).select_from(Contact).where(Contact.id == keeper.id)
            )
        ).scalar_one()
        assert still_there == 1


# ------------------------------------------------------------ listing/search


@pytest.mark.asyncio
async def test_list_members_are_paginated_and_searchable(client, db):
    contact_list = ContactList(name="Paged")
    db.add(contact_list)
    await db.flush()
    members = []
    for i in range(60):
        first = "Ada" if i < 10 else f"Ben{i - 10}"
        member = await _make_contact(db, i, first_name=first)
        members.append(member)
        db.add(ContactListMember(list_id=contact_list.id, contact_id=member.id))
    contact_list.contact_count = len(members)
    await db.flush()

    page = await client.get(
        f"/api/v1/lists/{contact_list.id}/contacts", params={"page": 2, "per_page": 25}
    )
    assert page.status_code == 200
    assert page.json()["total"] == 60
    assert len(page.json()["items"]) == 25

    needle = await client.get(
        f"/api/v1/lists/{contact_list.id}/contacts", params={"search": "Ada", "per_page": 100}
    )
    assert needle.json()["total"] == 10
    assert {item["first_name"] for item in needle.json()["items"]} == {"Ada"}

    none = await client.get(
        f"/api/v1/lists/{contact_list.id}/contacts", params={"search": "Nobody", "per_page": 100}
    )
    assert none.json()["total"] == 0
    assert none.json()["items"] == []


@pytest.mark.asyncio
async def test_contacts_can_exclude_an_entire_list(client, db):
    contact_list, members = await _make_list_with_members(db, 5)
    free_contact = await _make_contact(db, 77)

    response = await client.get(
        "/api/v1/contacts/",
        params={"exclude_list_id": contact_list.id, "per_page": 100},
    )
    ids = {item["id"] for item in response.json()["items"]}
    assert free_contact.id in ids
    assert not ids.intersection({m.id for m in members})
    assert response.json()["total"] == 1
