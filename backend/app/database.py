"""Database session management."""
import asyncio
import logging
import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

logger = logging.getLogger(__name__)


# libpq / pooler query parameters that asyncpg does not accept on connect().
_ASYNC_UNSAFE_PG_PARAMS = {
    "sslmode",
    "channel_binding",
    "sslnegotiation",
    "options",
    "pgbouncer",
    "supa",
}


def _normalize_database_url(raw: str) -> tuple[str, dict]:
    """Make a DATABASE_URL usable by asyncpg and fail fast on connect.

    Neon / Render / Supabase often hand out ``postgresql://…?sslmode=require``
    (plus ``channel_binding=require`` or ``pgbouncer=true``). asyncpg does not
    accept libpq query keys, and transaction-pooler endpoints require
    ``statement_cache_size=0`` so PgBouncer never errors on prepared statements.
    """
    url = (raw or "").strip()
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql+psycopg2://"):
        url = url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql+psycopg://"):
        url = url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)

    connect_args: dict = {}
    parsed = urlparse(url)
    if parsed.scheme.startswith("postgresql"):
        kept: list[tuple[str, str]] = []
        sslmode = ""
        use_pgbouncer = False
        for k, v in parse_qsl(parsed.query, keep_blank_values=True):
            kl = k.lower()
            if kl == "sslmode":
                sslmode = v.lower()
            if kl == "pgbouncer" and v.lower() in {"1", "true", "yes", "on"}:
                use_pgbouncer = True
            if kl in _ASYNC_UNSAFE_PG_PARAMS:
                continue
            kept.append((k, v))
        if sslmode in {"require", "verify-ca", "verify-full", "prefer"}:
            connect_args["ssl"] = True
        url = urlunparse(parsed._replace(query=urlencode(kept)))
        # Neon / serverless Postgres cold wake can take 10-20s; 25s avoids
        # false boot/request timeouts while still failing fast on dead hosts.
        connect_args["timeout"] = 25
        connect_args["command_timeout"] = 60
        host_lower = (parsed.hostname or "").lower()
        if use_pgbouncer or "-pooler." in host_lower or "pooler.supabase.com" in host_lower:
            connect_args["statement_cache_size"] = 0
    elif parsed.scheme.startswith("sqlite"):
        # SQLite (local runs, the simulator, the test suite) locks the whole file
        # for a write. Waiting 20s avoids spurious "database is locked" 500s.
        connect_args["timeout"] = 20
        db_path = parsed.path
        if db_path.startswith("//"):
            db_path = db_path[1:]
        elif db_path.startswith("/"):
            db_path = db_path[1:]
        if db_path and db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
    return url, connect_args


db_url, _connect_args = _normalize_database_url(settings.DATABASE_URL)

# SQLite (used by the test suite and local runs) does not accept the
# QueuePool sizing arguments; passing them raises TypeError at import time.
_engine_kwargs: dict = {"echo": False}
if db_url.startswith("sqlite"):
    _engine_kwargs["pool_pre_ping"] = True
    _engine_kwargs["connect_args"] = _connect_args
else:
    _engine_kwargs.update(
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=240,
        pool_timeout=15,
        connect_args=_connect_args,
    )

engine = create_async_engine(db_url, **_engine_kwargs)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
AsyncSessionLocal = async_session_factory


class Base(DeclarativeBase):
    pass


_init_lock = asyncio.Lock()
_db_initialized = False


def is_db_initialized() -> bool:
    """Return True once ``init_db`` has verified every table and column."""
    return _db_initialized


async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> list[str]:
    """Create missing tables, repair drifted columns/types, and verify schema.

    Splits work into short, savepoint-isolated transactions so:
    1. Table creation commits independently of column/index repairs.
    2. Each ``ALTER TABLE`` runs inside its own ``SAVEPOINT`` in
       ``repair_schema_sync``, preventing any single failure from aborting
       PostgreSQL's transaction and rolling back other columns.
    3. Whenever DDL changes are applied, ``await engine.dispose()`` flushes
       pooled ``asyncpg`` connections so cached prepared statement plans never
       fail with ``InvalidCachedStatementError``.
    """
    global _db_initialized
    import app.models  # noqa: F401 -- ensure every model is registered on Base.metadata
    from app.schema_repair import _snapshot_schema, repair_schema_sync, verify_schema_sync
    from app import db_health

    def _create_missing_tables(sync_conn) -> list[str]:
        snap = _snapshot_schema(sync_conn, [t.name for t in Base.metadata.sorted_tables])
        missing_tbls = [t for t in Base.metadata.sorted_tables if t.name not in snap.tables]
        if missing_tbls:
            Base.metadata.create_all(sync_conn, tables=missing_tbls)
        return [t.name for t in missing_tbls]

    async with engine.begin() as conn:
        created_tables = await conn.run_sync(_create_missing_tables)

    async with engine.begin() as conn:
        added = await conn.run_sync(repair_schema_sync, Base.metadata)

    if created_tables or added:
        # Flush pooled asyncpg connections so cached SELECT * / ORM plans
        # compiled before ALTER TABLE are discarded cleanly.
        await engine.dispose()

    async with engine.begin() as conn:
        missing = await conn.run_sync(verify_schema_sync, Base.metadata)

    if added:
        logger.warning(
            "Schema auto-repair applied %d change(s): %s.",
            len(added),
            ", ".join(added),
        )

    if missing:
        _db_initialized = False
        sample = ", ".join(missing[:8])
        more = f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""
        msg = f"Database schema incomplete — missing: {sample}{more}"
        logger.error(msg)
        db_health.status.ok = False
        db_health.status.kind = db_health.KIND_SCHEMA
        db_health.status.message = msg
        return added

    _db_initialized = True
    return added


async def ensure_db_initialized(timeout: float = 90.0) -> bool:
    """Idempotent, lock-serialized wrapper around ``init_db()``."""
    if _db_initialized:
        return True
    async with _init_lock:
        if _db_initialized:
            return True
        await asyncio.wait_for(init_db(), timeout=timeout)
        return _db_initialized
