"""
Celery tasks for push notifications.
Notifications are always non-blocking and never affect SMS operations.
"""

import asyncio
import logging

from sqlalchemy import select

from app.tasks.celery_app import celery_app
from app.database import async_session_factory
from app.models.notification import NotificationProvider, NotificationEvent
from app.providers.onesignal import OneSignalProvider
from app.providers.pushover import PushoverProvider
from app.security.encryption import decrypt_value

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def send_push_notification(self, event_id: int):
    """
    Send a push notification via configured providers.
    Critically: if notification sending fails, SMS operations are unaffected.
    """
    async def _send():
        async with async_session_factory() as db:
            result = await db.execute(
                select(NotificationEvent).where(NotificationEvent.id == event_id)
            )
            event = result.scalar_one_or_none()
            if not event:
                return

            provider_name = event.provider

            # Get provider config
            prov_result = await db.execute(
                select(NotificationProvider).where(
                    NotificationProvider.provider == provider_name,
                    NotificationProvider.is_enabled == True,
                )
            )
            provider_config = prov_result.scalar_one_or_none()
            if not provider_config:
                event.status = "skipped"
                event.error = "Provider not enabled"
                await db.commit()
                return

            try:
                success = False
                if provider_name == "onesignal":
                    import json
                    config = json.loads(provider_config.config_json or "{}")
                    provider = OneSignalProvider(
                        app_id=config.get("app_id"),
                        rest_api_key=decrypt_value(config.get("rest_api_key_encrypted", "")),
                    )
                    if provider.is_configured():
                        success = await provider.send_notification(
                            title=event.title or "SendSMS",
                            body=event.body or "",
                        )

                elif provider_name == "pushover":
                    import json
                    config = json.loads(provider_config.config_json or "{}")
                    provider = PushoverProvider(
                        app_token=config.get("app_token"),
                        user_key=decrypt_value(config.get("user_key_encrypted", "")),
                    )
                    if provider.is_configured():
                        success = await provider.send_notification(
                            title=event.title or "SendSMS",
                            body=event.body or "",
                        )

                event.status = "sent" if success else "failed"
                if not success:
                    event.error = "Provider returned failure"

            except Exception as e:
                logger.error(f"Notification error ({provider_name}): {e}")
                event.status = "failed"
                event.error = str(e)
                # Do not retry - notification failures must not block anything

            await db.commit()

    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(_send())
    except Exception as exc:
        logger.error(f"Notification task error: {exc}")
        # Never retry notification failures - they're non-critical


@celery_app.task
def process_pending_notifications():
    """Process all pending notification events."""
    async def _process():
        async with async_session_factory() as db:
            result = await db.execute(
                select(NotificationEvent).where(NotificationEvent.status == "pending").limit(50)
            )
            events = result.scalars().all()
            from app.tasks.queue import try_enqueue
            for event in events:
                try_enqueue(send_push_notification, event.id)
            await db.commit()

    loop = asyncio.get_event_loop()
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(_process())
