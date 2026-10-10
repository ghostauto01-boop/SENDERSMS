"""
Shared test fixtures for SendSMS backend tests.

``api_db`` / ``api_client`` are opt-in building blocks for tests that drive the
real FastAPI app over HTTP against an in-memory SQLite database. They are named
differently from the ``db`` / ``client`` fixtures many older modules define
locally on purpose: a module-level fixture always wins, so adding these cannot
change what an existing test sees.
"""

import pytest
import pytest_asyncio

# Event loop is managed automatically by pytest-asyncio.
# No manual override needed.


@pytest_asyncio.fixture
async def api_db():
    """One in-memory database + session, shared by the test and the app."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.database import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def api_client(api_db):
    """The real app, authenticated as an admin, using ``api_db``."""
    from httpx import ASGITransport, AsyncClient

    from app.database import get_db
    from app.main import app
    from app.models.user import User
    from app.security.auth import get_current_user

    async def _get_db():
        yield api_db

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="tester", email="tester@example.com",
        password_hash="x", role="admin", is_active=True,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def sms_gateway_configured(monkeypatch):
    """Pretend SMS-Gate credentials are present, as in a normal deployment."""
    from app.config import settings

    monkeypatch.setattr(settings, "SMSGATE_BASE_URL", "https://api.sms-gate.app/3rdparty/v1")
    monkeypatch.setattr(settings, "SMSGATE_USERNAME", "user")
    monkeypatch.setattr(settings, "SMSGATE_PASSWORD", "pass")


@pytest.fixture
def stub_queue(monkeypatch):
    """Swallow Celery enqueues (no broker in tests); returns the list of calls."""
    calls: list[tuple] = []

    def _enqueue(task, *args, **kwargs):
        calls.append((getattr(task, "name", str(task)), args))

    monkeypatch.setattr("app.tasks.queue.enqueue", _enqueue)
    return calls


# --------------------------------------------------------------------------
# The sending gate and the wall clock
# --------------------------------------------------------------------------
#
# The sending rules are ON by default in production: a 09:00-17:00 window,
# weekends off, a daily cap. Left alone, that would make every test that sends
# something pass or fail depending on what time of day (and which weekday) the
# suite happens to run. Tests that are not ABOUT those rules therefore run with
# the old open-door rules; tests that are about them opt back in with
# ``@pytest.mark.real_sending_defaults`` and pin the clock with ``sending_clock``.


@pytest.fixture(autouse=True)
def _open_sending_rules(request, monkeypatch):
    if request.node.get_closest_marker("real_sending_defaults"):
        return
    from app.config import settings
    from app.services import sending_limits

    for key, value in {
        "enable_daily_limit": False,
        "sending_start_time": "",
        "sending_end_time": "",
        "allow_weekends": True,
    }.items():
        monkeypatch.setitem(sending_limits.DEFAULTS, key, value)
    monkeypatch.setitem(sending_limits.DEFAULTS, "email_daily_per_mailbox", 0)
    monkeypatch.setattr(settings, "EMAIL_DEFAULT_DAILY_LIMIT", 0, raising=False)


@pytest.fixture
def sending_clock(monkeypatch):
    """Pin the instant the sending gate believes it is. Returns a setter.

    Default: Wednesday 2026-09-02 12:00 Africa/Lagos -- a weekday, inside the
    09:00-17:00 window.
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from app.services import sending_limits

    state = {"now": datetime(2026, 9, 2, 12, 0, tzinfo=ZoneInfo("Africa/Lagos"))
             .astimezone(timezone.utc)}
    monkeypatch.setattr(sending_limits, "_utcnow", lambda: state["now"])

    def _set(moment):
        state["now"] = moment.astimezone(timezone.utc)
        return state["now"]

    _set.now = lambda: state["now"]
    return _set
