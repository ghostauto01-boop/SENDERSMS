"""Idle-aware inline poller.

The poller used to query the database every 30 seconds for as long as the
process lived. On Neon's free plan that alone uses ~180 CU-hours a month
against a 100 CU-hour allowance, so the database was switched off by the
provider mid-month and every page broke. These tests pin the scheduling rule
that keeps the 30 s cadence when it is useful and backs off when it is not.
"""

import asyncio
import time

import pytest

from app.poll_scheduler import PollActivity, next_poll_delay

NOW = 1_700_000_000.0
BASE = dict(
    interval=30,
    idle_interval=900,
    active_window=600,
    now=NOW,
    last_request_at=0.0,
    did_work=False,
    has_pending_work=False,
    next_due_at=None,
    db_ok=True,
)


def _delay(**overrides):
    return next_poll_delay(**{**BASE, **overrides})


def test_idle_deployment_sleeps_for_the_idle_interval():
    assert _delay() == 900


def test_recent_user_request_keeps_full_speed():
    assert _delay(last_request_at=NOW - 60) == 30


def test_request_older_than_the_active_window_counts_as_idle():
    assert _delay(last_request_at=NOW - 601) == 900


def test_work_done_last_cycle_keeps_full_speed():
    assert _delay(did_work=True) == 30


def test_pending_work_keeps_full_speed():
    """Running campaign / queued messages / statuses still settling."""
    assert _delay(has_pending_work=True) == 30


def test_job_due_soon_keeps_full_speed():
    assert _delay(next_due_at=NOW + 20) == 30


def test_overdue_job_keeps_full_speed():
    assert _delay(next_due_at=NOW - 5) == 30


def test_sleeps_exactly_until_the_next_job_when_sooner_than_idle():
    assert _delay(next_due_at=NOW + 300) == 300


def test_far_future_job_is_capped_by_the_idle_interval():
    assert _delay(next_due_at=NOW + 4 * 3600) == 900


def test_suspended_database_is_not_hammered():
    """Every attempt would fail: back off even if a user is active."""
    assert _delay(db_ok=False, last_request_at=NOW, did_work=True) == 900


def test_idle_interval_never_shorter_than_interval():
    assert _delay(interval=60, idle_interval=10) == 60


def test_activity_touch_wakes_a_sleeping_loop():
    async def run():
        act = PollActivity()
        assert act.last_request_at == 0.0
        waiter = asyncio.create_task(asyncio.wait_for(act.wake.wait(), timeout=5))
        await asyncio.sleep(0)
        before = time.time()
        act.touch()
        await waiter
        assert act.last_request_at >= before
        assert act.wake.is_set()

    asyncio.run(run())


@pytest.mark.asyncio
async def test_api_traffic_touches_activity_but_health_probes_do_not():
    from httpx import ASGITransport, AsyncClient
    from app.main import app, poll_activity

    poll_activity.last_request_at = 0.0
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.get("/api/v1/health")
        assert poll_activity.last_request_at == 0.0, "uptime pings must not wake the DB"
        await c.get("/api/v1/auth/me")  # 401, but it is real API traffic
        assert poll_activity.last_request_at > 0.0


# --------------------------------------------------------------------------
# Integration: the real poller against a real (SQLite) and a dead database
# --------------------------------------------------------------------------

@pytest.fixture
def _quiet_limiter():
    from app.security.rate_limit import limiter
    limiter.enabled = False
    yield
    limiter.enabled = True


@pytest.mark.asyncio
async def test_poll_reports_a_dead_database_instead_of_looping(monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app import db_health
    from app import main as main_mod

    class _Dead:
        def __call__(self):
            return self

        async def __aenter__(self):
            raise OperationalError(
                "SELECT 1", {},
                Exception("Your account or project has exceeded the compute time quota."),
            )

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(main_mod, "async_session_factory", _Dead())
    db_health.status.__init__()
    with pytest.raises(main_mod._DatabaseDown):
        await main_mod._poll()
    assert db_health.status.ok is False
    assert db_health.status.kind == db_health.KIND_QUOTA

    # And the scheduler backs off for it.
    assert next_poll_delay(**{**BASE, "db_ok": False}) == BASE["idle_interval"]
    db_health.status.__init__()


@pytest.mark.asyncio
async def test_pending_work_snapshot_finds_the_next_job(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app import main as main_mod
    from app.database import Base
    from app.models.campaign import Campaign
    from app.models.scheduled import ScheduledMessage
    from app.models.meeting import Meeting

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(main_mod, "async_session_factory", factory)
    try:
        # Empty database: nothing pending, nothing due.
        has_pending, next_due = await main_mod._pending_work_snapshot()
        assert has_pending is False and next_due is None

        now = datetime.now(timezone.utc)
        async with factory() as db:
            db.add(ScheduledMessage(phone_number="+2348012345678", body="hi",
                                    schedule_at=now + timedelta(hours=2), status="pending"))
            db.add(Campaign(name="later", status="scheduled",
                            scheduled_at=now + timedelta(hours=5)))
            # Meeting in 3h with a 90-minute reminder => due in 1.5h (the earliest).
            db.add(Meeting(title="Demo", starts_at=now + timedelta(hours=3),
                           ends_at=now + timedelta(hours=4), status="scheduled",
                           send_sms_reminder=1, reminder_minutes_json="[90]"))
            await db.commit()

        has_pending, next_due = await main_mod._pending_work_snapshot()
        assert has_pending is False
        expected = (now + timedelta(minutes=90)).timestamp()
        assert next_due is not None and abs(next_due - expected) < 5

        # A running campaign is pending work regardless of timers.
        async with factory() as db:
            db.add(Campaign(name="live", status="running"))
            await db.commit()
        has_pending, _ = await main_mod._pending_work_snapshot()
        assert has_pending is True
    finally:
        await engine.dispose()
