"""Tests for the sending limits / pacing gate.

Every check is evaluated at a FIXED instant (``NOON``) rather than at the real
clock. The default rules only allow sending between 08:00 and 20:00 local
time, so these tests used to pass during the working day and fail every
evening -- the gate correctly answered "outside sending hours" and the
assertions, which were about hourly limits and pacing, never got that far.
Pinning the clock tests the rule under examination instead of the time of day.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.conversation import Message
from app.models.system import SystemSetting


#: A Wednesday at 12:00 Africa/Lagos -- a weekday, inside the default
#: 08:00-20:00 window, so neither the weekend nor the hours rule interferes.
NOON = datetime(2026, 9, 2, 12, 0, tzinfo=ZoneInfo("Africa/Lagos")).astimezone(timezone.utc)


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
            sent_at=NOON - timedelta(minutes=minutes_ago),
        ))
    await db.flush()


class TestSendingGate:
    @pytest.mark.asyncio
    async def test_no_window_configured_allows_sending_at_any_hour(self, db):
        """With no window set, 03:00 is fine -- the window must be opt-in."""
        from app.services.sending_limits import SendingGate

        night = datetime(2026, 9, 2, 3, 0, tzinfo=ZoneInfo("Africa/Lagos")).astimezone(
            timezone.utc
        )
        check = await SendingGate(db).check(now=night)
        assert check["allowed"] is True, check["reason"]

    @pytest.mark.asyncio
    async def test_allowed_by_default(self, db):
        from app.services.sending_limits import SendingGate
        check = await SendingGate(db).check(now=NOON)
        assert check["allowed"] is True

    @pytest.mark.asyncio
    async def test_hourly_limit_blocks_when_reached(self, db):
        await _set(db, "enable_hourly_limit", "true")
        await _set(db, "hourly_maximum", "2")
        await _seed_sent(db, 2)
        from app.services.sending_limits import SendingGate
        check = await SendingGate(db).check(now=NOON)
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
        check = await SendingGate(db).check(now=NOON)
        assert check["allowed"] is False
        assert "pacing" in check["reason"]

    @pytest.mark.asyncio
    async def test_old_sends_do_not_block(self, db):
        await _set(db, "enable_hourly_limit", "true")
        await _set(db, "hourly_maximum", "5")
        await _seed_sent(db, 3, minutes_ago=90)  # previous hour
        from app.services.sending_limits import SendingGate
        check = await SendingGate(db).check(now=NOON)
        assert check["allowed"] is True

    @pytest.mark.asyncio
    async def test_disabled_limits_do_nothing(self, db):
        await _set(db, "enable_hourly_limit", "false")
        await _set(db, "hourly_maximum", "1")
        await _set(db, "enable_pacing", "false")
        await _seed_sent(db, 50)
        from app.services.sending_limits import SendingGate
        check = await SendingGate(db).check(now=NOON)
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

    @pytest.mark.asyncio
    async def test_wait_seconds_is_measured_from_the_supplied_clock(self, db):
        """Regression: wait_seconds was computed from the real clock.

        ``check(now=...)`` decided *whether* to block using the supplied
        instant, but ``_wait_until`` measured the wait with datetime.now().
        Evaluating a moment that is not right now therefore produced a
        wait_seconds for a different instant entirely -- usually negative, and
        then clamped to 1, so a caller that honoured it barely waited at all.
        """
        await _set(db, "enable_hourly_limit", "true")
        await _set(db, "hourly_maximum", "1")
        await _seed_sent(db, 1)

        from app.services.sending_limits import SendingGate

        # 12:00 with the hour's allowance used up -> must wait until 13:00.
        check = await SendingGate(db).check(now=NOON)
        assert check["allowed"] is False
        assert "hourly limit" in check["reason"]
        # One hour away, give or take the inclusive +1 second.
        assert 3595 <= check["wait_seconds"] <= 3605, check["wait_seconds"]

    @pytest.mark.asyncio
    async def test_outside_hours_waits_until_the_window_opens(self, db):
        """A 23:00 send waits for the 08:00 opening, not one second."""
        from app.services.sending_limits import SendingGate

        # The window is opt-in, so configure one explicitly.
        await _set(db, "sending_start_time", "08:00")
        await _set(db, "sending_end_time", "20:00")

        late = datetime(2026, 9, 2, 23, 0, tzinfo=ZoneInfo("Africa/Lagos")).astimezone(
            timezone.utc
        )
        check = await SendingGate(db).check(now=late)
        assert check["allowed"] is False
        assert check["reason"] == "outside sending hours"
        # 23:00 -> 08:00 next day is nine hours.
        assert 9 * 3600 - 5 <= check["wait_seconds"] <= 9 * 3600 + 5, check["wait_seconds"]
