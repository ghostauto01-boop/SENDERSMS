"""End-to-end regression tests for PostgreSQL schema upgrade, Contacts, Dashboard & Analytics.

Covers the exact production failure chain where upgrading a populated PostgreSQL
database across PR #66/#67 left Contacts, Dashboard, Analytics, Overview, Inbox,
and Calendar broken:
  1. ``_literal_default`` emitted ``DEFAULT FALSE``/``TRUE`` on ``INTEGER`` flag
     columns (``meetings.send_invite_email``, ``followups.is_overdue``, etc.),
     raising ``DatatypeMismatchError`` on PostgreSQL and rolling back the entire
     un-savepointed ``init_db()`` transaction (losing ``campaigns.plain_personal_email``,
     ``messages.status_polled_at``, ``email_events.is_bot``, etc.).
  2. ``_add_column_sql`` checked ``"DEFAULT" not in ddl.upper()``, matching column
     names containing ``"default"`` (``email_accounts.is_default``,
     ``mcp_connections.default_scope``) and omitting their ``DEFAULT`` on
     populated tables.
  3. ``0001_meetings_email_columns`` / ``migrate_existing_db.sql`` created
     ``meetings`` flag columns as ``BOOLEAN`` while ``Meeting`` maps ``Integer``
     and queries ``Meeting.send_email_reminder == 1``.
  4. Legacy ``contacts`` rows holding ``NULL`` in scalar flags / timestamps
     crashed ``ContactOut`` with Pydantic ``ValidationError`` (HTTP 500) and
     were silently excluded by ``is_(False)`` SQL filters.
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pgserver
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base, _normalize_database_url, get_db
from app.models.ads import AdsCampaign
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.models.meeting import Meeting
from app.models.user import User
from app.schema_repair import _add_column_sql, repair_schema_sync, verify_schema_sync
from app.security.auth import get_current_user


def test_every_model_column_add_sql_executes_on_populated_sqlite():
    """Every non-PK column across all 67 models must ALTER cleanly onto a non-empty SQLite table."""
    eng = create_engine("sqlite:///:memory:")
    failures = []
    with eng.connect() as conn:
        for table in Base.metadata.sorted_tables:
            for col in table.columns:
                if col.primary_key:
                    continue
                sql = _add_column_sql(conn, table.name, col)
                raw = sqlite3.connect(":memory:")
                try:
                    raw.execute(f'CREATE TABLE "{table.name}" (id INTEGER PRIMARY KEY)')
                    raw.execute(f'INSERT INTO "{table.name}" (id) VALUES (1)')
                    raw.execute(sql)
                except Exception as exc:  # pragma: no cover
                    failures.append((table.name, col.name, sql, str(exc)))
                finally:
                    raw.close()
    eng.dispose()
    assert failures == []


@pytest.mark.asyncio
async def test_every_model_column_add_sql_executes_on_populated_postgres(tmp_path):
    """Every non-PK column across all 67 models must ALTER cleanly onto a non-empty PostgreSQL table."""
    srv = pgserver.get_server(str(tmp_path / "pg_audit"))
    try:
        uri = srv.get_uri().replace("postgresql://", "postgresql+asyncpg://", 1)
        eng = create_async_engine(uri)
        failures = []
        try:
            async with eng.begin() as conn:
                def _audit(sync_conn):
                    for table in Base.metadata.sorted_tables:
                        q_tbl = sync_conn.dialect.identifier_preparer.quote(table.name)
                        sync_conn.execute(text(f"CREATE TABLE {q_tbl} (id SERIAL PRIMARY KEY)"))
                        sync_conn.execute(text(f"INSERT INTO {q_tbl} DEFAULT VALUES"))
                        for col in table.columns:
                            if col.name == "id":
                                continue
                            sql = _add_column_sql(sync_conn, table.name, col)
                            try:
                                with sync_conn.begin_nested():
                                    sync_conn.execute(text(sql))
                            except Exception as exc:  # pragma: no cover
                                failures.append((table.name, col.name, sql, repr(exc)))
                        sync_conn.execute(text(f"DROP TABLE {q_tbl}"))

                await conn.run_sync(_audit)
        finally:
            await eng.dispose()
        assert failures == []
    finally:
        srv.cleanup()


@pytest.mark.asyncio
async def test_pre_pr67_postgres_upgrade_restores_all_pages(tmp_path, monkeypatch):
    """Simulate a populated pre-PR-#67 PostgreSQL DB and verify every page API recovers."""
    srv = pgserver.get_server(str(tmp_path / "pg_upgrade"))
    try:
        uri = srv.get_uri().replace("postgresql://", "postgresql+asyncpg://", 1)
        eng = create_async_engine(uri)
        factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)

        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        now = datetime.now(timezone.utc)
        async with factory() as db:
            admin = User(
                id=1,
                username="admin",
                email="admin@sendify.test",
                password_hash="x",
                role="admin",
                is_active=True,
            )
            c1 = Contact(
                phone_number="+2348031110001",
                first_name="Ada",
                last_name="Okafor",
                email="ada@lagos.ng",
                email_lower="ada@lagos.ng",
                lead_status="interested",
            )
            camp = Campaign(name="Q4 Promo", channel="email", status="draft", message_body="Hello")
            ads = AdsCampaign(name="Lagos Email Blast", channel="email", status="draft")
            meet = Meeting(
                title="Discovery Call",
                starts_at=now + timedelta(minutes=20),
                ends_at=now + timedelta(minutes=50),
                send_email_reminder=1,
                send_sms_reminder=1,
            )
            db.add_all([admin, c1, camp, ads, meet])
            await db.commit()

        # Roll schema back to pre-PR-#67 + legacy 0001/0002 drift + duplicate threads + NULL contact flags.
        async with eng.begin() as conn:
            for ddl in [
                "ALTER TABLE campaigns DROP COLUMN IF EXISTS plain_personal_email",
                "ALTER TABLE campaigns DROP COLUMN IF EXISTS timezone_name",
                "ALTER TABLE campaigns DROP COLUMN IF EXISTS allowed_weekdays",
                "ALTER TABLE campaigns ALTER COLUMN sender_daily_limits TYPE TEXT USING sender_daily_limits::text",
                "ALTER TABLE ads_campaigns DROP COLUMN IF EXISTS plain_personal_email",
                "ALTER TABLE ads_campaigns ALTER COLUMN sender_daily_limits TYPE TEXT USING sender_daily_limits::text",
                "ALTER TABLE email_accounts DROP COLUMN IF EXISTS last_webhook_event_at",
                "ALTER TABLE email_accounts DROP COLUMN IF EXISTS is_default",
                "ALTER TABLE mcp_connections DROP COLUMN IF EXISTS default_scope",
                "ALTER TABLE email_events DROP COLUMN IF EXISTS is_bot",
                "ALTER TABLE email_events DROP COLUMN IF EXISTS bot_reason",
                "ALTER TABLE email_events DROP COLUMN IF EXISTS occurred_at",
                "ALTER TABLE messages DROP COLUMN IF EXISTS status_polled_at",
                "ALTER TABLE meetings DROP COLUMN IF EXISTS send_invite_email",
                "ALTER TABLE meetings ALTER COLUMN send_email_reminder DROP DEFAULT",
                "ALTER TABLE meetings ALTER COLUMN send_email_reminder TYPE BOOLEAN USING (send_email_reminder <> 0)",
                "ALTER TABLE conversations DROP CONSTRAINT IF EXISTS uq_conversation_contact_channel",
                "DROP INDEX IF EXISTS uq_conversation_contact_channel",
                "ALTER TABLE contacts ALTER COLUMN is_email_opted_out DROP NOT NULL",
                "ALTER TABLE contacts ALTER COLUMN is_email_undeliverable DROP NOT NULL",
                "ALTER TABLE contacts ALTER COLUMN is_undeliverable DROP NOT NULL",
                "ALTER TABLE contacts ALTER COLUMN email_verified DROP NOT NULL",
                "ALTER TABLE contacts ALTER COLUMN created_at DROP NOT NULL",
                "ALTER TABLE contacts ALTER COLUMN updated_at DROP NOT NULL",
                "ALTER TABLE campaigns ALTER COLUMN updated_at DROP NOT NULL",
            ]:
                await conn.execute(text(ddl))

            # Insert a legacy contact with NULLs in boolean/timestamp columns + duplicate threads.
            await conn.execute(
                text(
                    "INSERT INTO contacts (phone_number, first_name, email, email_lower, "
                    "lead_status, consent_status, has_consented, is_opted_out, is_undeliverable, "
                    "delivery_fail_count, is_email_opted_out, email_status, is_email_undeliverable, "
                    "email_fail_count, emails_sent, emails_received, email_verified, "
                    "messages_sent, messages_received, created_at, updated_at) "
                    "VALUES ('+2348031110002', 'LegacyChidi', 'chidi@abuja.ng', 'chidi@abuja.ng', "
                    "'new', 'unknown', false, false, NULL, 0, NULL, 'active', NULL, 0, 0, 0, NULL, "
                    "0, 0, NULL, NULL)"
                )
            )
            await conn.execute(text("UPDATE campaigns SET updated_at = NULL"))
            cid = (await conn.execute(text("SELECT id FROM contacts WHERE first_name='Ada'"))).scalar_one()
            await conn.execute(
                text(
                    "INSERT INTO conversations (contact_id, channel, status, unread_count, "
                    "message_count, sequence_paused, created_at, updated_at) "
                    "VALUES (:cid, 'sms', 'active', 1, 1, false, now(), now()), "
                    "       (:cid, 'sms', 'active', 0, 1, false, now(), now())"
                ),
                {"cid": cid},
            )

        # Prime a pooled connection BEFORE repair to verify dispose() avoids InvalidCachedStatementError.
        async with factory() as s:
            await s.execute(select(Contact))

        async with eng.begin() as conn:
            repaired = await conn.run_sync(repair_schema_sync, Base.metadata)
            missing = await conn.run_sync(verify_schema_sync, Base.metadata)
        await eng.dispose()

        assert missing == [], f"Schema still missing columns: {missing}"
        assert "campaigns.plain_personal_email" in repaired
        assert "meetings.send_invite_email" in repaired
        assert "email_accounts.is_default" in repaired
        assert "mcp_connections.default_scope" in repaired

        # Second pass must be a clean no-op.
        async with eng.begin() as conn:
            assert await conn.run_sync(repair_schema_sync, Base.metadata) == []

        # Verify every major API route against the upgraded PostgreSQL database!
        from app import database as db_mod
        from app import main as main_mod
        from app.main import app

        monkeypatch.setattr(db_mod, "async_session_factory", factory)
        monkeypatch.setattr(main_mod, "async_session_factory", factory)

        async def _override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = lambda: User(
            id=1, username="admin", email="admin@sendify.test",
            password_hash="x", role="admin", is_active=True,
        )
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                for path in (
                    "/api/v1/health/db",
                    "/api/v1/contacts/",
                    "/api/v1/contacts",
                    "/api/v1/contacts/?email_state=emailable",
                    "/api/v1/contacts/?undeliverable=false",
                    "/api/v1/dashboard/stats",
                    "/api/v1/dashboard/charts?days=7",
                    "/api/v1/analytics/overview?days=30",
                    "/api/v1/analytics/timeseries?days=30",
                    "/api/v1/analytics/funnel?days=30",
                    "/api/v1/analytics/by-campaign",
                    "/api/v1/analytics/calls?days=30",
                    "/api/v1/overview/campaigns?channel=sms",
                    "/api/v1/overview/campaigns?channel=email",
                    "/api/v1/overview/metrics?channel=sms",
                    "/api/v1/overview/metrics?channel=email",
                    "/api/v1/campaigns/",
                    "/api/v1/campaigns",
                    "/api/v1/lists",
                    "/api/v1/inbox/conversations",
                    "/api/v1/calendar/upcoming",
                    "/api/v1/system/health",
                ):
                    resp = await client.get(path)
                    assert resp.status_code == 200, f"{path} failed ({resp.status_code}): {resp.text}"

                contacts_body = (await client.get("/api/v1/contacts/?email_state=emailable")).json()
                names = {c["first_name"] for c in contacts_body["items"]}
                assert {"Ada", "LegacyChidi"} <= names

                # Also verify Meeting.send_email_reminder == 1 runs cleanly on PostgreSQL!
                async with factory() as s:
                    rows = (
                        await s.execute(select(Meeting).where(Meeting.send_email_reminder == 1))
                    ).scalars().all()
                    assert len(rows) == 1
        finally:
            app.dependency_overrides.clear()
            await eng.dispose()
    finally:
        srv.cleanup()


@pytest.mark.asyncio
async def test_alembic_migrations_on_postgres(tmp_path):
    """Alembic 0001 + 0002 run cleanly on a postgres:// URL and produce INTEGER meeting flags."""
    from app.run_migrations import current_revision, upgrade_head

    srv = pgserver.get_server(str(tmp_path / "pg_alembic"))
    try:
        sep = "&" if "?" in srv.get_uri() else "?"
        raw_pg_url = srv.get_uri().replace("postgresql://", "postgres://", 1) + f"{sep}sslmode=disable"
        async_uri, cargs = _normalize_database_url(raw_pg_url)
        eng = create_async_engine(async_uri, connect_args=cargs)
        try:
            async with eng.begin() as conn:
                await conn.execute(
                    text(
                        "CREATE TABLE meetings ("
                        "id SERIAL PRIMARY KEY, title VARCHAR(255) NOT NULL, "
                        "status VARCHAR(30) DEFAULT 'scheduled', "
                        "starts_at TIMESTAMPTZ NOT NULL, ends_at TIMESTAMPTZ NOT NULL, "
                        "send_email_reminder BOOLEAN NOT NULL DEFAULT TRUE)"
                    )
                )
                await conn.execute(
                    text(
                        "CREATE TABLE campaigns ("
                        "id SERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL, "
                        "sender_daily_limits TEXT)"
                    )
                )

            await asyncio.to_thread(upgrade_head, raw_pg_url)
            rev = await asyncio.to_thread(current_revision, raw_pg_url)
            assert rev == "0002_p0_p1_additive_columns"

            async with eng.begin() as conn:
                types = dict(
                    (
                        await conn.execute(
                            text(
                                "SELECT column_name, data_type FROM information_schema.columns "
                                "WHERE table_name IN ('meetings', 'campaigns') "
                                "AND column_name IN ('send_invite_email', 'send_email_reminder', 'sender_daily_limits')"
                            )
                        )
                    ).all()
                )
            assert types["send_invite_email"] == "integer"
            assert types["send_email_reminder"] == "integer"
            assert types["sender_daily_limits"] == "json"
        finally:
            await eng.dispose()
    finally:
        srv.cleanup()


def test_normalize_database_url_pooler_and_timeouts():
    url, args = _normalize_database_url(
        "postgres://u:p@ep-cool-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require&pgbouncer=true"
    )
    assert url == "postgresql+asyncpg://u:p@ep-cool-pooler.us-east-2.aws.neon.tech/neondb"
    assert args["ssl"] is True
    assert args["statement_cache_size"] == 0
    assert args["timeout"] >= 25
    assert args["command_timeout"] >= 60


def test_worker_loop_never_closes_between_tasks():
    from app.tasks._loop import get_worker_loop, run_in_worker_loop

    async def _step(x: int) -> int:
        await asyncio.sleep(0)
        return x + 1

    assert run_in_worker_loop(_step(1)) == 2
    loop1 = get_worker_loop()
    assert not loop1.is_closed()
    assert run_in_worker_loop(_step(41)) == 42
    assert get_worker_loop() is loop1
    assert not loop1.is_closed()
