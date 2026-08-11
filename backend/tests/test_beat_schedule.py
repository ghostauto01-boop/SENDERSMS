"""Guard: every Celery beat entry must point at a task that actually exists.

Regression: the schedule referenced app.tasks.sms_tasks.poll_inbox, which was
never defined. Beat dispatched it every 45s and the worker answered with
"Received unregistered task" -- filling the production log with errors and
making a healthy deploy look broken.
"""
from app.tasks.celery_app import celery_app


def _loaded_app():
    # Tasks are declared via include=[...]; they only register once the worker
    # (or this call) imports those modules.
    celery_app.loader.import_default_modules()
    return celery_app


def test_every_beat_task_is_registered():
    app = _loaded_app()
    missing = sorted(
        entry["task"]
        for entry in app.conf.beat_schedule.values()
        if entry["task"] not in app.tasks
    )
    assert not missing, f"beat schedule references unregistered task(s): {missing}"


def test_beat_schedule_is_not_empty():
    app = _loaded_app()
    assert app.conf.beat_schedule, "beat schedule unexpectedly empty"
