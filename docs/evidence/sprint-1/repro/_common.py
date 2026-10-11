"""Shared bootstrap for the Sprint 1 reproduction scripts.

Each script builds a throwaway in-memory database, drives the REAL app (HTTP routes
and the MCP tool dispatcher) against it, and prints numbers. They run unchanged against
old and new code: point REPO_ROOT at a checkout (a `git worktree` of the parent commit
gives the "before" figures) and compare.

    REPO_ROOT=/path/to/checkout python docs/evidence/sprint-1/repro/repro_p0_5.py
"""
import asyncio
import logging
import os
import pathlib
import sys

ROOT = pathlib.Path(os.environ.get("REPO_ROOT") or pathlib.Path(__file__).resolve().parents[4])
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend" / "tests")]
logging.disable(logging.CRITICAL)

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402


async def new_db():
    import app.models  # noqa: F401  -- registers every table before create_all
    from app.database import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def sign_in_as_admin(db):
    """Route the real app at this database and authenticate every request as an admin."""
    from app.database import get_db
    from app.main import app
    from app.models.user import User
    from app.security.auth import get_current_user

    async def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="t", email="t@example.com", password_hash="x", role="admin", is_active=True)
    return app


def open_sending_rules():
    """Keep the P0-3 throttles out of the way for scripts that are about something else."""
    from app.services import sending_limits

    for key, value in {"enable_daily_limit": False, "sending_start_time": "", "sending_end_time": "",
                       "allow_weekends": True, "email_daily_per_mailbox": 0}.items():
        sending_limits.DEFAULTS[key] = value


def run(coro):
    asyncio.run(coro)
