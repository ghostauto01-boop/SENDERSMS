"""Contact variable registry: discovery, short codes, and safe rendering.

The behaviour these lock down is the one the operator actually asked for:

* Import a CSV with any columns (Pain Point, Niche, States) and those columns
  become usable message variables with no extra step.
* The operator can rename the short code, and messages written with that short
  code resolve against the right contact field.
* A short code a contact has no value for is REMOVED from the message. Sending
  the literal text "{{Business_name}}" to a customer is the bug being fixed.
"""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact
from app.models.user import User
from app.models.variable import ContactVariable
from app.security.auth import get_current_user
from app.services.variable_service import (
    analyze_message,
    render_for_contact,
    sync_variables_from_contacts,
    variable_maps,
)
from app.utils.templating import normalize_key, render_template


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
        id=1, username="tester", email="t@example.com",
        password_hash="x", role="admin", is_active=True,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def make_contact(db, phone="+2348031234567", custom=None, **kw):
    contact = Contact(phone_number=phone, country="Nigeria", **kw)
    if custom:
        contact.custom_fields = json.dumps(custom)
    db.add(contact)
    await db.flush()
    return contact


class TestNormalizeKey:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Pain Point", "pain_point"),
            ("pain-point", "pain_point"),
            ("  PAIN_POINT  ", "pain_point"),
            ("Pain  Point", "pain_point"),
            ("Business Name", "business_name"),
            ("niche", "niche"),
        ],
    )
    def test_every_spelling_collapses(self, raw, expected):
        assert normalize_key(raw) == expected


class TestDiscovery:
    @pytest.mark.asyncio
    async def test_imported_columns_become_variables(self, db):
        await make_contact(
            db, custom={"Pain Point": "No online orders", "Niche": "Restaurant"}
        )
        result = await sync_variables_from_contacts(db)
        assert result["discovered"] >= 2

        keys = set((await db.execute(select(ContactVariable.field_key))).scalars().all())
        assert "pain_point" in keys
        assert "niche" in keys

    @pytest.mark.asyncio
    async def test_a_second_import_adds_only_the_new_columns(self, db):
        await make_contact(db, custom={"Pain Point": "x"})
        await sync_variables_from_contacts(db)

        # A later CSV introduces different columns entirely.
        await make_contact(db, phone="+2348031234568", custom={"States": "Lagos", "Problem": "Slow"})
        await sync_variables_from_contacts(db)

        keys = set((await db.execute(select(ContactVariable.field_key))).scalars().all())
        assert {"pain_point", "states", "problem"} <= keys

    @pytest.mark.asyncio
    async def test_standard_fields_are_always_registered(self, db):
        await sync_variables_from_contacts(db)
        keys = set((await db.execute(select(ContactVariable.field_key))).scalars().all())
        assert {"first_name", "business_name", "city", "website"} <= keys

    @pytest.mark.asyncio
    async def test_usage_counts_are_reported(self, db):
        await make_contact(db, custom={"Pain Point": "A"})
        await make_contact(db, phone="+2348031234568", custom={"Pain Point": "B"})
        await make_contact(db, phone="+2348031234569", custom={"Niche": "Salon"})
        await sync_variables_from_contacts(db)

        pain = (
            await db.execute(
                select(ContactVariable).where(ContactVariable.field_key == "pain_point")
            )
        ).scalar_one()
        assert pain.contact_count == 2
        assert pain.sample_value in ("A", "B")

    @pytest.mark.asyncio
    async def test_sync_preserves_an_operator_chosen_shortcode(self, db):
        await make_contact(db, custom={"Pain Point": "A"})
        await sync_variables_from_contacts(db)

        pain = (
            await db.execute(
                select(ContactVariable).where(ContactVariable.field_key == "pain_point")
            )
        ).scalar_one()
        pain.shortcode = "problem"
        pain.fallback_text = "your current setup"
        await db.flush()

        await sync_variables_from_contacts(db)
        await db.refresh(pain)
        assert pain.shortcode == "problem"
        assert pain.fallback_text == "your current setup"


class TestRendering:
    @pytest.mark.asyncio
    async def test_imported_field_renders_with_spaced_shortcode(self, db):
        contact = await make_contact(
            db, first_name="Ada", custom={"Pain Point": "no online orders"}
        )
        await sync_variables_from_contacts(db)
        out = await render_for_contact(
            db, "Hi {{first_name}}, still struggling with {{Pain Point}}?", contact
        )
        assert out == "Hi Ada, still struggling with no online orders?"

    @pytest.mark.asyncio
    async def test_custom_shortcode_maps_to_the_right_field(self, db):
        contact = await make_contact(db, custom={"Pain Point": "slow delivery"})
        await sync_variables_from_contacts(db)
        pain = (
            await db.execute(
                select(ContactVariable).where(ContactVariable.field_key == "pain_point")
            )
        ).scalar_one()
        pain.shortcode = "issue"
        await db.flush()

        out = await render_for_contact(db, "About {{issue}}.", contact)
        assert out == "About slow delivery."

    @pytest.mark.asyncio
    async def test_unknown_shortcode_is_removed_entirely(self, db):
        """The headline bug: {{Business_name}} must never reach a handset."""
        contact = await make_contact(db, first_name="Ada")
        await sync_variables_from_contacts(db)

        out = await render_for_contact(
            db, "Hi {{first_name}}, does {{Business_name}} need help?", contact
        )
        assert "{{" not in out and "}}" not in out
        assert "Business_name" not in out
        assert out == "Hi Ada, does need help?"

    @pytest.mark.asyncio
    async def test_known_but_empty_shortcode_is_removed(self, db):
        contact = await make_contact(db, first_name="Ada", custom={"Niche": ""})
        await sync_variables_from_contacts(db)
        out = await render_for_contact(db, "Hi {{first_name}} in {{niche}}.", contact)
        assert "{{" not in out
        assert "niche" not in out.lower()

    @pytest.mark.asyncio
    async def test_configured_fallback_is_used_when_empty(self, db):
        contact = await make_contact(db, first_name="Ada")
        await make_contact(db, phone="+2348031234599", custom={"Niche": "Salon"})
        await sync_variables_from_contacts(db)

        niche = (
            await db.execute(
                select(ContactVariable).where(ContactVariable.field_key == "niche")
            )
        ).scalar_one()
        niche.fallback_text = "your industry"
        await db.flush()

        out = await render_for_contact(db, "Great for {{niche}}.", contact)
        assert out == "Great for your industry."

    @pytest.mark.asyncio
    async def test_deactivated_variable_stops_resolving(self, db):
        contact = await make_contact(db, custom={"Niche": "Salon"})
        await sync_variables_from_contacts(db)
        niche = (
            await db.execute(
                select(ContactVariable).where(ContactVariable.field_key == "niche")
            )
        ).scalar_one()
        niche.is_active = False
        await db.flush()

        aliases, _ = await variable_maps(db)
        assert "niche" not in aliases

    @pytest.mark.asyncio
    async def test_no_braces_survive_any_combination(self, db):
        contact = await make_contact(db, first_name="Ada", custom={"Niche": "Salon"})
        await sync_variables_from_contacts(db)
        bodies = [
            "Hi {{first_name}} — {{niche}} — {{totally_unknown}}",
            "{{ Pain Point }} and {{ niche }}",
            "{{unknown_one}}{{unknown_two}}",
        ]
        for body in bodies:
            out = await render_for_contact(db, body, contact)
            assert "{{" not in out and "}}" not in out


class TestTidyAfterRemoval:
    def test_dangling_punctuation_is_repaired(self):
        out = render_template("Hi {{first_name}}, about {{unknown}}, thanks", None)
        assert "  " not in out
        assert ",," not in out
        assert "{{" not in out

    def test_empty_parentheses_are_dropped(self):
        out = render_template("Call us ({{unknown}}) today", None)
        assert "()" not in out
        assert "{{" not in out


class TestAnalyzeMessage:
    @pytest.mark.asyncio
    async def test_reports_resolved_empty_and_unknown(self, db):
        contact = await make_contact(
            db, first_name="Ada", custom={"Pain Point": "slow site", "Niche": ""}
        )
        await sync_variables_from_contacts(db)

        result = await analyze_message(
            db,
            "Hi {{first_name}}, {{Pain Point}} in {{niche}} — {{made_up}}?",
            contact,
        )
        resolved = {item["field_key"] for item in result["resolved"]}
        empty = {item["field_key"] for item in result["empty"]}

        assert "first_name" in resolved and "pain_point" in resolved
        assert "niche" in empty
        assert result["unknown"] == ["made_up"]
        assert "{{" not in result["preview"]


class TestVariablesAPI:
    @pytest.mark.asyncio
    async def test_list_discovers_and_returns_variables(self, client, db):
        await make_contact(db, custom={"Pain Point": "x", "Niche": "Salon"})
        response = await client.get("/api/v1/variables/")
        assert response.status_code == 200
        shortcodes = {item["shortcode"] for item in response.json()["items"]}
        assert {"pain_point", "niche", "first_name"} <= shortcodes

    @pytest.mark.asyncio
    async def test_update_shortcode(self, client, db):
        await make_contact(db, custom={"Pain Point": "x"})
        await sync_variables_from_contacts(db)
        variable = (
            await db.execute(
                select(ContactVariable).where(ContactVariable.field_key == "pain_point")
            )
        ).scalar_one()

        response = await client.put(
            f"/api/v1/variables/{variable.id}",
            json={"shortcode": "Pain", "fallback_text": "your problem"},
        )
        assert response.status_code == 200
        assert response.json()["shortcode"] == "pain"
        assert response.json()["fallback_text"] == "your problem"

    @pytest.mark.asyncio
    async def test_duplicate_shortcode_is_rejected(self, client, db):
        await make_contact(db, custom={"Pain Point": "x", "Niche": "y"})
        await sync_variables_from_contacts(db)
        niche = (
            await db.execute(
                select(ContactVariable).where(ContactVariable.field_key == "niche")
            )
        ).scalar_one()

        response = await client.put(
            f"/api/v1/variables/{niche.id}", json={"shortcode": "pain_point"}
        )
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_standard_variable_cannot_be_deleted(self, client, db):
        await sync_variables_from_contacts(db)
        first_name = (
            await db.execute(
                select(ContactVariable).where(ContactVariable.field_key == "first_name")
            )
        ).scalar_one()
        response = await client.delete(f"/api/v1/variables/{first_name.id}")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_preview_endpoint_flags_bad_shortcodes(self, client, db):
        contact = await make_contact(db, first_name="Ada")
        response = await client.post(
            "/api/v1/variables/preview",
            params={"body": "Hi {{first_name}} at {{Business_name}}", "contact_id": contact.id},
        )
        assert response.status_code == 200
        data = response.json()
        assert "{{" not in data["preview"]
        assert "business_name" in data["will_remove"]

    @pytest.mark.asyncio
    async def test_contact_profile_shows_every_imported_field(self, client, db):
        contact = await make_contact(
            db,
            first_name="Ada",
            website="ada.ng",
            custom={"Pain Point": "no orders", "Niche": "Restaurant"},
        )
        response = await client.get(f"/api/v1/variables/contact/{contact.id}/profile")
        assert response.status_code == 200
        data = response.json()
        by_key = {field["field_key"]: field for field in data["fields"]}

        assert by_key["first_name"]["value"] == "Ada"
        assert by_key["website"]["value"] == "ada.ng"
        # Everything the CSV brought in is visible on the profile.
        assert by_key["pain_point"]["value"] == "no orders"
        assert by_key["niche"]["value"] == "Restaurant"
        assert by_key["pain_point"]["label"] == "Pain Point"
