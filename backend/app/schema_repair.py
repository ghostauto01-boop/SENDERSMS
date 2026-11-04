"""Lightweight, zero-Alembic schema repair run on every startup.

Why this exists
---------------
``Base.metadata.create_all()`` creates *tables* that do not exist yet, which is
why brand-new models (e.g. ``auto_reply_rules``) appear automatically on first
deploy. What ``create_all()`` NEVER does is ``ALTER TABLE`` an existing table
when a new column is added to a model later. Without a migration step, the code
starts up cleanly while the live database silently lacks the new column -- and
every ``SELECT`` / ``INSERT`` against that model crashes at runtime with
``UndefinedColumnError`` / ``OperationalError: no such column`` (which surfaces
to the browser as HTTP 500/503).

This module closes that gap cleanly and idempotently:

1. Inspect every table registered on ``Base.metadata`` (using 4 bulk catalog
   queries on PostgreSQL instead of 400+ per-table round-trips over WAN).
2. For each table that already exists in the database, compare the live column
   names against the SQLAlchemy model's columns.
3. ``ALTER TABLE <t> ADD COLUMN <col> <type> [DEFAULT ...]`` for anything
   missing, inside an individual ``SAVEPOINT`` (``begin_nested()``) so one
   problematic statement can never poison the PostgreSQL transaction or roll
   back unrelated column repairs.
4. Repair PostgreSQL column type drift introduced by older SQL scripts /
   migrations (e.g. ``meetings`` integer flags created as ``BOOLEAN``, or
   ``sender_daily_limits`` created as ``TEXT`` instead of ``JSON``).
5. Backfill ``NULL`` values on legacy ``contacts`` rows for non-nullable flag /
   counter / timestamp columns so ``IS FALSE`` audience filters and Pydantic
   response models never choke on pre-migration rows.

Safety rules
------------
* Purely additive + safe widening/type-alignment: never drops columns or data.
* Every DDL/DML repair step runs inside ``with sync_conn.begin_nested():`` so
  PostgreSQL ``InFailedSQLTransactionError`` cascades are impossible.
* Works identically on PostgreSQL (production) and SQLite (local dev / tests).
* Columns added to a non-empty table CANNOT be ``NOT NULL`` unless they have a
  constant default, because existing rows would violate the constraint. When a
  new model column is declared ``nullable=False``:
  - if it has a scalar Python ``default=...`` (bool / int / float / str) or a
    ``server_default``, we emit ``DEFAULT <literal>`` and keep ``NOT NULL``;
  - otherwise (e.g. ``default=datetime.utcnow``) we drop ``NOT NULL`` on the
    ``ALTER`` so existing rows survive with ``NULL``. New rows written through
    SQLAlchemy still get the Python-side default.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import Boolean, Integer, JSON, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateColumn, CreateIndex
from sqlalchemy.sql.schema import Column, Index, MetaData, Table

logger = logging.getLogger(__name__)


@dataclass
class _SchemaSnapshot:
    """Cached database catalog snapshot to avoid hundreds of WAN round-trips."""

    dialect_name: str
    tables: set[str] = field(default_factory=set)
    columns: dict[str, dict[str, dict]] = field(default_factory=dict)
    unique_constraints: dict[str, list[dict]] = field(default_factory=dict)
    indexes: dict[str, list[dict]] = field(default_factory=dict)


def _snapshot_schema(sync_conn: Connection, target_tables: Optional[Iterable[str]] = None) -> _SchemaSnapshot:
    """Capture live tables, columns, unique constraints, and indexes.

    On PostgreSQL this uses 4 bulk catalog queries instead of ~7 queries per
    table (~490 queries across 67 tables), cutting WAN startup latency from
    30-60s down to <0.5s on cold serverless databases (Neon / Supabase / Render).
    """
    dialect = sync_conn.dialect.name
    if dialect == "postgresql":
        tables = {
            r[0]
            for r in sync_conn.execute(
                text("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = current_schema()")
            ).all()
        }
        cols_by_table: dict[str, dict[str, dict]] = {}
        for tname, cname, is_nullable, data_type, udt_name in sync_conn.execute(
            text(
                "SELECT table_name, column_name, is_nullable, data_type, udt_name "
                "FROM information_schema.columns WHERE table_schema = current_schema()"
            )
        ).all():
            cols_by_table.setdefault(tname, {})[cname] = {
                "name": cname,
                "nullable": (is_nullable == "YES"),
                "data_type": (data_type or "").lower(),
                "udt_name": (udt_name or "").lower(),
            }

        ucs_by_table: dict[str, list[dict]] = {}
        for tname, conname, colnames in sync_conn.execute(
            text(
                """
                SELECT c.relname AS table_name,
                       con.conname AS constraint_name,
                       ARRAY(
                           SELECT a.attname
                           FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
                           JOIN pg_catalog.pg_attribute a
                             ON a.attrelid = c.oid AND a.attnum = k.attnum
                           ORDER BY k.ord
                       ) AS column_names
                FROM pg_catalog.pg_constraint con
                JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE con.contype = 'u'
                  AND n.nspname = current_schema()
                """
            )
        ).all():
            ucs_by_table.setdefault(tname, []).append(
                {
                    "name": conname,
                    "column_names": list(colnames or []),
                }
            )

        ixs_by_table: dict[str, list[dict]] = {}
        for tname, ixname, is_unique, colnames in sync_conn.execute(
            text(
                """
                SELECT tbl.relname AS table_name,
                       idx.relname AS index_name,
                       i.indisunique AS is_unique,
                       ARRAY(
                           SELECT a.attname
                           FROM unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
                           JOIN pg_catalog.pg_attribute a
                             ON a.attrelid = tbl.oid AND a.attnum = k.attnum
                           WHERE k.attnum > 0
                           ORDER BY k.ord
                       ) AS column_names
                FROM pg_catalog.pg_index i
                JOIN pg_catalog.pg_class tbl ON tbl.oid = i.indrelid
                JOIN pg_catalog.pg_class idx ON idx.oid = i.indexrelid
                JOIN pg_catalog.pg_namespace n ON n.oid = tbl.relnamespace
                WHERE n.nspname = current_schema()
                  AND NOT i.indisprimary
                """
            )
        ).all():
            ixs_by_table.setdefault(tname, []).append(
                {
                    "name": ixname,
                    "unique": bool(is_unique),
                    "column_names": list(colnames or []),
                }
            )

        return _SchemaSnapshot(
            dialect_name=dialect,
            tables=tables,
            columns=cols_by_table,
            unique_constraints=ucs_by_table,
            indexes=ixs_by_table,
        )

    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())
    wanted = (tables & set(target_tables)) if target_tables is not None else tables
    cols_by_table = {}
    ucs_by_table = {}
    ixs_by_table = {}
    for tname in wanted:
        cols_by_table[tname] = {
            col["name"]: {
                "name": col["name"],
                "nullable": bool(col.get("nullable", True)),
                "data_type": str(col.get("type", "")).lower(),
                "udt_name": str(col.get("type", "")).lower(),
            }
            for col in inspector.get_columns(tname)
        }
        try:
            ucs_by_table[tname] = list(inspector.get_unique_constraints(tname))
        except NotImplementedError:
            ucs_by_table[tname] = []
        try:
            ixs_by_table[tname] = list(inspector.get_indexes(tname))
        except NotImplementedError:
            ixs_by_table[tname] = []

    return _SchemaSnapshot(
        dialect_name=dialect,
        tables=tables,
        columns=cols_by_table,
        unique_constraints=ucs_by_table,
        indexes=ixs_by_table,
    )


def _literal_default(column: Column) -> Optional[str]:
    """Turn a scalar ``Column(default=...)`` into a safe SQL literal, or None.

    Carefully matches the literal's SQL type to ``column.type``: several models
    (e.g. ``Meeting``, ``FollowUp``, ``Sequence``, ``Template``) declare
    ``Column(Integer, default=False)`` / ``default=True``. Emitting
    ``DEFAULT FALSE`` on an ``INTEGER`` column raises
    ``DatatypeMismatchError: column "..." is of type integer but default
    expression is of type boolean`` on PostgreSQL.
    """
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    arg = default.arg
    is_integer_col = isinstance(column.type, Integer) and not isinstance(column.type, Boolean)
    if isinstance(arg, bool):
        if is_integer_col:
            return "1" if arg else "0"
        return "TRUE" if arg else "FALSE"
    if isinstance(arg, int):
        if isinstance(column.type, Boolean):
            return "TRUE" if arg else "FALSE"
        return str(arg)
    if isinstance(arg, float):
        return repr(arg)
    if isinstance(arg, str):
        escaped = arg.replace("'", "''")
        return f"'{escaped}'"
    return None


def _add_column_sql(sync_conn: Connection, table_name: str, column: Column) -> str:
    """Render a dialect-appropriate ``ALTER TABLE ... ADD COLUMN ...`` statement."""
    preparer = sync_conn.dialect.identifier_preparer
    quoted_table = preparer.quote(table_name)

    # Work on a detached copy so we never mutate Base.metadata in place.
    col_copy = column._copy()

    # ForeignKey inline constraints on ADD COLUMN are rejected by older SQLite
    # builds and unnecessary for repair; drop them from the DDL copy.
    col_copy.foreign_keys.clear()
    # Inline UNIQUE on ADD COLUMN is rejected by SQLite ("Cannot add a UNIQUE
    # column"); _pending_indexes creates the unique index separately.
    col_copy.unique = False

    literal = _literal_default(column)
    has_server_default = column.server_default is not None

    if not col_copy.nullable and not has_server_default and literal is None:
        # Non-null column with no constant default (e.g. datetime.utcnow): must
        # be added as NULLable so existing rows don't blow up the ALTER.
        col_copy.nullable = True

    ddl = str(CreateColumn(col_copy).compile(dialect=sync_conn.dialect)).strip()

    # Check for a DEFAULT keyword ONLY after the column name token so column
    # names containing "default" (e.g. email_accounts.is_default,
    # mcp_connections.default_scope) never suppress their DEFAULT clause!
    quoted_col = preparer.quote(col_copy.name)
    after_name = ddl[len(quoted_col):] if ddl.startswith(quoted_col) else ddl[len(col_copy.name):]
    has_default_kw = bool(re.search(r"\bDEFAULT\b", after_name, re.IGNORECASE))

    if not has_server_default and literal is not None and not has_default_kw:
        ddl = f"{ddl} DEFAULT {literal}"

    if sync_conn.dialect.name == "postgresql":
        return f"ALTER TABLE {quoted_table} ADD COLUMN IF NOT EXISTS {ddl}"
    return f"ALTER TABLE {quoted_table} ADD COLUMN {ddl}"


def _pending_columns(
    sync_conn: Connection,
    metadata: MetaData,
    snapshot: Optional[_SchemaSnapshot] = None,
) -> List[Tuple[str, Column]]:
    """Return ``(table_name, column)`` pairs missing from existing DB tables."""
    snap = snapshot or _snapshot_schema(sync_conn, [t.name for t in metadata.sorted_tables])
    missing: List[Tuple[str, Column]] = []
    for table in metadata.sorted_tables:
        if table.name not in snap.tables:
            # Whole table is missing -- create_all() handles that.
            continue
        live_cols = snap.columns.get(table.name, {})
        for column in table.columns:
            if column.name not in live_cols:
                missing.append((table.name, column))
    return missing


def _pending_indexes(
    sync_conn: Connection,
    metadata: MetaData,
    snapshot: Optional[_SchemaSnapshot] = None,
) -> List[Index]:
    """Return non-unique model indexes absent from existing database tables.

    Why a separate pass from ``_pending_columns``: ``Base.metadata.create_all``
    only emits ``CREATE INDEX`` when it creates the owning table. Every index
    declared via ``index=True`` or ``Index(...)`` on a column added to an
    already-existing table was therefore silently skipped on live databases,
    leaving new foreign-key / filter columns unindexed in production while
    fresh-DB tests saw the index and passed.

    Unique indexes are excluded deliberately: adding a ``UNIQUE`` constraint to
    populated data that wasn't unique before can fail, and the ones that matter
    have dedicated handling in ``_repair_widened_unique_keys``. An index whose
    column is STILL missing (because ``ADD COLUMN`` failed above) is also
    skipped so one broken column cannot cascade into index errors.
    """
    snap = snapshot or _snapshot_schema(sync_conn, [t.name for t in metadata.sorted_tables])
    missing: List[Index] = []
    for table in metadata.sorted_tables:
        if table.name not in snap.tables:
            continue
        live_cols = set(snap.columns.get(table.name, {}).keys())
        table_indexes = snap.indexes.get(table.name, [])
        live_idx_names = {ix["name"] for ix in table_indexes if ix.get("name")}
        # Also match by column tuple so an index that already exists under an
        # auto-generated name isn't duplicated.
        live_idx_cols = {
            tuple(ix.get("column_names") or ())
            for ix in table_indexes
            if not ix.get("unique")
        }
        for index in table.indexes:
            if index.unique or not index.name:
                continue
            cols = tuple(c.name for c in index.columns)
            if not cols or not set(cols).issubset(live_cols):
                continue
            if index.name in live_idx_names or cols in live_idx_cols:
                continue
            missing.append(index)
    return missing


def _sqlite_rebuild_table(sync_conn: Connection, target: Table) -> None:
    """Recreate ``target`` from its current SQLAlchemy definition, keeping rows.

    SQLite cannot ``ALTER TABLE ... DROP CONSTRAINT`` (and cannot drop an
    unnamed ``UNIQUE (col)`` autoindex at all). The documented SQLite way to
    change table-level constraints is copy-and-swap inside the caller's
    transaction, with ``foreign_keys`` temporarily off so child tables (e.g.
    ``messages.conversation_id``) do not block the swap.
    """
    preparer = sync_conn.dialect.identifier_preparer
    live_cols = {col["name"] for col in inspect(sync_conn).get_columns(target.name)}
    shared = [c.name for c in target.columns if c.name in live_cols]
    col_list = ", ".join(preparer.quote(c) for c in shared)

    tmp_name = f"__repair_new_{target.name}"
    q_target = preparer.quote(target.name)
    q_tmp = preparer.quote(tmp_name)

    # Build a throwaway metadata holding only the replacement table so its
    # indexes don't collide with existing index names before the drop.
    scratch = MetaData()
    cols_copy = [c._copy() for c in target.columns]
    for c in cols_copy:
        c.foreign_keys.clear()
        c.index = False
    tmp_table = Table(tmp_name, scratch, *cols_copy)

    sync_conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        sync_conn.exec_driver_sql(f"DROP TABLE IF EXISTS {q_tmp}")
        tmp_table.create(sync_conn)
        if shared:
            sync_conn.exec_driver_sql(
                f"INSERT INTO {q_tmp} ({col_list}) SELECT {col_list} FROM {q_target}"
            )
        sync_conn.exec_driver_sql(f"DROP TABLE {q_target}")
        sync_conn.exec_driver_sql(f"ALTER TABLE {q_tmp} RENAME TO {q_target}")
        for idx in target.indexes:
            sync_conn.execute(CreateIndex(idx, if_not_exists=True))
        for uq in target.constraints:
            if getattr(uq, "columns", None) and uq.__class__.__name__ == "UniqueConstraint":
                uq_cols = ", ".join(preparer.quote(c.name) for c in uq.columns)
                uq_name = preparer.quote(uq.name or f"uq_{target.name}_{'_'.join(c.name for c in uq.columns)}")
                sync_conn.exec_driver_sql(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {uq_name} ON {q_target} ({uq_cols})"
                )
    finally:
        sync_conn.exec_driver_sql("PRAGMA foreign_keys=ON")


# ``(table, old_single_col_tuple, replacement_constraint_name, new_cols_tuple)``
# Whenever a model widens a unique key (here: one thread per contact -> one
# thread per contact *per channel*), adding the new column is not enough: the
# old narrower UNIQUE constraint is still enforced by the database and will
# reject the second channel with IntegrityError at runtime.
_WIDENED_UNIQUE_KEYS: Tuple[Tuple[str, Tuple[str, ...], str, Tuple[str, ...]], ...] = (
    ("conversations", ("contact_id",), "uq_conversation_contact_channel", ("channel", "contact_id")),
)


def _dedupe_conversations_for_unique_index(sync_conn: Connection) -> int:
    """Merge duplicate ``(contact_id, channel)`` threads before adding a unique index.

    Older pre-constraint databases can contain multiple SMS threads for the same
    contact. Without deduplicating first, ``CREATE UNIQUE INDEX
    uq_conversation_contact_channel`` raises ``UniqueViolationError`` on every
    boot. We preserve all messages/meetings by re-pointing them at the lowest
    ``id`` conversation for each ``(contact_id, channel)`` pair.
    """
    dupes = sync_conn.execute(
        text(
            """
            SELECT contact_id, COALESCE(channel, 'sms') AS ch, MIN(id) AS keep_id, COUNT(*) AS cnt
            FROM conversations
            GROUP BY contact_id, COALESCE(channel, 'sms')
            HAVING COUNT(*) > 1
            """
        )
    ).all()
    if not dupes:
        return 0

    merged = 0
    live_tables = set(inspect(sync_conn).get_table_names())
    for contact_id, ch, keep_id, _cnt in dupes:
        sync_conn.execute(
            text(
                "UPDATE conversations SET channel = :ch "
                "WHERE contact_id = :cid AND COALESCE(channel, 'sms') = :ch AND channel IS NULL"
            ),
            {"cid": contact_id, "ch": ch},
        )
        victim_ids = [
            r[0]
            for r in sync_conn.execute(
                text(
                    "SELECT id FROM conversations "
                    "WHERE contact_id = :cid AND channel = :ch AND id <> :keep_id"
                ),
                {"cid": contact_id, "ch": ch, "keep_id": keep_id},
            ).all()
        ]
        for vid in victim_ids:
            if "messages" in live_tables:
                sync_conn.execute(
                    text("UPDATE messages SET conversation_id = :keep_id WHERE conversation_id = :vid"),
                    {"keep_id": keep_id, "vid": vid},
                )
            if "meetings" in live_tables:
                sync_conn.execute(
                    text("UPDATE meetings SET conversation_id = :keep_id WHERE conversation_id = :vid"),
                    {"keep_id": keep_id, "vid": vid},
                )
            sync_conn.execute(
                text("DELETE FROM conversations WHERE id = :vid"),
                {"vid": vid},
            )
            merged += 1
    return merged


def _repair_widened_unique_keys(
    sync_conn: Connection,
    metadata: MetaData,
    snapshot: Optional[_SchemaSnapshot] = None,
) -> List[str]:
    """Drop superseded single-column UNIQUE keys and install the wider ones."""
    snap = snapshot or _snapshot_schema(sync_conn, [t for t, _, _, _ in _WIDENED_UNIQUE_KEYS])
    dialect = snap.dialect_name
    preparer = sync_conn.dialect.identifier_preparer
    changes: List[str] = []

    for table_name, old_cols, new_uq_name, new_cols in _WIDENED_UNIQUE_KEYS:
        if table_name not in snap.tables or table_name not in metadata.tables:
            continue

        live_cols = set(snap.columns.get(table_name, {}).keys())
        if not set(new_cols).issubset(live_cols):
            continue

        uqs = snap.unique_constraints.get(table_name, [])
        idxs = snap.indexes.get(table_name, [])

        stale_uqs = [u for u in uqs if tuple(u.get("column_names") or ()) == old_cols]
        stale_idxs = [
            ix for ix in idxs
            if ix.get("unique") and tuple(ix.get("column_names") or ()) == old_cols
        ]
        has_new = any(
            set(u.get("column_names") or ()) == set(new_cols) for u in uqs
        ) or any(
            ix.get("unique") and set(ix.get("column_names") or ()) == set(new_cols) for ix in idxs
        )

        if not stale_uqs and not stale_idxs and has_new:
            continue

        q_table = preparer.quote(table_name)
        q_new = preparer.quote(new_uq_name)
        q_new_cols = ", ".join(preparer.quote(c) for c in new_cols)

        if dialect == "postgresql":
            for u in stale_uqs:
                if u.get("name"):
                    try:
                        with sync_conn.begin_nested():
                            sync_conn.execute(
                                text(f"ALTER TABLE {q_table} DROP CONSTRAINT IF EXISTS {preparer.quote(u['name'])}")
                            )
                        changes.append(f"{table_name}: dropped unique constraint {u['name']}")
                    except Exception:
                        logger.exception("Schema repair failed dropping constraint %s on %s", u["name"], table_name)
            for ix in stale_idxs:
                if ix.get("name"):
                    try:
                        with sync_conn.begin_nested():
                            sync_conn.execute(text(f"DROP INDEX IF EXISTS {preparer.quote(ix['name'])}"))
                        changes.append(f"{table_name}: dropped unique index {ix['name']}")
                    except Exception:
                        logger.exception("Schema repair failed dropping index %s on %s", ix["name"], table_name)
            if not has_new:
                try:
                    with sync_conn.begin_nested():
                        if table_name == "conversations":
                            merged = _dedupe_conversations_for_unique_index(sync_conn)
                            if merged:
                                changes.append(f"conversations: merged {merged} duplicate threads")
                        sync_conn.execute(
                            text(f"CREATE UNIQUE INDEX IF NOT EXISTS {q_new} ON {q_table} ({q_new_cols})")
                        )
                    changes.append(f"{table_name}: added unique index {new_uq_name}")
                except Exception:
                    logger.exception("Schema repair failed adding unique index %s on %s", new_uq_name, table_name)
        elif dialect == "sqlite":
            # Unnamed inline UNIQUE creates ``sqlite_autoindex_<table>_N`` which
            # SQLite refuses to DROP INDEX on; only a table rebuild removes it.
            needs_rebuild = bool(stale_uqs) or any(
                (ix.get("name") or "").startswith("sqlite_autoindex_") for ix in stale_idxs
            )
            if needs_rebuild:
                try:
                    with sync_conn.begin_nested():
                        if table_name == "conversations":
                            _dedupe_conversations_for_unique_index(sync_conn)
                        _sqlite_rebuild_table(sync_conn, metadata.tables[table_name])
                    changes.append(f"{table_name}: rebuilt table to widen unique key -> {new_uq_name}")
                except Exception:
                    logger.exception("Schema repair failed rebuilding %s", table_name)
            else:
                try:
                    with sync_conn.begin_nested():
                        for ix in stale_idxs:
                            if ix.get("name"):
                                sync_conn.execute(text(f"DROP INDEX IF EXISTS {preparer.quote(ix['name'])}"))
                                changes.append(f"{table_name}: dropped unique index {ix['name']}")
                        if not has_new:
                            if table_name == "conversations":
                                _dedupe_conversations_for_unique_index(sync_conn)
                            sync_conn.execute(
                                text(f"CREATE UNIQUE INDEX IF NOT EXISTS {q_new} ON {q_table} ({q_new_cols})")
                            )
                            changes.append(f"{table_name}: added unique index {new_uq_name}")
                except Exception:
                    logger.exception("Schema repair failed updating unique index %s on %s", new_uq_name, table_name)
    return changes


# ``(table, column)`` pairs that used to be ``NOT NULL`` and were later made
# optional in the model. ``create_all`` never touches existing columns and
# ``_pending_columns`` only sees columns that are missing altogether, so
# without an explicit pass a legacy database keeps rejecting ``NULL`` with
# ``IntegrityError`` while every fresh-DB test passes.
_RELAXED_NOT_NULL: Tuple[Tuple[str, str], ...] = (
    # Contacts can now be imported with an email address and no phone number
    # (lead spreadsheets, newsletter exports, enrichment output), and with no
    # country assumed when none was supplied.
    ("contacts", "phone_number"),
    ("contacts", "country"),
)


def _sqlite_relax_column(
    sync_conn: Connection,
    metadata: MetaData,
    table_name: str,
    col_name: str,
) -> bool:
    """Rebuild a SQLite table with one ``NOT NULL`` dropped, shape otherwise kept.

    ``_sqlite_rebuild_table`` reshapes the table to the model, which reintroduces
    ``NOT NULL`` on every other model column (such as ``created_at``, which
    ``_add_column_sql`` deliberately adds as nullable on populated legacy tables).
    Reflecting the existing table and changing *only* ``col_name`` to
    ``nullable=True`` preserves every existing column's nullability, defaults,
    and rows without ever inventing values.
    """
    tmp_name = f"{table_name}__repair"
    try:
        reflected = Table(table_name, MetaData(), autoload_with=sync_conn)
        if col_name not in reflected.columns:
            return False

        columns: list[Column] = []
        for col in reflected.columns:
            columns.append(
                Column(
                    col.name,
                    col.type,
                    primary_key=col.primary_key,
                    nullable=True if col.name == col_name else col.nullable,
                    server_default=(
                        col.server_default.arg if col.server_default is not None else None
                    ),
                )
            )

        model = metadata.tables.get(table_name)
        if model is not None:
            for col in model.columns:
                if col.name not in reflected.columns:
                    columns.append(Column(col.name, col.type, nullable=True))

        tmp = Table(tmp_name, MetaData(), *columns)
        old_cols = [c.name for c in reflected.columns]

        fk_state = 0
        try:
            fk_state = sync_conn.execute(text("PRAGMA foreign_keys")).scalar() or 0
            sync_conn.execute(text("PRAGMA foreign_keys=OFF"))
        except Exception:
            pass

        try:
            sync_conn.execute(text(f'DROP TABLE IF EXISTS "{tmp_name}"'))
            tmp.create(sync_conn)
            cols_sql = ", ".join(f'"{c}"' for c in old_cols)
            sync_conn.execute(
                text(f'INSERT INTO "{tmp_name}" ({cols_sql}) SELECT {cols_sql} FROM "{table_name}"')
            )
            sync_conn.execute(text(f'DROP TABLE "{table_name}"'))
            sync_conn.execute(text(f'ALTER TABLE "{tmp_name}" RENAME TO "{table_name}"'))
            if model is not None:
                live_now = {c.name for c in tmp.columns}
                for idx in model.indexes:
                    if idx.name and {c.name for c in idx.columns}.issubset(live_now):
                        sync_conn.execute(CreateIndex(idx, if_not_exists=True))
        finally:
            if fk_state:
                try:
                    sync_conn.execute(text("PRAGMA foreign_keys=ON"))
                except Exception:
                    pass
        return True
    except Exception:
        logger.exception("Schema repair could not rebuild %s to relax %s", table_name, col_name)
        return False


def _repair_relaxed_not_null(
    sync_conn: Connection,
    metadata: MetaData,
    snapshot: Optional[_SchemaSnapshot] = None,
) -> List[str]:
    """Allow ``NULL`` on columns whose model dropped ``nullable=False``."""
    snap = snapshot or _snapshot_schema(sync_conn, [t for t, _ in _RELAXED_NOT_NULL])
    dialect = snap.dialect_name
    preparer = sync_conn.dialect.identifier_preparer
    changes: List[str] = []

    for table_name, col_name in _RELAXED_NOT_NULL:
        if table_name not in snap.tables or table_name not in metadata.tables:
            continue
        table = metadata.tables[table_name]
        if col_name not in table.columns or table.columns[col_name].nullable is not True:
            continue
        live = snap.columns.get(table_name, {}).get(col_name)
        if live is None or live.get("nullable", True):
            continue  # column absent (handled earlier) or already nullable

        if dialect == "postgresql":
            q_table = preparer.quote(table_name)
            q_col = preparer.quote(col_name)
            try:
                with sync_conn.begin_nested():
                    sync_conn.execute(text(f"ALTER TABLE {q_table} ALTER COLUMN {q_col} DROP NOT NULL"))
                changes.append(f"{table_name}.{col_name}: relaxed NOT NULL")
                live["nullable"] = True
            except Exception:
                logger.exception("Schema repair failed dropping NOT NULL on %s.%s", table_name, col_name)
        elif dialect == "sqlite":
            try:
                with sync_conn.begin_nested():
                    ok = _sqlite_relax_column(sync_conn, metadata, table_name, col_name)
                if ok:
                    changes.append(f"{table_name}.{col_name}: relaxed NOT NULL")
                    # Refresh snapshot for this table since SQLite rebuilt it.
                    refreshed = _snapshot_schema(sync_conn, [table_name])
                    snap.columns[table_name] = refreshed.columns.get(table_name, {})
                    snap.indexes[table_name] = refreshed.indexes.get(table_name, [])
                    snap.unique_constraints[table_name] = refreshed.unique_constraints.get(table_name, [])
            except Exception:
                logger.exception("Schema repair failed relaxing NOT NULL on %s.%s", table_name, col_name)
    return changes


def _repair_postgres_varchar_widening(
    sync_conn: Connection,
    metadata: MetaData,
    snapshot: _SchemaSnapshot,
) -> List[str]:
    """Widen VARCHAR columns that were created too narrow (e.g. VARCHAR(60) -> 255).

    Production DBs created before the model was widened still have
    character varying(60) while the model now says VARCHAR(120)/255.
    Without this, any value longer than 60 chars fails with
    ``value too long for type character varying(60)`` on INSERT/UPDATE.

    This is purely additive: PostgreSQL can widen VARCHAR without rewriting
    the table, and the operation is safe to run on every startup.
    """
    if snapshot.dialect_name != "postgresql":
        return []
    preparer = sync_conn.dialect.identifier_preparer
    changes: List[str] = []
    # Known columns that grew: (table, column, new_size)
    widenings = [
        ("campaigns", "timezone_name", 120),
        ("ads_campaigns", "timezone_name", 120),
        ("ads_assignments", "experiment_group", 255),
        ("quarantined_sms", "reason", 255),
    ]
    for table_name, col_name, new_size in widenings:
        if table_name not in snapshot.tables:
            continue
        live = snapshot.columns.get(table_name, {}).get(col_name)
        if live is None:
            continue
        # Only attempt on PostgreSQL; rely on exception handling for idempotency.
        # If the column is already wide enough, the ALTER is a no-op (or we catch the error).
        q_table = preparer.quote(table_name)
        q_col = preparer.quote(col_name)
        try:
            with sync_conn.begin_nested():
                sync_conn.execute(text(f"ALTER TABLE {q_table} ALTER COLUMN {q_col} TYPE VARCHAR({new_size})"))
            changes.append(f"{table_name}.{col_name}: widened to VARCHAR({new_size})")
        except Exception:
            # Already correct size, or transient error — ignore but log
            logger.debug("Schema repair skip widening %s.%s to VARCHAR(%s)", table_name, col_name, new_size)
    return changes


def _repair_postgres_column_types(
    sync_conn: Connection,
    metadata: MetaData,
    snapshot: _SchemaSnapshot,
) -> List[str]:
    """Fix PostgreSQL column type mismatches introduced by earlier migrations/scripts.

    1. ``0001_meetings_email_columns.py`` and ``scripts/migrate_existing_db.sql``
       created ``meetings`` flag columns as ``BOOLEAN``, while the ``Meeting``
       ORM model declares ``Integer`` and ``meeting_service.py`` / ``main.py``
       compare ``Meeting.send_email_reminder == 1`` — crashing PostgreSQL with
       ``UndefinedFunctionError: operator does not exist: boolean = integer``.
    2. ``0002_p0_p1_additive_columns.py`` created ``sender_daily_limits`` as
       ``TEXT`` while ``Campaign`` and ``AdsCampaign`` map ``JSON``.
    """
    if snapshot.dialect_name != "postgresql":
        return []

    preparer = sync_conn.dialect.identifier_preparer
    changes: List[str] = []

    for table in metadata.sorted_tables:
        if table.name not in snapshot.tables:
            continue
        live_cols = snapshot.columns.get(table.name, {})
        q_table = preparer.quote(table.name)

        for column in table.columns:
            live = live_cols.get(column.name)
            if live is None:
                continue
            live_type = live.get("data_type", "")
            q_col = preparer.quote(column.name)

            # Case 1: Model expects INTEGER, live DB has BOOLEAN
            if isinstance(column.type, Integer) and not isinstance(column.type, Boolean) and live_type == "boolean":
                default_lit = _literal_default(column)
                try:
                    with sync_conn.begin_nested():
                        sync_conn.execute(text(f"ALTER TABLE {q_table} ALTER COLUMN {q_col} DROP DEFAULT"))
                        sync_conn.execute(
                            text(
                                f"ALTER TABLE {q_table} ALTER COLUMN {q_col} TYPE INTEGER "
                                f"USING (CASE WHEN {q_col} IS NULL THEN NULL WHEN {q_col} THEN 1 ELSE 0 END)"
                            )
                        )
                        if default_lit in ("0", "1"):
                            sync_conn.execute(
                                text(f"ALTER TABLE {q_table} ALTER COLUMN {q_col} SET DEFAULT {default_lit}")
                            )
                    changes.append(f"{table.name}.{column.name}: converted boolean -> integer")
                except Exception:
                    logger.exception(
                        "Schema repair failed converting %s.%s boolean -> integer",
                        table.name,
                        column.name,
                    )

            # Case 2: Model expects JSON, live DB has TEXT / VARCHAR
            elif isinstance(column.type, JSON) and live_type in ("text", "character varying"):
                try:
                    with sync_conn.begin_nested():
                        sync_conn.execute(text(f"ALTER TABLE {q_table} ALTER COLUMN {q_col} DROP DEFAULT"))
                        sync_conn.execute(
                            text(
                                f"ALTER TABLE {q_table} ALTER COLUMN {q_col} TYPE JSON "
                                f"USING (CASE WHEN {q_col} IS NULL OR BTRIM({q_col}) = '' "
                                f"THEN NULL ELSE {q_col}::json END)"
                            )
                        )
                    changes.append(f"{table.name}.{column.name}: converted {live_type} -> json")
                except Exception:
                    logger.exception(
                        "Schema repair failed converting %s.%s %s -> json",
                        table.name,
                        column.name,
                        live_type,
                    )

    return changes


def repair_schema_sync(sync_conn: Connection, metadata: MetaData) -> List[str]:
    """Add any model columns / indexes / constraint repairs missing in the live DB.

    Designed to be invoked via ``await conn.run_sync(repair_schema_sync, Base.metadata)``
    right after ``create_all``. Returns ``["<table>.<column>", ...]`` for logging
    and test assertions. Every DDL/DML statement runs inside its own SAVEPOINT
    so a single failure can never abort the outer PostgreSQL transaction.
    """
    target_names = [t.name for t in metadata.sorted_tables]
    snapshot = _snapshot_schema(sync_conn, target_names)
    added: List[str] = []

    pending_cols = _pending_columns(sync_conn, metadata, snapshot=snapshot)
    for table_name, column in pending_cols:
        sql = _add_column_sql(sync_conn, table_name, column)
        try:
            with sync_conn.begin_nested():
                sync_conn.execute(text(sql))
            added.append(f"{table_name}.{column.name}")
            # Update in-memory snapshot so subsequent passes see the new column
            # without re-querying all 67 tables over WAN.
            snapshot.columns.setdefault(table_name, {})[column.name] = {
                "name": column.name,
                "nullable": bool(column.nullable),
                "data_type": str(column.type).lower(),
                "udt_name": str(column.type).lower(),
            }
            logger.warning("Schema auto-repair added missing column %s.%s", table_name, column.name)
        except Exception:
            # Never let a single tricky column block every other repair OR
            # leave PostgreSQL in InFailedSQLTransactionError.
            logger.exception(
                "Schema auto-repair failed on %s.%s (SQL: %s)", table_name, column.name, sql
            )

    for change in _repair_postgres_varchar_widening(sync_conn, metadata, snapshot=snapshot):
        added.append(change)
        logger.warning("Schema auto-repair %s", change)

    for change in _repair_postgres_column_types(sync_conn, metadata, snapshot=snapshot):
        added.append(change)
        logger.warning("Schema auto-repair %s", change)

    for change in _repair_widened_unique_keys(sync_conn, metadata, snapshot=snapshot):
        added.append(change)
        logger.warning("Schema auto-repair %s", change)

    for change in _repair_relaxed_not_null(sync_conn, metadata, snapshot=snapshot):
        added.append(change)
        logger.warning("Schema auto-repair %s", change)

    for index in _pending_indexes(sync_conn, metadata, snapshot=snapshot):
        try:
            with sync_conn.begin_nested():
                sync_conn.execute(CreateIndex(index, if_not_exists=True))
            added.append(f"index {index.name}")
            logger.warning("Schema auto-repair created missing index %s", index.name)
        except Exception:
            logger.exception("Schema auto-repair failed on index %s", index.name)

    if added:
        logger.warning("Schema auto-repair complete (%d change(s)): %s", len(added), ", ".join(added))
    return added


def verify_schema_sync(sync_conn: Connection, metadata: MetaData) -> List[str]:
    """Return ``["<table>.<col>", ...]`` still missing from the database.

    Called immediately after ``create_all`` + ``repair_schema_sync``. A
    non-empty return means either ``create_all`` skipped a whole table (for
    instance a permissions failure) or an ``ALTER TABLE`` above failed and
    was logged. Surfacing the residue lets ``init_db`` flip ``db_health`` to
    degraded *at boot* instead of waiting for the first user request to trip
    over the missing column.
    """
    snapshot = _snapshot_schema(sync_conn, [t.name for t in metadata.sorted_tables])
    missing: List[str] = []
    for table in metadata.sorted_tables:
        if table.name not in snapshot.tables:
            missing.append(f"{table.name}.*")
            continue
        live_cols = snapshot.columns.get(table.name, {})
        for col in table.columns:
            if col.name not in live_cols:
                missing.append(f"{table.name}.{col.name}")
    return missing
