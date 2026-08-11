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
and issues ``ALTER TABLE ... ADD COLUMN`` for anything missing.

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

from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateColumn

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


def repair_schema_sync(sync_conn, metadata) -> list[str]:
    """Add every missing column. Returns a list of human-readable changes."""
    pending = _pending_columns(sync_conn, metadata)
    applied: list[str] = []

    for table_name, column in pending:
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
    return applied
