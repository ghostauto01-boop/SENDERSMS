"""Background-worker heartbeat.

One tiny task, run by Celery beat every minute and mirrored by the inline
poller (``app.main._poll``) on every cycle, that stamps
``worker.heartbeat_at`` in ``system_settings``. The health endpoint and the
UI banner read it: when no processor has completed a cycle for 5 minutes
while follow-ups or sends are due, something is broken — a dead worker, a
crashed beat, a stopped poller — and the operator sees a red banner instead
of silently stalled follow-ups.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.system_settings import (
    WORKER_HEARTBEAT,
    WORKER_HEARTBEAT_SOURCE,
    set_setting,
)

logger = logging.getLogger(__name__)

#: A heartbeat older than this while work is due means "nothing is processing".
STALE_AFTER_SECONDS = 5 * 60


async def touch_worker_heartbeat(db: AsyncSession, source: str) -> float:
    """Stamp the heartbeat; returns the unix time written."""
    now = time.time()
    await set_setting(
        db, WORKER_HEARTBEAT, f"{now:.1f}", category="system",
        description="Unix time a background processor last completed a cycle",
    )
    await set_setting(
        db, WORKER_HEARTBEAT_SOURCE, source, category="system",
        description="Which processor wrote the last heartbeat",
    )
    return now


def worker_heartbeat_age(last_heartbeat: float | None, *, now: float | None = None) -> float | None:
    """Seconds since the last heartbeat, or None when none was ever written."""
    if not last_heartbeat:
        return None
    return max(0.0, (now if now is not None else time.time()) - float(last_heartbeat))


# ---------------------------------------------------------------------------
# Celery entry points (no-op silently when the broker is down; the inline
# poller's own stamp covers that case).
# ---------------------------------------------------------------------------

try:  # Celery is an optional runtime dependency for tests that only use the API.
    from app.tasks.celery_app import celery_app
except Exception:  # noqa: BLE001 — importing without a broker must not explode
    celery_app = None


if celery_app is not None:

    @celery_app.task(name="app.tasks.system_tasks.worker_heartbeat")
    def worker_heartbeat() -> float:
        """Beat entrypoint: stamp the heartbeat once per minute."""
        import asyncio

        from app.database import async_session_factory

        async def _run() -> float:
            async with async_session_factory() as db:
                value = await touch_worker_heartbeat(db, source="celery-beat")
                await db.commit()
                return value

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()
