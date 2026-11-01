"""Run the Alembic migrations programmatically.

Used by the test suite (and callable from scripts) so a migration can be
exercised end-to-end without a shell:

    from app.run_migrations import upgrade_head
    upgrade_head()  # uses settings.DATABASE_URL

Every migration is written defensively (it checks the live schema before it
changes anything), so running this against a database the startup repair has
already fixed is a no-op.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = BACKEND_DIR / "migrations"


def _config(database_url: str | None = None) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if database_url:
        cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def upgrade_head(database_url: str | None = None) -> None:
    """Bring the configured (or given) database up to the newest revision."""
    command.upgrade(_config(database_url), "head")


def _sync_url(url: str) -> str:
    for async_prefix, sync_prefix in (
        ("postgresql+asyncpg://", "postgresql+psycopg2://"),
        ("sqlite+aiosqlite://", "sqlite://"),
    ):
        if url.startswith(async_prefix):
            return url.replace(async_prefix, sync_prefix, 1)
    return url


def current_revision(database_url: str | None = None) -> str | None:
    """The revision the database is currently at, or None when unversioned."""
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    if database_url is None:
        from app.config import settings

        database_url = settings.DATABASE_URL
    engine = create_engine(_sync_url(database_url))
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
