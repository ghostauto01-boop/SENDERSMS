"""Persistent per-thread event loop helper for synchronous Celery tasks.

Why this exists
---------------
SQLAlchemy's async ``engine`` pools ``asyncpg`` connections bound to the event
loop that opened them. Calling ``asyncio.run(coro)`` (as ``mailbox_tasks`` did)
or ``loop = asyncio.new_event_loop(); ...; loop.close()`` (as ``system_tasks``
did) closes the loop while leaving the ``asyncpg`` socket inside the pool — so
the next Celery task fails with ``RuntimeError: Event loop is closed`` or
``Future attached to a different loop``. Meanwhile ``asyncio.get_event_loop()``
raises ``RuntimeError: There is no current event loop in thread`` in Python
3.12+ worker threads (and immediately after any ``asyncio.run()`` call).

Keeping one open event loop per worker thread solves all three failure modes.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine, TypeVar

_T = TypeVar("_T")
_thread_state = threading.local()


def get_worker_loop() -> asyncio.AbstractEventLoop:
    """Return a persistent, open event loop for the current OS thread."""
    loop: asyncio.AbstractEventLoop | None = getattr(_thread_state, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _thread_state.loop = loop
    return loop


def run_in_worker_loop(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run ``coro`` to completion on the current thread's persistent loop."""
    return get_worker_loop().run_until_complete(coro)
