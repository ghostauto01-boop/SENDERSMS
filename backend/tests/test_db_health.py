"""Database outage handling.

When Postgres is unavailable every page used to fail with a blank
``Internal Server Error`` and nothing said why. These tests pin the new
behaviour:

1. Driver errors are classified into a safe kind + message + hint (the Neon
   free-plan quota suspension being the important one) with no DSN or
   traceback leaking through.
2. ``GET /api/v1/health/db`` reports that summary (200 healthy / 503 not).
3. Any route that dies on a database error answers a JSON 503 carrying the
   same summary, so the UI can show one clear banner.
4. Non-database crashes are still a JSON 500 without internals.
"""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError, ProgrammingError

from app import db_health
from tests.test_inbound_receive import client, db  # noqa: F401


NEON_QUOTA_TEXT = (
    "Your account or project has exceeded the compute time quota. "
    "Upgrade your plan to increase limits."
)


def _operational(msg: str) -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception(msg))


@pytest.fixture(autouse=True)
def _reset_status():
    db_health.status.__init__()
    yield
    db_health.status.__init__()


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

def test_neon_quota_error_is_recognised():
    kind, message = db_health.classify_db_error(_operational(NEON_QUOTA_TEXT))
    assert kind == db_health.KIND_QUOTA
    assert "compute time quota" in message
    assert "1st of next month" in db_health.hint_for(kind)


def test_data_transfer_quota_is_also_a_quota():
    kind, _ = db_health.classify_db_error(
        _operational("Your project has exceeded the data transfer quota. Upgrade your plan.")
    )
    assert kind == db_health.KIND_QUOTA


@pytest.mark.parametrize(
    "text,kind",
    [
        ("password authentication failed for user \"neondb_owner\"", db_health.KIND_AUTH),
        ("[Errno 111] Connect call failed ('10.0.0.1', 5432)", db_health.KIND_UNREACHABLE),
        ("connection refused", db_health.KIND_UNREACHABLE),
        ("timed out", db_health.KIND_TIMEOUT),
        ("relation \"contacts\" does not exist", db_health.KIND_SCHEMA),
        ("something odd happened", db_health.KIND_UNKNOWN),
    ],
)
def test_other_failures_are_classified(text, kind):
    assert db_health.classify_db_error(_operational(text))[0] == kind


def test_timeout_error_without_text():
    kind, message = db_health.classify_db_error(asyncio.TimeoutError())
    assert kind == db_health.KIND_TIMEOUT
    assert message


def test_message_never_leaks_connection_string_or_sql():
    exc = OperationalError(
        "SELECT * FROM secrets",
        {"k": "v"},
        Exception("could not connect to postgresql://user:hunter2@db.neon.tech/app"),
    )
    _, message = db_health.classify_db_error(exc)
    assert "hunter2" not in message
    assert "[SQL:" not in message
    assert "[redacted]" in message


def test_is_db_error_distinguishes_driver_failures():
    assert db_health.is_db_error(_operational("x"))
    assert db_health.is_db_error(asyncio.TimeoutError())
    assert not db_health.is_db_error(ValueError("bad input"))
    assert not db_health.is_db_error(KeyError("k"))


def test_status_tracks_transitions():
    s = db_health.status
    assert s.ok is None
    s.note_error(_operational(NEON_QUOTA_TEXT))
    assert s.ok is False and s.kind == db_health.KIND_QUOTA and s.consecutive_failures == 1
    s.note_error(_operational(NEON_QUOTA_TEXT))
    assert s.consecutive_failures == 2
    s.note_ok()
    assert s.ok is True and s.kind is None and s.consecutive_failures == 0
    d = s.as_dict()
    assert d["ok"] is True and d["hint"] is None


# --------------------------------------------------------------------------
# HTTP behaviour
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_db_reports_ok_with_a_working_database(client, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app import main as main_mod

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(main_mod, "async_session_factory", factory)
    try:
        r = await client.get("/api/v1/health/db")
    finally:
        await engine.dispose()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["kind"] is None
    assert body["hint"] is None


@pytest.mark.asyncio
async def test_health_db_explains_a_suspended_neon_database(client, monkeypatch):
    from app import main as main_mod

    class _Boom:
        def __call__(self):
            return self

        async def __aenter__(self):
            raise _operational(NEON_QUOTA_TEXT)

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(main_mod, "async_session_factory", _Boom())
    r = await client.get("/api/v1/health/db")
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["kind"] == "quota_exceeded"
    assert "compute time quota" in body["message"]
    assert "Neon" in body["hint"]
    assert r.headers.get("cache-control") == "no-store"


@pytest.mark.asyncio
async def test_any_route_turns_a_db_outage_into_a_json_503(client):
    """A route whose DB session blows up must not return a blank 500."""
    from app.main import app
    from app.database import get_db

    async def _dead_db():
        raise _operational(NEON_QUOTA_TEXT)
        yield  # pragma: no cover — makes this an async generator dependency

    app.dependency_overrides[get_db] = _dead_db
    try:
        r = await client.get("/api/v1/auth/access")
    finally:
        # Restore the fixture's working override.
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["error_kind"] == "database"
    assert body["db"]["kind"] == "quota_exceeded"
    assert body["detail"].startswith("Database unavailable:")
    assert "Traceback" not in r.text
    # The outage is now the last known state for the process.
    assert db_health.status.ok is False


@pytest.mark.asyncio
async def test_non_database_crashes_are_a_generic_json_500(client):
    """A plain bug in a handler must not leak its message or a traceback."""
    from app.main import app
    from app.database import get_db

    async def _buggy_db():
        raise ValueError("secret detail 12345")
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = _buggy_db
    try:
        r = await client.get("/api/v1/auth/access")
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 500
    assert r.json()["detail"].startswith("Internal server error")
    assert "12345" not in r.text
    assert "Traceback" not in r.text


# ---------------------------------------------------------------------------
# A query the database refuses is NOT an outage
# ---------------------------------------------------------------------------
#
# The production inbox failed with
#   column "conversations.status" must appear in the GROUP BY clause ...
# and the app reported it as "Database unavailable", which sent everyone
# looking at Neon/Render for a database that was perfectly healthy. The
# database had answered — it rejected our SQL. These tests pin the distinction.


def test_a_rejected_statement_is_not_reported_as_an_outage():
    exc = ProgrammingError(
        "SELECT 1",
        {},
        Exception(
            'column "conversations.status" must appear in the GROUP BY clause '
            "or be used in an aggregate function"
        ),
    )
    kind, message = db_health.classify_db_error(exc)
    assert kind == db_health.KIND_QUERY
    assert "GROUP BY" in message
    assert not db_health.is_db_down_kind(kind)
    assert db_health.is_query_rejected(exc)
    hint = db_health.hint_for(kind)
    assert "application bug" in hint
    assert "nothing is wrong with your data" in hint


@pytest.mark.parametrize(
    "text",
    [
        'column "x" must appear in the GROUP BY clause or be used in an aggregate function',
        'operator does not exist: integer = boolean',
        'function lower(integer) does not exist',
        'invalid input syntax for type integer: "abc"',
        'syntax error at or near "SELCT"',
    ],
)
def test_statement_bugs_are_their_own_kind(text):
    assert db_health.classify_db_error(_operational(text))[0] == db_health.KIND_QUERY


def test_a_rejected_statement_never_marks_the_database_down():
    s = db_health.status
    s.ok = True
    kind, _ = s.note_error(ProgrammingError("SELECT 1", {}, Exception(
        'column "conversations.status" must appear in the GROUP BY clause'
    )))
    assert kind == db_health.KIND_QUERY
    # The database is still fine: no outage banner, no back-off.
    assert s.ok is True
    assert s.kind is None
    assert s.consecutive_failures == 0
    # ... but the bug is recorded for whoever reads /health/db next.
    assert "GROUP BY" in (s.last_query_error or "")


@pytest.mark.asyncio
async def test_a_rejected_statement_answers_a_truthful_500(client):
    """Not a 503 "Database unavailable": the database is up, the query is wrong."""
    from app.database import get_db
    from app.main import app

    async def _bad_query_db():
        raise ProgrammingError("SELECT 1", {}, Exception(
            'column "conversations.status" must appear in the GROUP BY clause '
            "or be used in an aggregate function"
        ))
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = _bad_query_db
    try:
        r = await client.get("/api/v1/inbox/conversations")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r.status_code == 500, r.text
    body = r.json()
    assert body["code"] == "DATABASE_QUERY_REJECTED"
    assert "database itself is running" in body["detail"]
    assert "Database unavailable" not in body["detail"]
    assert body["db"]["ok"] is True
    assert db_health.status.ok is not False
