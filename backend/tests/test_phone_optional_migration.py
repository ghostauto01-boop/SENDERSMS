"""A database created before this release must accept an email-only contact.

``Base.metadata.create_all`` never alters a table that already exists, so the
``contacts.phone_number NOT NULL`` constraint written by every earlier version
survives a deploy. Without the repair below, this feature would work perfectly
on a fresh install and fail on the live database with a NOT NULL violation —
the worst possible way to find out.
"""

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.schema_repair import repair_schema_sync
from app.models.contact import Contact  # noqa: F401 - registers the table


LEGACY_SCHEMA = """
CREATE TABLE contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name VARCHAR(150),
    last_name VARCHAR(150),
    business_name VARCHAR(255),
    phone_number VARCHAR(20) NOT NULL UNIQUE,
    email VARCHAR(255),
    city VARCHAR(150),
    state VARCHAR(150),
    country VARCHAR(100) NOT NULL DEFAULT 'Nigeria',
    website VARCHAR(500),
    industry VARCHAR(255),
    source VARCHAR(255),
    lead_status VARCHAR(50) NOT NULL DEFAULT 'new',
    consent_status VARCHAR(50) NOT NULL DEFAULT 'unknown',
    has_consented BOOLEAN NOT NULL DEFAULT 0,
    is_opted_out BOOLEAN NOT NULL DEFAULT 0,
    opted_out_at DATETIME,
    opt_out_reason VARCHAR(500),
    is_undeliverable BOOLEAN NOT NULL DEFAULT 0,
    undeliverable_reason VARCHAR(500),
    delivery_fail_count INTEGER NOT NULL DEFAULT 0,
    is_email_opted_out BOOLEAN NOT NULL DEFAULT 0,
    email_opted_out_at DATETIME,
    email_opt_out_reason VARCHAR(500),
    email_status VARCHAR(30) NOT NULL DEFAULT 'active',
    is_email_undeliverable BOOLEAN NOT NULL DEFAULT 0,
    email_fail_count INTEGER NOT NULL DEFAULT 0,
    email_last_error VARCHAR(500),
    notes TEXT,
    custom_fields TEXT,
    messages_sent INTEGER NOT NULL DEFAULT 0,
    messages_received INTEGER NOT NULL DEFAULT 0,
    emails_sent INTEGER NOT NULL DEFAULT 0,
    emails_received INTEGER NOT NULL DEFAULT 0,
    last_emailed_at DATETIME,
    last_email_reply_at DATETIME,
    last_contacted_at DATETIME,
    last_reply_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


@pytest.mark.asyncio
async def test_legacy_not_null_is_relaxed_and_an_email_only_row_inserts():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(LEGACY_SCHEMA))
        # An existing customer, exactly as the old schema demanded.
        await conn.execute(
            text(
                "INSERT INTO contacts (phone_number, first_name, country, lead_status,"
                " consent_status, has_consented, is_opted_out, is_undeliverable,"
                " delivery_fail_count, is_email_opted_out, email_status,"
                " is_email_undeliverable, email_fail_count, messages_sent,"
                " messages_received, emails_sent, emails_received, created_at, updated_at)"
                " VALUES ('+2348031234567', 'Ada', 'Nigeria', 'new', 'unknown', 0, 0, 0,"
                " 0, 0, 'active', 0, 0, 0, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

        def _columns(sync_conn):
            return {c["name"]: c for c in inspect(sync_conn).get_columns("contacts")}

        before = await conn.run_sync(_columns)
        assert before["phone_number"]["nullable"] is False, "precondition"

        # create_all builds the tables this legacy dump does not include
        # (contact_tags, lists, ...); repair then migrates the legacy ones.
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(repair_schema_sync, Base.metadata)

        after = await conn.run_sync(_columns)
        assert after["phone_number"]["nullable"] is True, "NOT NULL was relaxed"
        # New enrichment columns arrived with the same repair run.
        for column in (
            "email_source", "email_verified", "email_verified_at",
            "email_confidence", "email_enriched_at", "email_enrichment_note",
            "email_lower",
        ):
            assert column in after, f"{column} was added"

        # The pre-existing row is untouched...
        row = (await conn.execute(text("SELECT phone_number, first_name FROM contacts"))).all()
        assert row == [("+2348031234567", "Ada")]

    # ...and the email-only contact that could never be stored before, can be.
    # Inserted through the ORM, the way the importer does it, so the models'
    # Python-side defaults apply.
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(Contact(
            phone_number=None, email="hello@acme.ng", email_lower="hello@acme.ng",
            first_name="Acme", email_source="csv",
        ))
        # Many email-only contacts coexist: SQL treats NULLs as distinct in a
        # UNIQUE index, so the phone uniqueness rule does not collide.
        session.add(Contact(
            phone_number=None, email="second@acme.ng", first_name="Second",
            email_source="csv",
        ))
        await session.commit()

        rows = (
            await session.execute(
                text("SELECT email FROM contacts WHERE phone_number IS NULL ORDER BY email")
            )
        ).all()
        assert rows == [("hello@acme.ng",), ("second@acme.ng",)]
        # Defaults came through on the new columns.
        contact = (
            await session.execute(select(Contact).where(Contact.email == "hello@acme.ng"))
        ).scalar_one()
        assert contact.email_verified is False
        assert contact.phone_number is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_repair_is_idempotent():
    """A second boot on an already-repaired database changes nothing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(LEGACY_SCHEMA))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(repair_schema_sync, Base.metadata)
        # Report only real work: on an already-migrated schema there is none.
        second = await conn.run_sync(repair_schema_sync, Base.metadata)
        assert second == []
    await engine.dispose()


#: The shape a *sparse* legacy database really has: only the columns the old
#: release knew about, with no defaults on the ones later releases added. This
#: is the case the model-shaped table rebuild could not handle — it reimposed
#: NOT NULL on `created_at` and friends, where these rows legitimately hold NULL
#: because `_add_column_sql` adds such columns nullable on purpose.
SPARSE_LEGACY_SCHEMA = """
CREATE TABLE contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name VARCHAR(150),
    phone_number VARCHAR(20) NOT NULL UNIQUE,
    email VARCHAR(255),
    is_opted_out BOOLEAN DEFAULT 0,
    is_undeliverable BOOLEAN DEFAULT 0
)
"""


@pytest.mark.asyncio
async def test_a_sparse_legacy_table_is_still_relaxed():
    """Columns added by an earlier repair hold NULL — the relax must survive it.

    A full model-shaped rebuild fails here with
    ``NOT NULL constraint failed: contacts__repair.created_at``, which silently
    left ``phone_number`` NOT NULL and made every email-only insert fail on the
    databases this repair exists for.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(SPARSE_LEGACY_SCHEMA))
        await conn.execute(
            text(
                "INSERT INTO contacts (phone_number, first_name) "
                "VALUES ('+2348031234567', 'Ada')"
            )
        )
        await conn.run_sync(Base.metadata.create_all)

        # One boot: the missing columns are added (nullable, so the row that is
        # already there holds NULL in them) and phone_number is relaxed in the
        # same pass. The relaxation must not be defeated by those NULLs.
        def _nullable(sync_conn):
            columns = {c["name"]: c for c in inspect(sync_conn).get_columns("contacts")}
            return columns["phone_number"]["nullable"]

        assert await conn.run_sync(_nullable) is False, "precondition"
        await conn.run_sync(repair_schema_sync, Base.metadata)
        assert await conn.run_sync(_nullable) is True, "NOT NULL was relaxed"

        # ...and a second boot has nothing left to do.
        second = await conn.run_sync(repair_schema_sync, Base.metadata)
        assert [a for a in second if "relax" in a] == []

        # Every column the rebuild did not touch keeps its own DEFAULT, so a
        # client that omits them still inserts successfully.
        await conn.execute(
            text("INSERT INTO contacts (first_name, email) VALUES ('Chidi', 'chidi@acme.ng')")
        )
        rows = (
            await conn.execute(
                text("SELECT first_name, country FROM contacts ORDER BY id")
            )
        ).all()
        assert rows[0] == ("Ada", None), "an unknown country stays unknown"
        assert rows[1] == ("Chidi", None), "the newly added country column has no assumed default"

    await engine.dispose()
