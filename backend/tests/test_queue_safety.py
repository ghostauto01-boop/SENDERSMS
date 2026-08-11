"""Tests for safe Celery enqueueing.

Regression cover for the Redis-outage blocker: process_campaign.delay() threw
kombu OperationalError straight out of the request handler, producing a 500
and leaving the campaign stuck in "running".
"""

import pytest

from app.tasks.queue import QueueUnavailable, enqueue, try_enqueue


class _BrokenTask:
    name = "tasks.broken"

    def delay(self, *args, **kwargs):
        raise OSError("Error 111 connecting to localhost:6379. Connection refused.")


class _WorkingTask:
    name = "tasks.working"

    def __init__(self):
        self.calls = []

    def delay(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "task-id"


class TestEnqueue:
    def test_successful_enqueue_returns_result(self):
        task = _WorkingTask()
        assert enqueue(task, 42) == "task-id"
        assert task.calls == [((42,), {})]

    def test_broker_failure_raises_queue_unavailable(self):
        with pytest.raises(QueueUnavailable):
            enqueue(_BrokenTask(), 1)

    def test_queue_unavailable_message_is_actionable(self):
        with pytest.raises(QueueUnavailable) as exc:
            enqueue(_BrokenTask(), 1)
        assert "REDIS_URL" in str(exc.value)

    def test_original_error_is_chained(self):
        with pytest.raises(QueueUnavailable) as exc:
            enqueue(_BrokenTask(), 1)
        assert isinstance(exc.value.__cause__, OSError)


class TestTryEnqueue:
    def test_returns_true_on_success(self):
        assert try_enqueue(_WorkingTask(), 1) is True

    def test_returns_false_instead_of_raising(self):
        assert try_enqueue(_BrokenTask(), 1) is False
