"""Tests for contact tags: CSV import, export round-trip, permanent delete."""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact, ContactTag, Tag
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.security.auth import get_current_user
from app.services.csv_service import detect_column_mapping, split_tags


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
        id=1, username="tester", email="t@example.com", password_hash="x",
        role="admin", is_active=True,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def test_split_tags():
    assert split_tags("vip, returning ; new") == ["vip", "returning", "new"]
    assert split_tags("  ") == []
    assert split_tags("a;a;b") == ["a", "b"]


def test_tags_header_is_detected():
    mapping = detect_column_mapping(["Name", "Phone", "Tags"])
    assert mapping["tags"] == "tags"


class TestTagImport:
    @pytest.mark.asyncio
    async def test_tags_column_attaches_tags(self, client, db):
        content = b"first_name,phone_number,tags\nAda,08031234567,vip\nChidi,08031112222,vip; returning\n"
        r = await client.post(
            "/api/v1/contacts/import/csv",
            files={"file": ("c.csv", content, "text/csv")},
        )
        assert r.status_code == 200
        assert r.json()["imported"] == 2

        contacts = (await db.execute(select(Contact).order_by(Contact.id))).scalars().all()
        ada, chidi = contacts
        ada_tags = {t.tag.name for t in ada.tags}
        chidi_tags = {t.tag.name for t in chidi.tags}
        assert ada_tags == {"vip"}
        assert chidi_tags == {"vip", "returning"}

    @pytest.mark.asyncio
    async def test_import_wide_tag_box_applies_to_all(self, client, db):
        content = b"first_name,phone_number\nAda,08031234567\nChidi,08031112222\n"
        r = await client.post(
            "/api/v1/contacts/import/csv",
            files={"file": ("c.csv", content, "text/csv")},
            data={"tags": "restaurants, cold-leads"},
        )
        assert r.status_code == 200
        contacts = (await db.execute(select(Contact))).scalars().all()
        assert len(contacts) == 2
        for c in contacts:
            names = {t.tag.name for t in c.tags}
            assert names == {"restaurants", "cold-leads"}

    @pytest.mark.asyncio
    async def test_export_includes_tags(self, client, db):
        c = Contact(phone_number="+2348031234567", first_name="Ada")
        db.add(c)
        await db.flush()
        tag = Tag(name="vip")
        db.add(tag)
        await db.flush()
        db.add(ContactTag(contact_id=c.id, tag_id=tag.id))
        await db.flush()

        r = await client.get("/api/v1/contacts/export/csv")
        assert r.status_code == 200
        assert "tags" in r.text
        assert "vip" in r.text


class TestPermanentDelete:
    @pytest.mark.asyncio
    async def test_delete_contact_removes_related_rows(self, client, db):
        c = Contact(phone_number="+2348031234567", first_name="Ada")
        db.add(c)
        await db.flush()
        conv = Conversation(contact_id=c.id)
        db.add(conv)
        await db.flush()
        db.add(Message(conversation_id=conv.id, contact_id=c.id, direction="incoming",
                       body="hi", idempotency_key="m1", status="delivered"))
        tag = Tag(name="x")
        db.add(tag)
        await db.flush()
        db.add(ContactTag(contact_id=c.id, tag_id=tag.id))
        await db.flush()

        r = await client.delete(f"/api/v1/contacts/{c.id}")
        assert r.status_code == 204

        assert (await db.execute(select(Contact))).scalars().first() is None
        assert (await db.execute(select(Message))).scalars().first() is None
        assert (await db.execute(select(Conversation))).scalars().first() is None
        assert (await db.execute(select(ContactTag))).scalars().first() is None
        # The Tag itself survives (only the link is removed).
        assert (await db.execute(select(Tag))).scalars().first() is not None

    @pytest.mark.asyncio
    async def test_bulk_delete_removes_contacts(self, client, db):
        a = Contact(phone_number="+2348031234567", first_name="Ada")
        b = Contact(phone_number="+2348031234568", first_name="Chidi")
        db.add(a)
        db.add(b)
        await db.flush()

        r = await client.post("/api/v1/contacts/bulk", json={
            "contact_ids": [a.id, b.id], "action": "delete",
        })
        assert r.status_code == 200
        assert (await db.execute(select(Contact))).scalars().first() is None
