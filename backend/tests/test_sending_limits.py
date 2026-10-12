"""Application-wide sending restrictions stay retired.

Campaign controls live in ``campaign_sending``; this module exists only as a
permissive compatibility shim and to read the email circuit-breaker settings.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
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


@pytest.mark.asyncio
async def test_legacy_global_gate_does_not_enforce_saved_caps_or_windows(db):
    from app.services.sending_limits import SendingGate

    db.add_all([
        SystemSetting(key="enable_daily_limit", value="true", category="sending_rules"),
        SystemSetting(key="daily_maximum", value="1", category="sending_rules"),
        SystemSetting(key="sending_start_time", value="09:00", category="sending_rules"),
        SystemSetting(key="sending_end_time", value="17:00", category="sending_rules"),
        SystemSetting(key="allow_weekends", value="false", category="sending_rules"),
    ])
    await db.flush()

    gate = SendingGate(db, channel="sms")
    check = await gate.check()

    assert check == {"allowed": True, "wait_seconds": 0, "reason": None, "next_window": None}
    assert await gate.room() is None
    assert gate.effective_interval({}) == 0
    assert (await gate.status())["global_limits_disabled"] is True


@pytest.mark.asyncio
async def test_breaker_configuration_is_the_only_legacy_setting_still_loaded(db):
    from app.services.sending_limits import get_sending_rules

    db.add_all([
        SystemSetting(key="breaker_enabled", value="false", category="sending_rules"),
        SystemSetting(key="daily_maximum", value="1", category="sending_rules"),
    ])
    await db.flush()

    rules = await get_sending_rules(db)

    assert rules["breaker_enabled"] is False
    assert rules["breaker_bounce_pct"] == 2.0
    assert "daily_maximum" not in rules
    assert "sending_start_time" not in rules
