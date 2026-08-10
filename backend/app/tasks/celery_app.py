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
    timezone=settings.DEFAULT_TIMEZONE,
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=300,
    task_time_limit=600,
    broker_connection_retry_on_startup=True,
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
    "poll-inbox": {
        "task": "app.tasks.sms_tasks.poll_inbox",
        "schedule": timedelta(seconds=45),
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
