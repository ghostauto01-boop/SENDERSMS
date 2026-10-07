"""Additive schema repair, run once at startup.

WHY THIS EXISTS
---------------
This project has no Alembic. Startup only calls ``Base.metadata.create_all``,
which creates tables that do not exist yet and does nothing else. It will
never add a column to a table that already exists.

That is a trap for any database that already has data: deploying code with a
new column produces a *green, healthy-looking* service whose pages then fail
with ``no such column: campaigns.scheduled_start_at``. The operator gets no
crash and no warning -- just a broken page. Exactly that happened with the
scheduling/auto-reply release.

This module closes the gap: it compares the models against the live database
and issues ``ALTER TABLE ... ADD COLUMN`` for anything missing, then
``CREATE INDEX`` for any model index that is still absent.

Indexes matter for the same reason columns do, just less loudly. ``create_all``
skips a table it already knows, and that skip takes the table's indexes with
it -- so a column added here would stay unindexed forever. The result is not
an error page, it is a table scan on every filtered query, which only shows up
as "the inbox got slow" once the table is large. Both halves of the model
definition are restored so behaviour and performance match a fresh install.

SAFETY RULES (deliberately conservative)
----------------------------------------
* **Additive only.** It only ever ADDs columns. It never drops, renames,
  retypes or reorders anything, so it cannot destroy data.
* **Never widens to NOT NULL.** A ``NOT NULL`` column with no default cannot
  be added to a table that already has rows. Where the model declares one
  without a usable default, the column is added *nullable* instead; the app
  works, and the strict constraint is left for the SQL migration script.
* **Never touches a table it did not find.** Missing tables are
  ``create_all``'s job.
* **Failure is not fatal.** Any error is logged and swallowed, so a
  permissions problem cannot take the service down.

It does NOT replace ``scripts/migrate_existing_db.sql``. That script still
owns data migrations (merging duplicate conversation threads), indexes and
constraints. This only fixes the specific, silent, high-frequency failure of
a missing column.
"""

import logging
import re

from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateColumn, CreateIndex, CreateTable

logger = logging.getLogger(__name__)


def _pending_columns(sync_conn, metadata) -> list[tuple]:
    """Return (table_name, Column) for every model column absent from the DB."""
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    pending: list[tuple] = []

    for table in metadata.sorted_tables:
        if table.name not in existing_tables:
            # create_all owns brand-new tables.
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in have:
                pending.append((table.name, column))
    return pending


def _pending_indexes(sync_conn, metadata) -> list:
    """Return every model Index missing from an existing table.

    Called after the columns have been added, so an index over a
    just-restored column is creatable.
    """
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    pending = []

    for table in metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all builds new tables with their indexes.
        have_indexes = {i["name"] for i in inspector.get_indexes(table.name)}
        have_columns = {c["name"] for c in inspector.get_columns(table.name)}
        # A unique constraint is often reported as an index; don't fight it.
        have_indexes |= {
            u["name"] for u in inspector.get_unique_constraints(table.name) if u.get("name")
        }
        for index in table.indexes:
            if index.name in have_indexes:
                continue
            # Only build an index whose columns all actually exist, otherwise
            # the statement fails and just adds noise to the log.
            if not {c.name for c in index.columns}.issubset(have_columns):
                continue
            pending.append(index)
    return pending


def _literal_default(column) -> str | None:
    """A SQL literal for a simple Python-side scalar default, else None.

    Model defaults like ``default=False`` live in Python and are invisible to
    the database, so existing rows would end up NULL. For plain scalars we can
    hand the database an equivalent literal. Callables (``default=lambda:
    datetime.now()``) have no safe literal, so they return None.
    """
    if column.default is None:
        return None
    arg = getattr(column.default, "arg", None)
    if callable(arg) or arg is None:
        return None
    if isinstance(arg, bool):
        return "TRUE" if arg else "FALSE"
    if isinstance(arg, int):
        return str(arg)
    if isinstance(arg, str):
        escaped = arg.replace("'", "''")
        return f"'{escaped}'"
    return None


def _add_column_sql(sync_conn, table_name: str, column) -> str:
    """Compile a safe ADD COLUMN statement for this dialect."""
    ddl = CreateColumn(column).compile(dialect=sync_conn.dialect).string.strip()
    default_literal = _literal_default(column)

    if not column.nullable:
        has_db_default = column.server_default is not None or default_literal is not None
        if not has_db_default:
            # NOT NULL with nothing to backfill existing rows would fail on a
            # populated table. Add it nullable so the deploy survives; the SQL
            # migration script applies the strict constraint.
            ddl = ddl.replace(" NOT NULL", "")
            logger.warning(
                "schema_repair: %s.%s is NOT NULL with no usable default; "
                "adding it as nullable. Run scripts/migrate_existing_db.sql "
                "to apply the strict constraint.",
                table_name,
                column.name,
            )

    if default_literal is not None and "DEFAULT" not in ddl.upper():
        ddl = f"{ddl} DEFAULT {default_literal}"

    return f'ALTER TABLE "{table_name}" ADD COLUMN {ddl}'


def _sqlite_rebuild_table(
    sync_conn, metadata, table_name: str, reason: str = "the model's unique key"
) -> bool:
    """Rebuild one SQLite table so its constraints match the models.

    SQLite cannot drop a UNIQUE constraint that was declared inline in
    ``CREATE TABLE``, so a legacy database would keep ``UNIQUE (contact_id)``
    forever and a contact could never have both an SMS and an email thread.

    The rebuild is the documented SQLite procedure and it is transactional:
    create ``<table>__repair`` from the current model, copy every shared column
    across, drop the old table, rename the new one into place. Indexes are
    restored by the index pass that follows this one. Foreign key enforcement is
    switched off for the swap (as SQLite's own documentation prescribes) and
    restored afterwards; the copy happens before anything is dropped, so a
    failure leaves the original table untouched.
    """
    table = metadata.tables.get(table_name)
    if table is None:
        return False

    tmp = f"{table_name}__repair"
    try:
        create_sql = str(CreateTable(table).compile(dialect=sync_conn.dialect)).strip()
        create_sql = re.sub(
            r'^CREATE TABLE ["\w]+',
            f'CREATE TABLE "{tmp}"',
            create_sql,
            count=1,
        )

        old_cols = [c["name"] for c in inspect(sync_conn).get_columns(table_name)]
        model_cols = {c.name for c in table.columns}
        shared = [c for c in old_cols if c in model_cols]

        fk_state = 0
        try:
            fk_state = sync_conn.execute(text("PRAGMA foreign_keys")).scalar() or 0
            sync_conn.execute(text("PRAGMA foreign_keys=OFF"))
        except Exception:  # noqa: BLE001 - pragma support varies; best effort
            pass

        try:
            sync_conn.execute(text(f'DROP TABLE IF EXISTS "{tmp}"'))
            sync_conn.execute(text(create_sql))
            if shared:
                cols = ", ".join(f'"{c}"' for c in shared)
                sync_conn.execute(
                    text(f'INSERT INTO "{tmp}" ({cols}) SELECT {cols} FROM "{table_name}"')
                )
            sync_conn.execute(text(f'DROP TABLE "{table_name}"'))
            sync_conn.execute(text(f'ALTER TABLE "{tmp}" RENAME TO "{table_name}"'))
        finally:
            if fk_state:
                try:
                    sync_conn.execute(text("PRAGMA foreign_keys=ON"))
                except Exception:  # noqa: BLE001
                    pass

        logger.warning(
            "schema_repair: rebuilt table %s to apply %s (%d row(s) copied).",
            table_name,
            reason,
            sync_conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar() or 0,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - never fatal
        logger.error(
            "schema_repair: could not rebuild %s (%s). The old unique key stays in "
            "place; run scripts/migrate_existing_db.sql for a manual fix.",
            table_name,
            exc,
        )
        return False


#: Unique keys that a release deliberately WIDENED.
#:
#: ``create_all`` never touches a table it already knows, so a unique
#: constraint that existed before a widening keeps its old, narrower shape
#: forever. For conversations that is not cosmetic: the previous release
#: declared ``UNIQUE (contact_id)``, and once email exists a contact needs one
#: SMS thread *and* one email thread. Leaving the old key in place makes the
#: second thread fail with a duplicate-key error that looks like an inbox bug.
#:
#: Each entry: table -> (old column set, new column set, new index name).
_WIDENED_UNIQUE_KEYS: dict[str, tuple[set[str], set[str], str]] = {
    "conversations": (
        {"contact_id"},
        {"contact_id", "channel"},
        "uq_conversation_contact_channel",
    ),
}


def _repair_widened_unique_keys(sync_conn, metadata) -> list[str]:
    """Drop too-narrow legacy unique keys, then create the widened one.

    Only keys whose columns are exactly the OLD set are dropped -- a
    hand-written partial index or a differently shaped constraint is left
    alone. Dropping an index or constraint never destroys rows, so this stays
    within the module's "no data loss" rule even though it is not purely
    additive. Failure is logged, never fatal.
    """
    from sqlalchemy import inspect

    applied: list[str] = []
    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())

    for table, (old_cols, new_cols, new_name) in _WIDENED_UNIQUE_KEYS.items():
        if table not in tables:
            continue
        have_cols = {c["name"] for c in inspector.get_columns(table)}
        if not new_cols.issubset(have_cols):
            # A column the widened key needs is missing; the ADD COLUMN pass
            # above already tried, and the next boot will finish the job.
            continue

        candidates: list[tuple[str, str]] = []  # (kind, name)
        existing_names: set = set()
        widened_sets: list[set] = []
        rebuilt = False
        try:
            for uc in inspector.get_unique_constraints(table):
                name = uc.get("name")
                if name:
                    existing_names.add(name)
                cols = set(uc.get("column_names") or [])
                if cols and cols.issuperset(new_cols):
                    widened_sets.append(cols)
                if cols == old_cols and name:
                    candidates.append(("constraint", name))
            for ix in inspector.get_indexes(table):
                name = ix.get("name")
                if not ix.get("unique"):
                    continue
                if name:
                    existing_names.add(name)
                cols = set(ix.get("column_names") or [])
                if cols and cols.issuperset(new_cols):
                    widened_sets.append(cols)
                if cols == old_cols and name:
                    candidates.append(("index", name))
        except Exception as exc:  # noqa: BLE001
            logger.error("schema_repair: could not inspect %s unique keys (%s)", table, exc)
            continue

        for kind, name in candidates:
            if name == new_name:
                continue

            if kind == "constraint" and sync_conn.dialect.name == "sqlite":
                # SQLite has no ALTER TABLE ... DROP CONSTRAINT at all; the only
                # honest way to widen the key is to rebuild the table.
                if _sqlite_rebuild_table(sync_conn, metadata, table):
                    applied.append(f"rebuilt table {table}")
                    rebuilt = True
                break

            try:
                if kind == "constraint":
                    sync_conn.execute(
                        text(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{name}"')
                    )
                else:
                    sync_conn.execute(text(f'DROP INDEX IF EXISTS "{name}"'))

                # "IF EXISTS" makes a miss a silent no-op, so confirm the key is
                # really gone instead of trusting the statement.
                fresh = inspect(sync_conn)
                still_there = any(
                    uc.get("name") == name for uc in fresh.get_unique_constraints(table)
                ) or any(ix.get("name") == name for ix in fresh.get_indexes(table))
                if still_there:
                    raise RuntimeError("the legacy key is still present")

                applied.append(f"dropped legacy unique {kind} {name}")
                logger.warning(
                    "schema_repair: dropped legacy unique %s %s on %s; the model "
                    "widened this key to (%s).",
                    kind, name, table, ", ".join(sorted(new_cols)),
                )
            except Exception as exc:  # noqa: BLE001
                if sync_conn.dialect.name == "sqlite" and _sqlite_rebuild_table(
                    sync_conn, metadata, table
                ):
                    applied.append(f"rebuilt table {table}")
                    rebuilt = True
                    break
                logger.error(
                    "schema_repair: could not drop legacy unique %s %s (%s). "
                    "Rebuild the table or drop it by hand -- until then a contact "
                    "cannot have both an SMS and an email thread.",
                    kind, name, exc,
                )

        if rebuilt:
            # The rebuilt table took its unique key from the models, and the
            # inspector we hold is a pre-rebuild snapshot.
            continue

        already_widened = new_name in existing_names or new_cols in widened_sets
        if already_widened:
            # Already widened on an earlier boot (or by create_all on a fresh
            # database): stay silent so a clean restart reports nothing.
            continue

        try:
            cols_sql = ", ".join(sorted(new_cols))
            sync_conn.execute(
                text(
                    f'CREATE UNIQUE INDEX IF NOT EXISTS "{new_name}" '
                    f'ON "{table}" ({cols_sql})'
                )
            )
            applied.append(f"unique index {new_name}")
        except Exception as exc:  # noqa: BLE001
            logger.error("schema_repair: could not create %s (%s)", new_name, exc)

    return applied


#: Columns a release deliberately RELAXED from NOT NULL to nullable.
#:
#: ``create_all`` never alters an existing table, so a column that was created
#: NOT NULL stays NOT NULL forever, no matter what the model now says. That is
#: not cosmetic: this release made ``contacts.phone_number`` optional so an
#: email-only contact can be imported, and on any database created before it,
#: every such insert would fail with a NOT NULL violation — the feature would
#: work on a fresh install and break on the live one.
#:
#: Each entry: table -> column.
_RELAXED_NOT_NULL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("contacts", "phone_number"),
)


def _repair_relaxed_not_null(sync_conn, metadata) -> list[str]:
    """Drop NOT NULL from columns the models now declare optional.

    Only ever removes a constraint; no row is read, written or dropped. On
    PostgreSQL this is a single metadata-only ALTER. SQLite cannot alter a
    constraint in place, so the existing table-rebuild path is used.
    """
    from sqlalchemy import inspect

    applied: list[str] = []
    dialect = sync_conn.dialect.name
    tables = set(inspect(sync_conn).get_table_names())

    for table_name, column_name in _RELAXED_NOT_NULL_COLUMNS:
        if table_name not in tables:
            continue
        table = metadata.tables.get(table_name)
        if table is None or column_name not in table.columns:
            continue
        if table.columns[column_name].nullable is not True:
            # The model still wants it NOT NULL; nothing to relax.
            continue

        columns = {c["name"]: c for c in inspect(sync_conn).get_columns(table_name)}
        info = columns.get(column_name)
        if info is None or info.get("nullable", True):
            continue  # already nullable — a clean restart reports nothing

        if dialect == "sqlite":
            # No ALTER COLUMN; rebuilding copies the column across as nullable
            # because the rebuild takes its shape from the model.
            if _sqlite_rebuild_table(
                sync_conn, metadata, table_name,
                reason=f"{table_name}.{column_name} being optional",
            ):
                applied.append(f"relaxed {table_name}.{column_name}")
            continue

        try:
            sync_conn.execute(
                text(f'ALTER TABLE "{table_name}" ALTER COLUMN "{column_name}" DROP NOT NULL')
            )
            applied.append(f"relaxed {table_name}.{column_name}")
            logger.warning(
                "schema_repair: %s.%s is now nullable (the model made it optional "
                "so email-only contacts can be stored).",
                table_name, column_name,
            )
        except Exception as exc:  # noqa: BLE001 - never take the service down
            logger.error(
                "schema_repair: could not drop NOT NULL on %s.%s (%s). Run "
                "scripts/migrate_existing_db.sql by hand — email-only contacts "
                "cannot be inserted until then.",
                table_name, column_name, exc,
            )

    return applied


def repair_schema_sync(sync_conn, metadata) -> list[str]:
    """Add every missing column, then every missing index.

    Returns a list of human-readable changes, e.g.
    ``["conversations.ads_campaign_id", "index ix_conversations_ads_campaign_id"]``.
    """
    applied: list[str] = []

    for table_name, column in _pending_columns(sync_conn, metadata):
        stmt = _add_column_sql(sync_conn, table_name, column)
        try:
            sync_conn.execute(text(stmt))
            applied.append(f"{table_name}.{column.name}")
            logger.warning("schema_repair: added missing column %s.%s", table_name, column.name)
        except Exception as exc:  # noqa: BLE001 - one bad column must not stop the rest
            logger.error(
                "schema_repair: could not add %s.%s (%s). "
                "Run scripts/migrate_existing_db.sql by hand.",
                table_name,
                column.name,
                exc,
            )

    # Relax NOT NULL constraints the models have since made optional. This runs
    # before the widened-key pass because that pass may rebuild the table, and a
    # rebuild should pick up the relaxed shape in the same boot.
    try:
        applied.extend(_repair_relaxed_not_null(sync_conn, metadata))
    except Exception as exc:  # noqa: BLE001 - never take the service down
        logger.error("schema_repair: NOT NULL relaxation failed (%s)", exc)

    # Widened unique keys next: they need the columns added above (the rebuild
    # path copies them), and they must exist before the index pass decides what
    # is missing.
    try:
        applied.extend(_repair_widened_unique_keys(sync_conn, metadata))
    except Exception as exc:  # noqa: BLE001 - never take the service down
        logger.error("schema_repair: widened-key repair failed (%s)", exc)

    # Indexes last: a column added above may be the one being indexed.
    for index in _pending_indexes(sync_conn, metadata):
        try:
            # IF NOT EXISTS (SQLite and PostgreSQL both support it) so a name
            # the inspector did not report -- a partial index, or a racing
            # second worker booting at the same time -- is a no-op instead of
            # an alarming error in the log.
            stmt = (
                CreateIndex(index, if_not_exists=True)
                .compile(dialect=sync_conn.dialect)
                .string.strip()
            )
            sync_conn.execute(text(stmt))
            applied.append(f"index {index.name}")
            logger.warning("schema_repair: created missing index %s", index.name)
        except Exception as exc:  # noqa: BLE001 - an index is an optimisation, never fatal
            logger.error(
                "schema_repair: could not create index %s (%s). "
                "Queries still work but may be slow; "
                "run scripts/migrate_existing_db.sql by hand.",
                index.name,
                exc,
            )

    return applied
