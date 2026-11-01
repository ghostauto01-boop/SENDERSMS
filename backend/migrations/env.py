"""Alembic environment for SENDERSMS.

Connects using ``settings.DATABASE_URL`` from the app configuration. The
migrations themselves are written defensively (each one checks the live schema
before it changes anything), so they are safe to run on a database that the
startup schema repair has already fixed, and safe to run twice.
"""

from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Make `app` importable when alembic runs from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The app URL is async (asyncpg / aiosqlite); migrations run through it with
# run_sync(). Offline mode only needs a URL string, so the driver stays as-is.
# An explicitly provided URL (programmatic runs, tests) wins over the app
# setting.
if not (config.get_main_option("sqlalchemy.url") or "").strip():
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = None  # migrations are hand-written, not autogenerate diffs


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
