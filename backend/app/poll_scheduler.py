"""Decide how long the inline poller should sleep between cycles.

Background
----------
The inline poller (``app.main._poll``) refreshes delivery statuses and sends
anything that has come due: scheduled messages, scheduled campaign launches,
follow-ups, meeting reminders, ads drips. It used to run a full pass every
``INLINE_POLL_INTERVAL`` (30 s) for as long as the process was alive — which,
with an uptime pinger or a browser tab left open, is 24 hours a day.

That matters on the free hosting stack: Neon suspends a free project's
compute after 5 minutes without any query, and the plan includes only 100
CU-hours per month. A database that is queried every 30 seconds never
suspends, burns roughly 180 CU-hours a month, and is switched off by the
provider around day 17 — at which point every page shows an error.

The rule here keeps the 30-second cadence whenever it is *useful* and backs
off when it is not:

* **active** — a user request arrived recently, the previous cycle actually
  did something, the database is known to have pending work, or something
  is due before the idle interval would elapse → ``interval``.
* **idle** — nothing to do and nobody around → ``idle_interval`` (15 min by
  default), or exactly until the next known due time if that is sooner.
* **database down** — every attempt would fail; back off to the idle
  interval so a suspended database is not hit every 30 seconds.

A request arriving while idle wakes the loop immediately (see
``PollActivity.touch``), so opening the app always gets a fresh pass.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PollActivity:
    """Process-local signals the scheduler reads."""

    last_request_at: float = 0.0
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def touch(self) -> None:
        """Record user/webhook traffic and wake a sleeping poller."""
        self.last_request_at = time.time()
        self.wake.set()


def next_poll_delay(
    *,
    interval: float,
    idle_interval: float,
    active_window: float,
    now: float,
    last_request_at: float,
    did_work: bool,
    has_pending_work: bool,
    next_due_at: Optional[float],
    db_ok: bool,
) -> float:
    """Seconds to sleep before the next cycle.

    ``next_due_at`` is a unix timestamp of the earliest known future job, or
    ``None`` when nothing is scheduled.
    """
    interval = max(1.0, float(interval))
    idle_interval = max(interval, float(idle_interval))

    if not db_ok:
        # Do not hammer a database that is refusing connections. The next
        # attempt is still bounded so recovery is noticed within one idle
        # interval (and any incoming request wakes the loop earlier).
        return idle_interval

    if did_work or has_pending_work:
        return interval

    if last_request_at and (now - last_request_at) < active_window:
        return interval

    if next_due_at is not None:
        until_due = next_due_at - now
        if until_due <= interval:
            return interval
        return min(idle_interval, until_due)

    return idle_interval
