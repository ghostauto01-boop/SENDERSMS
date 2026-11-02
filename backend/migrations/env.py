"""Alembic environment for SENDERSMS.

Connects using ``settings.DATABASE_URL`` from the app configuration (normalized
through ``_normalize_database_url`` so ``postgres://`` and ``?sslmode=require``
work identically to the web server). The migrations themselves are written
defensively (each one checks the live schema before it changes anything), so
they are safe to run on a database that the startup schema repair has already
fixed, and safe to run twice.
"""

from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# Make `app` importable when alembic runs from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.database import _normalize_database_url  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# An explicitly provided URL (programmatic runs, tests) wins over settings.
if not (config.get_main_option("sqlalchemy.url") or "").strip():
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))

target_metadata = None  # migrations are hand-written, not autogenerate diffs


def run_migrations_offline() -> None:
    raw_url = config.get_main_option("sqlalchemy.url") or settings.DATABASE_URL
    norm_url, _ = _normalize_database_url(raw_url)
    context.configure(
        url=norm_url,
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
    raw_url = config.get_main_option("sqlalchemy.url") or settings.DATABASE_URL
    norm_url, connect_args = _normalize_database_url(raw_url)
    connectable = create_async_engine(
        norm_url,
        poolclass=pool.NullPool,
        connect_args=connect_args,
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
