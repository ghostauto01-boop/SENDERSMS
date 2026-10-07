"""Import rules for phone-only, email-only, and phone+email contacts.

The requirement these tests pin down:

    Phone must not be *compulsory*. Some contacts have only a phone, some
    have only an email, and some have both — all three must import, and a
    person who appears in two files (once with a number, once with an
    address) must end up as ONE contact carrying both, not as two records.

They also cover the email-enrichment surface: cleaning, typo/diagnosis flags,
and the rule that a guessed address is never marked verified.
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
from app.utils.contact_identity import (
    contact_is_usable,
    contact_key,
    is_role_address,
    normalize_email,
)


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


async def _import(client, csv_content: bytes, **data):
    response = await client.post(
        "/api/v1/contacts/import/csv",
        files={"file": ("contacts.csv", csv_content, "text/csv")},
        data=data or None,
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------
# The identity rules in isolation
# --------------------------------------------------------------------------


class TestIdentityRules:
    def test_phone_only_row_is_usable(self):
        assert contact_is_usable("08031234567", None) is True

    def test_email_only_row_is_usable(self):
        assert contact_is_usable(None, "ada@example.com") is True

    def test_row_with_neither_channel_is_not_usable(self):
        assert contact_is_usable(None, None) is False
        assert contact_is_usable("", "") is False

    def test_placeholder_phone_counts_as_no_phone(self):
        # Excel filler cells must not be mistaken for a number.
        for filler in ("-", "N/A", "none", "null", "0", "x"):
            assert contact_is_usable(filler, "ada@example.com") is True
            assert contact_is_usable(filler, None) is False

    def test_email_is_normalised(self):
        assert normalize_email("  Ada.Obi@Example.COM ") == "ada.obi@example.com"
        assert normalize_email("<hello@domain.com>,") == "hello@domain.com"
        assert normalize_email("not an email") is None
        assert normalize_email("") is None
        assert normalize_email(None) is None

    def test_key_prefers_phone_then_email(self):
        assert contact_key("08031234567", "ada@example.com") == "p:+2348031234567"
        assert contact_key(None, "Ada@Example.com") == "e:ada@example.com"
        assert contact_key("08031234567", None) == "p:+2348031234567"
        assert contact_key(None, None) is None

    def test_role_addresses_detected(self):
        assert is_role_address("info@restaurant.com") is True
        assert is_role_address("sales.lagos@x.com") is True
        assert is_role_address("ada.obi@gmail.com") is False


class TestHeaderDetection:
    def test_email_aliases_are_recognised(self):
        mapping = detect_column_mapping(
            ["Business Name", "Email Address", "Email Id", "Work Email", "Phone"]
        )
        assert mapping["email address"] == "email"
        assert mapping["email id"] == "email"
        assert mapping["work email"] == "email"
        assert mapping["business name"] == "business_name"
        assert mapping["phone"] == "phone_number"


# --------------------------------------------------------------------------
# Import behaviour
# --------------------------------------------------------------------------


class TestMixedChannelImport:
    @pytest.mark.asyncio
    async def test_email_only_rows_import_without_a_phone_column(self, client, db):
        """The headline requirement: no phone column at all, still imports."""
        csv_content = (
            "Business Name,Email,City\n"
            "Chicken Republic,hello@chickenrepublic.ng,Lagos\n"
            "Dominos,info@dominos.ng,Abuja\n"
        ).encode()
        data = await _import(client, csv_content)

        assert data["imported"] == 2
        assert data["invalid"] == 0
        assert data["email_only"] == 2
        assert data["with_phone"] == 0

        contacts = (await db.execute(select(Contact))).scalars().all()
        assert len(contacts) == 2
        assert {c.email for c in contacts} == {
            "hello@chickenrepublic.ng",
            "info@dominos.ng",
        }
        # No phone on either — and that is a valid state, not an error.
        assert all(c.phone_number is None for c in contacts)

    @pytest.mark.asyncio
    async def test_phone_only_rows_still_import(self, client, db):
        csv_content = b"First Name,Phone Number\nAda,08031234567\nChidi,08031112222\n"
        data = await _import(client, csv_content)

        assert data["imported"] == 2
        assert data["phone_only"] == 2
        assert data["with_email"] == 0
        contacts = (await db.execute(select(Contact))).scalars().all()
        assert {c.phone_number for c in contacts} == {"+2348031234567", "+2348031112222"}
        assert all(c.email is None for c in contacts)

    @pytest.mark.asyncio
    async def test_mixed_file_counts_each_channel(self, client, db):
        csv_content = (
            "Name,Phone,Email\n"
            "Ada,08031234567,ada@example.com\n"     # both
            "Chidi,,chidi@example.com\n"            # email only
            "Ngozi,08052223344,\n"                  # phone only
        ).encode()
        data = await _import(client, csv_content)

        assert data["imported"] == 3
        assert data["invalid"] == 0
        assert data["both"] == 1
        assert data["email_only"] == 1
        assert data["phone_only"] == 1

    @pytest.mark.asyncio
    async def test_row_with_neither_channel_is_still_invalid(self, client, db):
        csv_content = b"Name,Phone,Email\nNobody,,\n"
        data = await _import(client, csv_content)

        assert data["imported"] == 0
        assert data["invalid"] == 1
        assert "No phone number and no email" in data["errors"][0]["error"]

    @pytest.mark.asyncio
    async def test_invalid_phone_with_a_good_email_imports_as_email_only(self, client, db):
        """A junk number must not take a perfectly good address down with it."""
        csv_content = b"Name,Phone,Email\nAda,12345,ada@example.com\n"
        data = await _import(client, csv_content)

        assert data["imported"] == 1
        assert data["invalid"] == 0
        assert data["email_only"] == 1
        contact = (await db.execute(select(Contact))).scalar_one()
        assert contact.email == "ada@example.com"
        assert contact.phone_number is None
        # The bad number is reported as a warning, not silently dropped.
        warnings = [e for e in data["errors"] if e.get("level") == "warning"]
        assert len(warnings) == 1
        assert "12345" in warnings[0]["error"]

    @pytest.mark.asyncio
    async def test_multiple_addresses_in_one_cell_keep_the_extras(self, client, db):
        csv_content = b"Business,Email\nAcme,\"ada@acme.com; billing@acme.com\"\n"
        data = await _import(client, csv_content)

        assert data["imported"] == 1
        contact = (await db.execute(select(Contact))).scalar_one()
        assert contact.email == "ada@acme.com"
        custom = json.loads(contact.custom_fields)
        assert custom["additional_emails"] == "billing@acme.com"

    @pytest.mark.asyncio
    async def test_reimporting_the_same_address_does_not_create_a_second_contact(
        self, client, db
    ):
        db.add(Contact(
            phone_number="+2348039999999", email="ada@example.com", first_name="Ada",
        ))
        await db.flush()

        # Same address, different case — the address is the identity.
        csv_content = b"Name,Email\nAda,ADA@example.com\n"
        data = await _import(client, csv_content)

        assert data["imported"] == 0
        contacts = (await db.execute(select(Contact))).scalars().all()
        assert len(contacts) == 1

    @pytest.mark.asyncio
    async def test_a_true_no_op_row_is_reported_as_a_duplicate(self, client, db):
        """Nothing new to add: that is a duplicate, not a merge."""
        db.add(Contact(
            phone_number="+2348039999999", email="ada@example.com",
            first_name="Ada", last_name="Obi", city="Lagos",
        ))
        await db.flush()

        csv_content = b"First Name,Last Name,Phone,Email,City\nAda,Obi,08039999999,ada@example.com,Lagos\n"
        data = await _import(client, csv_content)

        assert data["imported"] == 0
        assert data["merged"] == 0
        assert data["duplicates"] == 1

    @pytest.mark.asyncio
    async def test_import_marks_csv_as_the_email_source(self, client, db):
        data = await _import(client, b"Name,Email\nAda,ada@example.com\n")
        assert data["imported"] == 1
        contact = (await db.execute(select(Contact))).scalar_one()
        # Provenance matters: enrichment must never overwrite "a human typed
        # this" with "a machine found it".
        assert contact.email_source == "csv"
        assert contact.email_verified is False


class TestPerfectMerge:
    """A person in two files lands as one contact with both channels."""

    @pytest.mark.asyncio
    async def test_email_only_import_enriches_an_existing_phone_only_contact(
        self, client, db
    ):
        """The two-file workflow: numbers first, addresses second."""
        db.add(Contact(
            phone_number="+2348031234567", first_name="Ada", last_name="Obi",
        ))
        await db.flush()

        # Second file: same person, only an address. The two rows share no
        # phone and no address, so the full name is what ties them together.
        csv_content = b"First Name,Last Name,Email\nAda,Obi,ada@example.com\n"
        data = await _import(client, csv_content)

        assert data["merged"] == 1
        assert data["merged_by_name"] == 1
        assert data["imported"] == 0
        assert data["duplicates"] == 0

        contacts = (await db.execute(select(Contact))).scalars().all()
        assert len(contacts) == 1, "the two rows must be ONE contact"
        assert contacts[0].phone_number == "+2348031234567"
        assert contacts[0].email == "ada@example.com"

    @pytest.mark.asyncio
    async def test_phone_only_import_enriches_an_existing_email_only_contact(
        self, client, db
    ):
        db.add(Contact(
            phone_number=None, email="ada@example.com",
            first_name="Ada", last_name="Obi",
        ))
        await db.flush()

        csv_content = b"First Name,Last Name,Phone Number\nAda,Obi,08031234567\n"
        data = await _import(client, csv_content)

        assert data["merged"] == 1
        contacts = (await db.execute(select(Contact))).scalars().all()
        assert len(contacts) == 1
        assert contacts[0].phone_number == "+2348031234567"
        assert contacts[0].email == "ada@example.com"

    @pytest.mark.asyncio
    async def test_a_row_with_both_channels_merges_on_either_one(self, client, db):
        """A row carrying both identifiers joins a contact matching either."""
        db.add(Contact(
            phone_number="+2348031234567", first_name="Ada", last_name="Obi",
        ))
        await db.flush()

        # Same phone, and an address we did not have -> one contact, both filled.
        csv_content = (
            "First Name,Last Name,Phone,Email\n"
            "Ada,Obi,08031234567,ada@example.com\n"
        ).encode()
        data = await _import(client, csv_content)

        assert data["merged"] == 1
        contacts = (await db.execute(select(Contact))).scalars().all()
        assert len(contacts) == 1
        assert contacts[0].email == "ada@example.com"

    @pytest.mark.asyncio
    async def test_ambiguous_name_match_does_not_merge(self, client, db):
        """Two Ada Obis: guessing would corrupt somebody's record."""
        db.add(Contact(phone_number="+2348031111111", first_name="Ada", last_name="Obi"))
        db.add(Contact(phone_number="+2348032222222", first_name="Ada", last_name="Obi"))
        await db.flush()

        csv_content = b"First Name,Last Name,Email\nAda,Obi,ada@example.com\n"
        data = await _import(client, csv_content)

        assert data["merged"] == 0
        assert data["imported"] == 1, "imported as a new contact instead"
        assert len((await db.execute(select(Contact))).scalars().all()) == 3

    @pytest.mark.asyncio
    async def test_single_name_alone_does_not_merge(self, client, db):
        """A first name is not an identity — never merge on it."""
        db.add(Contact(phone_number="+2348031234567", first_name="Ada"))
        await db.flush()

        csv_content = b"First Name,Email\nAda,ada@example.com\n"
        data = await _import(client, csv_content)

        assert data["merged"] == 0
        assert data["imported"] == 1

    @pytest.mark.asyncio
    async def test_name_match_skips_contacts_that_already_have_an_address(
        self, client, db
    ):
        """The match is only for the contact this row can actually improve."""
        db.add(Contact(
            phone_number="+2348031234567", email="other@example.com",
            first_name="Ada", last_name="Obi",
        ))
        await db.flush()

        csv_content = b"First Name,Last Name,Email\nAda,Obi,ada@example.com\n"
        data = await _import(client, csv_content)

        # That Ada already has an address, so this is somebody else / a new row.
        assert data["merged"] == 0
        assert data["imported"] == 1

    @pytest.mark.asyncio
    async def test_merge_never_clears_a_populated_field(self, client, db):
        db.add(Contact(
            phone_number="+2348031234567", email="ada@example.com",
            first_name="Ada", city="Lagos",
        ))
        await db.flush()

        # A file that only carries numbers + a new business name must not blank
        # the address on file.
        csv_content = b"Phone Number,City,Business Name\n08031234567,,Acme Foods\n"
        data = await _import(client, csv_content)

        assert data["merged"] == 1
        contact = (await db.execute(select(Contact))).scalar_one()
        assert contact.email == "ada@example.com", "email survived the re-import"
        assert contact.city == "Lagos", "blank cell must not overwrite"
        assert contact.first_name == "Ada"
        assert contact.business_name == "Acme Foods"

    @pytest.mark.asyncio
    async def test_merge_fills_blank_fields_from_the_new_file(self, client, db):
        db.add(Contact(phone_number="+2348031234567", first_name="Ada"))
        await db.flush()

        csv_content = b"Phone Number,Business Name,City\n08031234567,Acme Foods,Abuja\n"
        data = await _import(client, csv_content)

        assert data["merged"] == 1
        contact = (await db.execute(select(Contact))).scalar_one()
        assert contact.business_name == "Acme Foods"
        assert contact.city == "Abuja"

    @pytest.mark.asyncio
    async def test_duplicate_rows_inside_one_file_merge_on_either_channel(
        self, client, db
    ):
        csv_content = (
            "Name,Phone,Email\n"
            "Ada,08031234567,\n"          # phone row
            "Ada,,08031234567@x.com\n"    # different key: a distinct person
            "Ada,08031234567,ada@x.com\n"  # same phone as row 1 -> duplicate
        ).encode()
        data = await _import(client, csv_content)

        assert data["imported"] == 2
        assert data["duplicates"] == 1

    @pytest.mark.asyncio
    async def test_merged_rows_join_the_destination_list(self, client, db):
        lst = ContactList(name="VIP")
        db.add(lst)
        db.add(Contact(
            phone_number="+2348031234567", first_name="Ada", last_name="Obi",
        ))
        await db.flush()

        csv_content = b"First Name,Last Name,Email\nAda,Obi,ada@example.com\n"
        data = await _import(client, csv_content, list_id=str(lst.id))

        assert data["merged"] == 1
        members = (await db.execute(
            select(ContactListMember).where(ContactListMember.list_id == lst.id)
        )).scalars().all()
        assert len(members) == 1
        await db.refresh(lst)
        assert lst.contact_count == 1

    @pytest.mark.asyncio
    async def test_two_distinct_people_are_not_merged(self, client, db):
        csv_content = (
            "Name,Phone,Email\n"
            "Ada,08031234567,ada@example.com\n"
            "Chidi,08031112222,chidi@example.com\n"
        ).encode()
        data = await _import(client, csv_content)

        assert data["imported"] == 2
        assert data["merged"] == 0
        assert len((await db.execute(select(Contact))).scalars().all()) == 2


class TestContactApiIdentity:
    """The same phone-OR-email rule on the single-contact endpoints."""

    @pytest.mark.asyncio
    async def test_create_email_only_contact(self, client, db):
        r = await client.post(
            "/api/v1/contacts/",
            json={"business_name": "Acme Foods", "email": "Hello@Acme.ng"},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["phone_number"] is None
        assert body["email"] == "hello@acme.ng", "address is normalised"
        assert body["email_source"] == "manual"
        assert body["email_verified"] is False

    @pytest.mark.asyncio
    async def test_create_phone_only_contact(self, client, db):
        r = await client.post(
            "/api/v1/contacts/", json={"first_name": "Ada", "phone_number": "08031234567"}
        )
        assert r.status_code == 201
        assert r.json()["phone_number"] == "+2348031234567"
        assert r.json()["email"] is None

    @pytest.mark.asyncio
    async def test_create_with_neither_identifier_is_refused(self, client, db):
        r = await client.post("/api/v1/contacts/", json={"first_name": "Nobody"})
        assert r.status_code == 400
        assert "phone number or an email" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_a_phone_can_be_added_to_an_email_only_contact(self, client, db):
        contact = Contact(phone_number=None, email="hello@acme.ng")
        db.add(contact)
        await db.flush()

        r = await client.put(
            f"/api/v1/contacts/{contact.id}", json={"phone_number": "08059998877"}
        )
        assert r.status_code == 200
        assert r.json()["phone_number"] == "+2348059998877"
        assert r.json()["email"] == "hello@acme.ng"

    @pytest.mark.asyncio
    async def test_clearing_both_channels_is_refused(self, client, db):
        contact = Contact(phone_number="+2348031234567", email="hello@acme.ng")
        db.add(contact)
        await db.flush()

        r = await client.put(
            f"/api/v1/contacts/{contact.id}",
            json={"phone_number": None, "email": None},
        )
        assert r.status_code == 400
        assert "phone number or an email" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_clearing_one_channel_keeps_the_contact(self, client, db):
        contact = Contact(phone_number="+2348031234567", email="hello@acme.ng")
        db.add(contact)
        await db.flush()

        r = await client.put(f"/api/v1/contacts/{contact.id}", json={"phone_number": None})
        assert r.status_code == 200
        assert r.json()["phone_number"] is None
        assert r.json()["email"] == "hello@acme.ng"

    @pytest.mark.asyncio
    async def test_editing_an_address_resets_its_verification(self, client, db):
        """A typed-in address is not the address that was verified."""
        contact = Contact(
            phone_number="+2348031234567", email="old@acme.ng",
            email_source="hunter", email_verified=True, email_confidence=95,
        )
        db.add(contact)
        await db.flush()

        r = await client.put(f"/api/v1/contacts/{contact.id}", json={"email": "new@acme.ng"})
        assert r.status_code == 200
        assert r.json()["email"] == "new@acme.ng"
        assert r.json()["email_verified"] is False, "must be re-verified"
        assert r.json()["email_source"] == "manual"

    @pytest.mark.asyncio
    async def test_api_refuses_a_duplicate_email(self, client, db):
        db.add(Contact(phone_number="+2348031234567", email="taken@acme.ng"))
        await db.flush()

        r = await client.post(
            "/api/v1/contacts/", json={"email": "TAKEN@acme.ng", "first_name": "Copy"}
        )
        assert r.status_code == 409
        assert "email" in r.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_email_state_filters_cover_verification(self, client, db):
        db.add(Contact(phone_number="+2348031111111", email="verified@acme.ng",
                       email_verified=True))
        db.add(Contact(phone_number="+2348032222222", email="guessed@acme.ng",
                       email_source="inferred"))
        db.add(Contact(phone_number="+2348033333333"))
        await db.flush()

        async def total(params):
            r = await client.get("/api/v1/contacts/", params=params)
            assert r.status_code == 200
            return r.json()["total"]

        assert await total({"email_state": "verified"}) == 1
        assert await total({"email_state": "unverified"}) == 1
        assert await total({"email_state": "inferred"}) == 1
        assert await total({"email_state": "emailable"}) == 2
        assert await total({"email_state": "no_email"}) == 1


class TestEnrichmentEndpoints:
    @pytest.mark.asyncio
    async def test_status_reports_the_outstanding_work(self, client, db):
        db.add(Contact(phone_number="+2348031111111", email="a@acme.ng",
                       email_verified=True))
        db.add(Contact(phone_number="+2348032222222", email="b@acme.ng"))
        db.add(Contact(phone_number="+2348033333333"))
        await db.flush()

        r = await client.get("/api/v1/contacts/enrich/status")
        assert r.status_code == 200
        body = r.json()
        assert body["contacts"]["total"] == 3
        assert body["contacts"]["with_email"] == 2
        assert body["contacts"]["without_email"] == 1
        assert body["contacts"]["verified"] == 1
        assert body["contacts"]["unverified"] == 1
        assert "clean" in body["free_stages"]

    @pytest.mark.asyncio
    async def test_no_email_scope_does_not_touch_contacts_that_have_an_address(
        self, client, db
    ):
        db.add(Contact(phone_number="+2348031111111", email="a@acme.ng"))
        db.add(Contact(phone_number="+2348033333333"))
        await db.flush()

        r = await client.post("/api/v1/contacts/enrich", params={"scope": "no_email"})
        assert r.status_code == 200
        body = r.json()
        assert body["matched"] == 1, "only the contact with no address"
        assert body["processed"] == 1

    @pytest.mark.asyncio
    async def test_ids_scope_requires_a_selection(self, client, db):
        r = await client.post("/api/v1/contacts/enrich", params={"scope": "ids"})
        assert r.status_code == 400
        assert "No contacts selected" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_unknown_scope_is_rejected(self, client, db):
        r = await client.post("/api/v1/contacts/enrich", params={"scope": "everything"})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_enriching_by_id_returns_per_contact_outcomes(self, client, db):
        contact = Contact(phone_number="+2348031234567", email="ADA@Acme.ng")
        db.add(contact)
        await db.flush()

        r = await client.post(
            "/api/v1/contacts/enrich",
            params={"scope": "ids", "contact_ids": [contact.id]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["processed"] == 1
        assert body["items"][0]["contact_id"] == contact.id
        await db.refresh(contact)
        assert contact.email == "ada@acme.ng"


class TestPreviewChannels:
    """The preview tallies channels so the gap is visible before importing."""

    @pytest.mark.asyncio
    async def test_preview_reports_channel_coverage(self, db):
        from app.services.csv_service import CSVImportService

        csv_content = (
            "Name,Phone,Email\n"
            "Ada,08031234567,ada@example.com\n"
            "Chidi,,chidi@example.com\n"
            "Ngozi,08052223344,\n"
        ).encode()
        body = await CSVImportService(db).preview_csv(csv_content)

        assert body["channels"]["both"] == 1
        assert body["channels"]["email_only"] == 1
        assert body["channels"]["phone_only"] == 1
        assert body["channels"]["neither"] == 0
        assert body["has_phone_column"] is True
        assert body["has_email_column"] is True
        assert body["total_rows"] == 3

    @pytest.mark.asyncio
    async def test_preview_sees_an_email_only_file(self, db):
        from app.services.csv_service import CSVImportService

        body = await CSVImportService(db).preview_csv(
            b"Business,Email\nAcme,a@acme.com\n"
        )
        assert body["has_phone_column"] is False
        assert body["has_email_column"] is True
        assert body["channels"]["email_only"] == 1

    @pytest.mark.asyncio
    async def test_preview_counts_rows_that_have_nothing(self, db):
        from app.services.csv_service import CSVImportService

        body = await CSVImportService(db).preview_csv(
            b"Name,Phone,Email\nNobody,,\n"
        )
        assert body["channels"]["neither"] == 1


class TestEmailCampaignReachesEmailOnlyContacts:
    """The whole point, end to end: an email-only contact gets emailed.

    Audience population used to run the SMS eligibility gate unconditionally,
    so an email campaign built from a list silently dropped every contact with
    no phone number. These tests pin the channel-aware gate.
    """

    @staticmethod
    async def _list_with(db, *contacts):
        from app.models.contact_list import ContactList, ContactListMember

        contact_list = ContactList(name="Restaurants")
        db.add(contact_list)
        await db.flush()
        for contact in contacts:
            db.add(contact)
        await db.flush()
        for contact in contacts:
            db.add(ContactListMember(list_id=contact_list.id, contact_id=contact.id))
        await db.flush()
        return contact_list

    @pytest.mark.asyncio
    async def test_email_campaign_includes_a_contact_that_has_no_phone(self, db):
        from app.models.campaign import Campaign
        from app.services.campaign_service import CampaignService

        contact_list = await self._list_with(
            db,
            Contact(first_name="Ada", email="ada@acmefoods.ng"),
            Contact(first_name="Chidi", phone_number="+2348039999999"),
        )
        campaign = Campaign(name="Blast", channel="email", list_id=contact_list.id,
                            status="scheduled", message_body="Hello", subject="Hi")
        db.add(campaign)
        await db.flush()

        await CampaignService(db).start_campaign(campaign.id)

        assert campaign.total_contacts == 1, "the email-only contact, not the phone-only one"

    @pytest.mark.asyncio
    async def test_sms_campaign_still_excludes_a_contact_that_has_no_phone(self, db):
        from app.models.campaign import Campaign
        from app.services.campaign_service import CampaignService

        contact_list = await self._list_with(
            db,
            Contact(first_name="Ada", email="ada@acmefoods.ng"),
            Contact(first_name="Chidi", phone_number="+2348039999999"),
        )
        campaign = Campaign(name="Blast", channel="sms", list_id=contact_list.id,
                            status="scheduled", message_body="Hello")
        db.add(campaign)
        await db.flush()

        await CampaignService(db).start_campaign(campaign.id)

        assert campaign.total_contacts == 1, "the phone-only contact"

    @pytest.mark.asyncio
    async def test_email_consent_does_not_block_sms_and_vice_versa(self, db):
        """Two channels, two consents — an email opt-out is not an SMS opt-out."""
        from app.models.campaign import Campaign
        from app.services.campaign_service import CampaignService

        contact_list = await self._list_with(
            db,
            Contact(first_name="Bola", phone_number="+2348031234567",
                    email="bola@acmefoods.ng", is_email_opted_out=True),
            Contact(first_name="Ngozi", phone_number="+2348032222222",
                    email="ngozi@acmefoods.ng"),
        )

        email_campaign = Campaign(name="Email", channel="email", list_id=contact_list.id,
                                  status="scheduled", message_body="Hi", subject="Hi")
        sms_campaign = Campaign(name="SMS", channel="sms", list_id=contact_list.id,
                                status="scheduled", message_body="Hi")
        db.add_all([email_campaign, sms_campaign])
        await db.flush()
        service = CampaignService(db)
        await service.start_campaign(email_campaign.id)
        await service.start_campaign(sms_campaign.id)

        assert email_campaign.total_contacts == 1, "the opted-out address is skipped"
        assert sms_campaign.total_contacts == 2, "an email opt-out does not stop an SMS"

    @pytest.mark.asyncio
    async def test_email_campaign_skips_bounced_and_unusable_addresses(self, db):
        from app.models.campaign import Campaign
        from app.services.campaign_service import CampaignService

        contact_list = await self._list_with(
            db,
            Contact(first_name="Fine", phone_number="+2348031111111", email="fine@acmefoods.ng"),
            Contact(first_name="Bounced", phone_number="+2348032222222",
                    email="bounced@acmefoods.ng", is_email_undeliverable=True),
            Contact(first_name="Placeholder", phone_number="+2348033333333",
                    email="someone@example.com"),
        )
        campaign = Campaign(name="Blast", channel="email", list_id=contact_list.id,
                            status="scheduled", message_body="Hello", subject="Hi")
        db.add(campaign)
        await db.flush()

        await CampaignService(db).start_campaign(campaign.id)

        assert campaign.total_contacts == 1, "only the good address"


class TestGuessedAddressesAreHeldBackFromSends:
    """A guessed address is stored, badged, and NOT sent to by default.

    The standing rule for this feature: an inferred address is never silently
    treated as confirmed. Storing it is useful; emailing it by default is not —
    one bounce is charged against the reputation of every address the app sends.
    """

    @pytest.mark.asyncio
    async def test_a_guessed_address_is_not_emailable(self, db):
        from app.services import email_service

        contact = Contact(phone_number=None, email="ada@acmefoods.ng",
                          email_source="inferred")
        db.add(contact)
        await db.flush()

        assert await email_service.contact_email_problem(db, contact) == "inferred_unverified"

    @pytest.mark.asyncio
    async def test_a_guessed_address_can_be_opted_in(self, db, monkeypatch):
        from app.config import settings
        from app.services import email_service

        contact = Contact(phone_number=None, email="ada@acmefoods.ng",
                          email_source="inferred")
        db.add(contact)
        await db.flush()

        monkeypatch.setattr(settings, "EMAIL_SEND_INFERRED", True)
        assert await email_service.contact_email_problem(db, contact) is None

    @pytest.mark.asyncio
    async def test_a_user_supplied_address_is_still_emailable_unverified(self, db):
        """Only *guesses* are held back. An address the user typed is theirs."""
        from app.services import email_service

        contact = Contact(phone_number=None, email="ada@acmefoods.ng",
                          email_source="manual", email_verified=False)
        db.add(contact)
        await db.flush()

        assert await email_service.contact_email_problem(db, contact) is None

    @pytest.mark.asyncio
    async def test_a_found_address_is_emailable(self, db):
        from app.services import email_service

        contact = Contact(phone_number=None, email="ada@acmefoods.ng",
                          email_source="hunter", email_verified=True)
        db.add(contact)
        await db.flush()

        assert await email_service.contact_email_problem(db, contact) is None

    @pytest.mark.asyncio
    async def test_a_guessed_address_is_held_back_from_an_email_campaign(self, db):
        from app.models.campaign import Campaign
        from app.models.contact_list import ContactList, ContactListMember
        from app.services.campaign_service import CampaignService

        contact_list = ContactList(name="Restaurants")
        db.add(contact_list)
        await db.flush()
        guessed = Contact(first_name="Ada", email="ada@acmefoods.ng", email_source="inferred")
        known = Contact(first_name="Bola", email="bola@acmefoods.ng", email_source="manual")
        db.add_all([guessed, known])
        await db.flush()
        db.add_all([ContactListMember(list_id=contact_list.id, contact_id=c.id)
                    for c in (guessed, known)])
        await db.flush()

        campaign = Campaign(name="Blast", channel="email", list_id=contact_list.id,
                            status="scheduled", message_body="Hi", subject="Hi")
        db.add(campaign)
        await db.flush()
        await CampaignService(db).start_campaign(campaign.id)

        assert campaign.total_contacts == 1, "only the user-supplied address"

    @pytest.mark.asyncio
    async def test_a_guessed_address_is_still_listed_by_the_inferred_filter(self, db, client):
        """Held back from sends, not hidden from the operator."""
        db.add(Contact(phone_number=None, email="ada@acmefoods.ng", email_source="inferred"))
        await db.flush()

        r = await client.get("/api/v1/contacts/", params={"email_state": "inferred"})
        assert r.status_code == 200
        assert r.json()["total"] == 1
