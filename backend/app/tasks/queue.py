"""Safe Celery enqueue helpers.

Celery's ``.delay()`` talks to the broker synchronously. When Redis is
unreachable (an external Upstash instance in production) kombu raises
``OperationalError`` after a long connection timeout, which surfaced to the
user as an unhandled HTTP 500. These helpers turn that into an explicit,
fast, catchable failure so callers can roll back state and return 503.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class QueueUnavailable(RuntimeError):
    """Raised when a task could not be handed to the broker."""


def enqueue(task, *args, **kwargs) -> Optional[Any]:
    """Enqueue a Celery task, converting broker errors into QueueUnavailable.

    Returns the AsyncResult on success.
    """
    task_name = getattr(task, "name", repr(task))
    try:
        return task.delay(*args, **kwargs)
    except Exception as exc:  # broker down, DNS failure, auth error, ...
        logger.error("Failed to enqueue %s: %s", task_name, exc)
        raise QueueUnavailable(
            "Background task queue is unavailable. Check that the Redis broker "
            "(REDIS_URL) is reachable, then try again."
        ) from exc


def enqueue_at(task, eta, *args, **kwargs) -> Optional[Any]:
    """Same as :func:`enqueue` but schedules the task for a future time."""
    task_name = getattr(task, "name", repr(task))
    try:
        return task.apply_async(args=args, kwargs=kwargs, eta=eta)
    except Exception as exc:
        logger.error("Failed to schedule %s: %s", task_name, exc)
        raise QueueUnavailable(
            "Background task queue is unavailable. Check that the Redis broker "
            "(REDIS_URL) is reachable, then try again."
        ) from exc


def try_enqueue(task, *args, **kwargs) -> bool:
    """Best-effort enqueue for non-critical work. Never raises.

    Returns True if the task was queued.
    """
    try:
        enqueue(task, *args, **kwargs)
        return True
    except QueueUnavailable:
        return False
