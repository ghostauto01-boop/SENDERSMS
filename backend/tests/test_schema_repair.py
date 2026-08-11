"""Regression tests for startup schema auto-repair.

Background: SQLAlchemy's create_all() creates missing TABLES but never adds a
missing COLUMN to a table that already exists. On a database with existing data
that meant a newly added model column silently never appeared, and every query
touching it failed with "no such column" / "column does not exist" at runtime --
while the service itself started up looking perfectly healthy.

That is exactly how the Campaigns page broke: campaigns.scheduled_start_at was
missing from the live database, so GET /api/v1/campaigns/ returned HTTP 500,
even though the auto_reply_rules TABLE had been created automatically.
"""
import asyncio
import sqlite3

import pytest
from sqlalchemy import Column, Integer, String, Boolean, MetaData, Table
from sqlalchemy.ext.asyncio import create_async_engine

from app.schema_repair import repair_schema_sync, _literal_default


def _columns(db_path: str, table: str) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def _make_metadata(with_new_columns: bool) -> MetaData:
    md = MetaData()
    cols = [
        Column("id", Integer, primary_key=True),
        Column("name", String(50)),
    ]
    if with_new_columns:
        cols += [
            Column("scheduled_start_at", String(50), nullable=True),
            Column("is_auto_reply", Boolean, nullable=False, default=False),
        ]
    Table("widgets", md, *cols)
    return md


async def _run_repair(db_path: str, metadata: MetaData) -> list[str]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with engine.begin() as conn:
            return await conn.run_sync(repair_schema_sync, metadata)
    finally:
        await engine.dispose()


def test_adds_missing_columns_to_existing_table(tmp_path):
    """The core bug: an existing table missing new columns gets them added."""
    db = str(tmp_path / "old.db")

    # Simulate the un-migrated production database: table exists, with data,
    # but without the columns that were added to the model later.
    asyncio.run(_run_repair(db, _make_metadata(with_new_columns=False)))
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name VARCHAR(50))")
    con.execute("INSERT INTO widgets (name) VALUES ('existing row')")
    con.commit()
    con.close()

    assert "scheduled_start_at" not in _columns(db, "widgets")

    added = asyncio.run(_run_repair(db, _make_metadata(with_new_columns=True)))

    assert "widgets.scheduled_start_at" in added
    assert "widgets.is_auto_reply" in added
    cols = _columns(db, "widgets")
    assert "scheduled_start_at" in cols
    assert "is_auto_reply" in cols


def test_existing_rows_are_preserved_and_backfilled(tmp_path):
    """Repair must not lose data, and NOT NULL columns must not leave NULLs."""
    db = str(tmp_path / "data.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name VARCHAR(50))")
    con.executemany("INSERT INTO widgets (name) VALUES (?)", [("a",), ("b",), ("c",)])
    con.commit()
    con.close()

    asyncio.run(_run_repair(db, _make_metadata(with_new_columns=True)))

    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM widgets").fetchone()[0] == 3
        assert [r[0] for r in con.execute("SELECT name FROM widgets ORDER BY id")] == ["a", "b", "c"]
        # default=False must be materialised as a real DB default so that
        # pre-existing rows are not NULL in a NOT NULL column.
        nulls = con.execute(
            "SELECT COUNT(*) FROM widgets WHERE is_auto_reply IS NULL"
        ).fetchone()[0]
        assert nulls == 0
    finally:
        con.close()


def test_is_idempotent_and_noop_when_schema_current(tmp_path):
    """Running against an up-to-date schema changes nothing."""
    db = str(tmp_path / "current.db")
    md = _make_metadata(with_new_columns=True)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(md.create_all)
        await engine.dispose()

    asyncio.run(_create())

    assert asyncio.run(_run_repair(db, md)) == []
    assert asyncio.run(_run_repair(db, md)) == []


def test_missing_table_is_skipped_not_fatal(tmp_path):
    """Tables absent entirely are create_all()'s job, not ours -- never crash."""
    db = str(tmp_path / "empty.db")
    md = _make_metadata(with_new_columns=True)
    # No tables created at all.
    assert asyncio.run(_run_repair(db, md)) == []


def test_real_models_repair_campaign_scheduled_start_at(tmp_path):
    """End-to-end against the real Campaign model that actually broke."""
    from app.database import Base
    import app.models  # noqa: F401  (registers all models on Base.metadata)

    db = str(tmp_path / "real.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")

    async def _create_full():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_create_full())

    # Recreate the production breakage exactly.
    con = sqlite3.connect(db)
    con.execute("DROP INDEX IF EXISTS ix_campaigns_scheduled_start_at")
    con.execute("ALTER TABLE campaigns DROP COLUMN scheduled_start_at")
    con.commit()
    con.close()
    assert "scheduled_start_at" not in _columns(db, "campaigns")

    added = asyncio.run(_run_repair(db, Base.metadata))

    assert "campaigns.scheduled_start_at" in added
    assert "scheduled_start_at" in _columns(db, "campaigns")


@pytest.mark.parametrize(
    "default, expected",
    [
        (False, "FALSE"),
        (True, "TRUE"),
        (0, "0"),
        (7, "7"),
        ("hi", "'hi'"),
        ("it's", "'it''s'"),  # quotes must be escaped
    ],
)
def test_literal_default_scalars(default, expected):
    col = Column("c", String(10), default=default)
    assert _literal_default(col) == expected


def test_literal_default_ignores_callables_and_none():
    """Callable defaults (e.g. datetime.utcnow) have no safe SQL literal."""
    assert _literal_default(Column("c", String(10))) is None
    assert _literal_default(Column("c", String(10), default=lambda: "x")) is None
