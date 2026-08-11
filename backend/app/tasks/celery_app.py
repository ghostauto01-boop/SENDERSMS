"""
Celery application configuration.
Uses Redis as broker and result backend.
"""

from datetime import timedelta

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "sendsms",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.sms_tasks",
        "app.tasks.campaign_tasks",
        "app.tasks.notification_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Nothing in this codebase ever reads a task result (no AsyncResult.get()
    # anywhere), and keeping the result backend active made every enqueue call
    # try to reach Redis twice. When Redis was down it retried 20 times before
    # giving up, which is what turned a broker outage into a ~19s API hang.
    task_ignore_result=True,
    result_backend_max_retries=3,
    timezone=settings.DEFAULT_TIMEZONE,
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=300,
    task_time_limit=600,
    broker_connection_retry_on_startup=True,
    # Fail fast when the broker is unreachable. The defaults retry for ~20s,
    # which blocked API requests that only wanted to enqueue a task; callers
    # now get a prompt 503 instead of a long hang.
    broker_transport_options={
        "socket_connect_timeout": 3,
        "socket_timeout": 3,
        "max_retries": 1,
        "retry_policy": {"timeout": 3.0},
    },
    broker_connection_timeout=3,
    broker_connection_max_retries=1,
)

# --- Periodic Beat Schedule ---
celery_app.conf.beat_schedule = {
    "sync-delivery-statuses": {
        "task": "app.tasks.sms_tasks.sync_delivery_status",
        "schedule": timedelta(minutes=3),
    },
    "gateway-health-check": {
        "task": "app.tasks.sms_tasks.gateway_health_check",
        "schedule": timedelta(minutes=3),
    },
    "process-pending-notifications": {
        "task": "app.tasks.notification_tasks.process_pending_notifications",
        "schedule": timedelta(minutes=2),
    },
    "process-campaigns": {
        "task": "app.tasks.campaign_tasks.process_running_campaigns",
        "schedule": timedelta(minutes=2),
    },
}
