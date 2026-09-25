"""Database health: classify failures and remember the last known state.

Why this exists
---------------
Every page of the app reads from Postgres. When the database itself is
unavailable, *every* API call used to die with a blank ``Internal Server
Error`` and the UI showed an error on every screen with no hint of why. The
most common cause on the free hosting stack is not a bug in the app at all:
Neon suspends a free project's compute once its monthly CU-hour quota is used
up, and every connection attempt is rejected with

    ERROR: Your account or project has exceeded the compute time quota.
    Upgrade your plan to increase limits.

This module turns those raw driver errors into a small, safe summary
(``kind`` + human message + what-to-do hint) that:

* ``GET /api/v1/health/db`` reports, so the cause is visible in one request;
* the global exception handler attaches to a ``503`` JSON body, so the
  frontend can show one clear banner instead of twenty broken widgets;
* the inline poller uses to back off instead of hammering a dead database.

Nothing here ever includes the DSN, credentials or a traceback.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError, SQLAlchemyError

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Classification
# ----------------------------------------------------------------------------

KIND_QUOTA = "quota_exceeded"
KIND_SUSPENDED = "suspended"
KIND_AUTH = "auth_failed"
KIND_UNREACHABLE = "unreachable"
KIND_TIMEOUT = "timeout"
KIND_SCHEMA = "schema"
KIND_UNKNOWN = "unknown"

# Order matters: the first matching pattern wins.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (KIND_QUOTA, re.compile(r"exceeded .*quota|quota exceeded|compute time quota|data transfer quota", re.I)),
    (KIND_SUSPENDED, re.compile(r"endpoint is disabled|compute is suspended|project is suspended|suspended", re.I)),
    (KIND_AUTH, re.compile(r"password authentication failed|authentication failed|invalid authorization|role .* does not exist|no pg_hba", re.I)),
    (KIND_TIMEOUT, re.compile(r"timed? ?out|timeout", re.I)),
    (KIND_UNREACHABLE, re.compile(r"connection refused|could not connect|connect call failed|name or service not known|nodename nor servname|network is unreachable|connection reset|server closed the connection|ssl syscall|too many connections|remaining connection slots", re.I)),
    (KIND_SCHEMA, re.compile(r"does not exist|undefined column|undefined table|no such table|no such column", re.I)),
]

_HINTS = {
    KIND_QUOTA: (
        "The database provider (Neon free plan) has suspended the database "
        "because its monthly compute quota is used up. Your data is safe and "
        "nothing is deleted. It comes back automatically on the 1st of next "
        "month, or immediately if you upgrade the Neon project (or point "
        "DATABASE_URL at a new Neon project)."
    ),
    KIND_SUSPENDED: (
        "The database compute is suspended by the provider. Check the Neon "
        "dashboard for the project status and any quota or billing notice."
    ),
    KIND_AUTH: (
        "The database rejected the credentials. Check DATABASE_URL in the "
        "Render environment matches the connection string shown in Neon."
    ),
    KIND_TIMEOUT: (
        "The database did not answer in time. It may be waking up from sleep; "
        "retry in a few seconds. If it persists, check the Neon dashboard."
    ),
    KIND_UNREACHABLE: (
        "The database host could not be reached. Check the Neon project is "
        "running and DATABASE_URL points at the right host."
    ),
    KIND_SCHEMA: (
        "A table or column is missing. Restart the service so the schema "
        "auto-repair runs, or run scripts/migrate_existing_db.sql."
    ),
    KIND_UNKNOWN: "The database returned an unexpected error. Check the service logs on Render.",
}

# Things that must never appear in a message shown to a browser.
_SCRUB = [
    re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^\s'\"]+", re.I),  # any DSN
    re.compile(r"password=\S+", re.I),
]


def _root_message(exc: BaseException) -> str:
    """Shortest useful description of the underlying driver error."""
    cause: BaseException = exc
    # SQLAlchemy wraps the DBAPI error; asyncpg errors carry the server text.
    seen = 0
    while getattr(cause, "orig", None) is not None and seen < 5:
        cause = cause.orig  # type: ignore[attr-defined]
        seen += 1
    while cause.__cause__ is not None and seen < 10:
        cause = cause.__cause__
        seen += 1
    text = str(cause).strip() or type(cause).__name__
    # SQLAlchemy appends "(Background on this error at: https://sqlalche.me/...)"
    text = re.sub(r"\s*\(Background on this error at: [^)]*\)", "", text)
    # Drop the "[SQL: ...] [parameters: ...]" tail — noise for a user.
    text = re.split(r"\s*\[SQL:", text, maxsplit=1)[0]
    for pat in _SCRUB:
        text = pat.sub("[redacted]", text)
    return text.splitlines()[0][:300]


def is_db_error(exc: BaseException) -> bool:
    """True when ``exc`` is a database/driver failure rather than app logic."""
    if isinstance(exc, (OperationalError, InterfaceError, DBAPIError)):
        return True
    if isinstance(exc, SQLAlchemyError):
        # ProgrammingError etc. still come from the database.
        return True
    mod = type(exc).__module__ or ""
    if mod.startswith("asyncpg") or mod.startswith("aiosqlite") or mod.startswith("sqlite3"):
        return True
    if isinstance(exc, (ConnectionError, OSError)) and "connect" in str(exc).lower():
        return True
    if isinstance(exc, TimeoutError):
        return True
    return False


def classify_db_error(exc: BaseException) -> tuple[str, str]:
    """Return ``(kind, safe_message)`` for a database failure."""
    message = _root_message(exc)
    if isinstance(exc, TimeoutError) and not message:
        return KIND_TIMEOUT, "Timed out waiting for the database."
    for kind, pattern in _PATTERNS:
        if pattern.search(message):
            return kind, message
    if isinstance(exc, TimeoutError):
        return KIND_TIMEOUT, message or "Timed out waiting for the database."
    return KIND_UNKNOWN, message


def hint_for(kind: str) -> str:
    return _HINTS.get(kind, _HINTS[KIND_UNKNOWN])


# ----------------------------------------------------------------------------
# Last-known state (process local; cheap to read from any request)
# ----------------------------------------------------------------------------


@dataclass
class DbStatus:
    ok: Optional[bool] = None  # None = never checked
    kind: Optional[str] = None
    message: Optional[str] = None
    last_ok_at: Optional[float] = None
    last_error_at: Optional[float] = None
    consecutive_failures: int = 0
    _extra: dict = field(default_factory=dict, repr=False)

    def note_ok(self) -> None:
        if self.ok is False:
            logger.info("Database is reachable again")
        self.ok = True
        self.kind = None
        self.message = None
        self.last_ok_at = time.time()
        self.consecutive_failures = 0

    def note_error(self, exc: BaseException) -> tuple[str, str]:
        kind, message = classify_db_error(exc)
        first = self.ok is not False or self.kind != kind
        self.ok = False
        self.kind = kind
        self.message = message
        self.last_error_at = time.time()
        self.consecutive_failures += 1
        if first:
            logger.error("Database unavailable (%s): %s", kind, message)
        return kind, message

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "kind": self.kind,
            "message": self.message,
            "hint": hint_for(self.kind) if self.ok is False and self.kind else None,
            "last_ok_at": self.last_ok_at,
            "last_error_at": self.last_error_at,
            "consecutive_failures": self.consecutive_failures,
        }


status = DbStatus()


def error_payload(exc: BaseException) -> dict:
    """JSON body for a 503 caused by ``exc`` (also records it in ``status``)."""
    kind, message = status.note_error(exc)
    return {
        "detail": f"Database unavailable: {message}",
        "error_kind": "database",
        "db": {"ok": False, "kind": kind, "message": message, "hint": hint_for(kind)},
    }


async def check_db(session_factory) -> dict:
    """Run ``SELECT 1`` through ``session_factory`` and update ``status``."""
    from sqlalchemy import text

    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        status.note_ok()
    except Exception as exc:  # noqa: BLE001 — we want every failure reported
        status.note_error(exc)
    return status.as_dict()
