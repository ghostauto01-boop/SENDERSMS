"""Tests for the sending limits / pacing gate."""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.conversation import Message
from app.models.system import SystemSetting


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _set(db, key, value):
    db.add(SystemSetting(key=key, value=str(value), category="sending_rules"))
    await db.flush()


async def _seed_sent(db, count, minutes_ago=0):
    """Insert `count` delivered outbound messages with a sent_at timestamp."""
    from app.models.contact import Contact
    from app.models.conversation import Conversation
    c = Contact(phone_number=f"+23480312345{10 + count}", first_name="Seed")
    db.add(c)
    await db.flush()
    conv = Conversation(contact_id=c.id)
    db.add(conv)
    await db.flush()
    for i in range(count):
        db.add(Message(
            conversation_id=conv.id, contact_id=c.id, direction="outgoing",
            body=f"msg {i}", status="delivered", idempotency_key=f"seed-{count}-{i}",
            sent_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        ))
    await db.flush()


class TestSendingGate:
    @pytest.mark.asyncio
    async def test_allowed_by_default(self, db):
        from app.services.sending_limits import SendingGate
        check = await SendingGate(db).check()
        assert check["allowed"] is True

    @pytest.mark.asyncio
    async def test_hourly_limit_blocks_when_reached(self, db):
        await _set(db, "enable_hourly_limit", "true")
        await _set(db, "hourly_maximum", "2")
        await _seed_sent(db, 2)
        from app.services.sending_limits import SendingGate
        check = await SendingGate(db).check()
        assert check["allowed"] is False
        assert "hourly limit" in check["reason"]
        assert check["wait_seconds"] > 0

    @pytest.mark.asyncio
    async def test_pacing_spaces_messages(self, db):
        await _set(db, "enable_hourly_limit", "true")
        await _set(db, "hourly_maximum", "60")
        await _set(db, "enable_pacing", "true")
        await _set(db, "min_delay_seconds", "30")
        await _seed_sent(db, 1, minutes_ago=0)  # just sent now
        from app.services.sending_limits import SendingGate
        check = await SendingGate(db).check()
        assert check["allowed"] is False
        assert "pacing" in check["reason"]

    @pytest.mark.asyncio
    async def test_old_sends_do_not_block(self, db):
        await _set(db, "enable_hourly_limit", "true")
        await _set(db, "hourly_maximum", "5")
        await _seed_sent(db, 3, minutes_ago=90)  # previous hour
        from app.services.sending_limits import SendingGate
        check = await SendingGate(db).check()
        assert check["allowed"] is True

    @pytest.mark.asyncio
    async def test_disabled_limits_do_nothing(self, db):
        await _set(db, "enable_hourly_limit", "false")
        await _set(db, "hourly_maximum", "1")
        await _set(db, "enable_pacing", "false")
        await _seed_sent(db, 50)
        from app.services.sending_limits import SendingGate
        check = await SendingGate(db).check()
        assert check["allowed"] is True

    @pytest.mark.asyncio
    async def test_effective_interval_derives_from_hourly(self, db):
        from app.services.sending_limits import SendingGate, get_sending_rules
        await _set(db, "enable_hourly_limit", "true")
        await _set(db, "hourly_maximum", "10")
        rules = await get_sending_rules(db)
        assert SendingGate(db).effective_interval(rules) == 360  # 3600/10

    @pytest.mark.asyncio
    async def test_status_returns_counters(self, db):
        from app.services.sending_limits import SendingGate
        status = await SendingGate(db).status()
        assert "counters" in status
        assert status["counters"]["hour"] >= 0
        assert "allowed_now" in status
