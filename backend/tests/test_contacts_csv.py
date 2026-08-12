"""Regression tests for CSV import/export and "import to list".

Covers the two "zero contacts" bugs users hit:

1. Mixed-case CSV headers were never matched. The import mapping was keyed by
   lowercased header names but looked up against the original (cased) header
   names, so every row came back "No phone number" and the import reported 0.
2. "Import to a list" attached nothing: ``list_id`` was declared as a query
   parameter (the UI sends it as a multipart form field) AND the import
   service ignored it, so the list stayed at zero contacts.
"""

import json

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
from app.services.csv_service import detect_column_mapping


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


async def _make_list(db, name="VIP") -> ContactList:
    lst = ContactList(name=name)
    db.add(lst)
    await db.flush()
    return lst


class TestHeaderDetection:
    def test_detects_common_headers_case_insensitively(self):
        mapping = detect_column_mapping([
            "First Name", "Last Name", "Phone Number", "Business Name", "Email", "City",
        ])
        assert mapping == {
            "first name": "first_name",
            "last name": "last_name",
            "phone number": "phone_number",
            "business name": "business_name",
            "email": "email",
            "city": "city",
        }

    def test_contact_name_is_not_phone(self):
        mapping = detect_column_mapping(["Contact Name", "Mobile Number"])
        assert mapping["contact name"] == "first_name"
        assert mapping["mobile number"] == "phone_number"


class TestCSVImport:
    @pytest.mark.asyncio
    async def test_mixed_case_headers_import(self, client, db):
        csv_content = (
            "First Name,Last Name,Phone Number,Business Name,City\n"
            "Ada,Obi,08031234567,Chicken Republic,Lagos\n"
            "Chidi,Eze,08031112222,Dominos,Abuja\n"
        ).encode()
        r = await client.post(
            "/api/v1/contacts/import/csv",
            files={"file": ("contacts.csv", csv_content, "text/csv")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["imported"] == 2
        assert data["invalid"] == 0
        assert data["duplicates"] == 0

        contacts = (await db.execute(select(Contact))).scalars().all()
        assert len(contacts) == 2
        phones = {c.phone_number for c in contacts}
        assert phones == {"+2348031234567", "+2348031112222"}

    @pytest.mark.asyncio
    async def test_import_into_list_attaches_members(self, client, db):
        lst = await _make_list(db)
        csv_content = (
            "first_name,phone_number\n"
            "Ngozi,08052223344\n"
            "Emeka,08093334455\n"
        ).encode()
        r = await client.post(
            "/api/v1/contacts/import/csv",
            files={"file": ("contacts.csv", csv_content, "text/csv")},
            data={"list_id": str(lst.id)},
        )
        assert r.status_code == 200
        assert r.json()["imported"] == 2

        members = (await db.execute(
            select(ContactListMember).where(ContactListMember.list_id == lst.id)
        )).scalars().all()
        assert len(members) == 2

        await db.refresh(lst)
        assert lst.contact_count == 2

        # The list API reflects the members too.
        r = await client.get(f"/api/v1/lists/{lst.id}/contacts")
        assert r.json()["total"] == 2

    @pytest.mark.asyncio
    async def test_import_to_missing_list_is_404(self, client, db):
        csv_content = b"first_name,phone_number\nNgozi,08052223344\n"
        r = await client.post(
            "/api/v1/contacts/import/csv",
            files={"file": ("contacts.csv", csv_content, "text/csv")},
            data={"list_id": "9999"},
        )
        assert r.status_code == 404
        # Nothing was half-imported.
        assert (await db.execute(select(Contact))).scalars().first() is None

    @pytest.mark.asyncio
    async def test_duplicates_are_skipped(self, client, db):
        db.add(Contact(phone_number="+2348031234567", first_name="Existing"))
        await db.flush()

        csv_content = (
            "first_name,phone_number\n"
            "Ada,08031234567\n"   # duplicate of existing
            "Chidi,08031112222\n"  # new
        ).encode()
        r = await client.post(
            "/api/v1/contacts/import/csv",
            files={"file": ("contacts.csv", csv_content, "text/csv")},
        )
        data = r.json()
        assert data["imported"] == 1
        assert data["duplicates"] == 1

    @pytest.mark.asyncio
    async def test_client_column_mapping_override(self, client, db):
        # A CSV with a non-standard phone column; the client maps it manually.
        csv_content = b"Name,Mobile No\nAda,08031234567\n"
        r = await client.post(
            "/api/v1/contacts/import/csv",
            files={"file": ("contacts.csv", csv_content, "text/csv")},
            data={
                "column_mapping": json.dumps({"Name": "first_name", "Mobile No": "phone_number"}),
            },
        )
        assert r.status_code == 200
        assert r.json()["imported"] == 1
        contact = (await db.execute(select(Contact))).scalar_one()
        assert contact.first_name == "Ada"
        assert contact.phone_number == "+2348031234567"


class TestCSVExport:
    @pytest.mark.asyncio
    async def test_export_returns_all_contacts(self, client, db):
        db.add(Contact(phone_number="+2348031234567", first_name="Ada", last_name="Obi",
                       business_name="Chicken Republic", city="Lagos", lead_status="new"))
        db.add(Contact(phone_number="+2348031112222", first_name="Chidi", last_name="Eze",
                       business_name="Dominos", city="Abuja", lead_status="customer"))
        await db.flush()

        r = await client.get("/api/v1/contacts/export/csv")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers.get("content-disposition", "")

        text = r.text
        assert "first_name" in text
        assert "Ada" in text and "Chidi" in text
        assert "+2348031234567" in text

        # Two data rows plus a header row.
        assert len([ln for ln in text.splitlines() if ln.strip()]) == 3

    @pytest.mark.asyncio
    async def test_export_respects_lead_status_filter(self, client, db):
        db.add(Contact(phone_number="+2348031234567", first_name="Ada", lead_status="new"))
        db.add(Contact(phone_number="+2348031112222", first_name="Chidi", lead_status="customer"))
        await db.flush()

        r = await client.get("/api/v1/contacts/export/csv", params={"lead_status": "customer"})
        assert r.status_code == 200
        assert "Chidi" in r.text
        assert "Ada" not in r.text
