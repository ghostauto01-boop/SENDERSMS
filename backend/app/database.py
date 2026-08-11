"""Database session management."""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

db_url = settings.DATABASE_URL
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)

# SQLite (used by the test suite and local runs) does not accept the
# QueuePool sizing arguments; passing them raises TypeError at import time.
_engine_kwargs = {"echo": False}
if db_url.startswith("sqlite"):
    _engine_kwargs["pool_pre_ping"] = True
else:
    _engine_kwargs.update(pool_size=20, max_overflow=10, pool_pre_ping=True)

engine = create_async_engine(db_url, **_engine_kwargs)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase): pass

async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as exc:
            await session.rollback()
            raise

async def init_db():
    """Create missing tables, then add any missing columns.

    create_all() only ever creates whole tables -- it will not add a column to
    a table that already exists. On a database with existing data that means a
    new column silently never appears and every query touching it fails at
    runtime, while the service itself looks healthy. repair_schema_sync closes
    that gap with additive-only ALTER TABLE ... ADD COLUMN.
    """
    from app.schema_repair import repair_schema_sync

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        added = await conn.run_sync(repair_schema_sync, Base.metadata)

    if added:
        import logging
        logging.getLogger(__name__).warning(
            "Schema auto-repair added %d missing column(s): %s. "
            "This is a safety net -- run scripts/migrate_existing_db.sql for "
            "indexes, constraints and data migrations.",
            len(added), ", ".join(added),
        )
    return added
