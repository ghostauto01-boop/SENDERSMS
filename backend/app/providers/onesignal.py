"""
OneSignal push notification provider.

OPTIONAL: Only used when configured and enabled.
"""

import logging
from typing import Optional

import httpx

from app.providers.base import NotificationProvider
from app.config import settings

logger = logging.getLogger(__name__)


class OneSignalProvider(NotificationProvider):
    """OneSignal push notification provider."""

    BASE_URL = "https://onesignal.com/api/v1"

    def __init__(
        self,
        app_id: Optional[str] = None,
        rest_api_key: Optional[str] = None,
    ):
        self.app_id = app_id or settings.ONESIGNAL_APP_ID
        self.rest_api_key = rest_api_key or settings.ONESIGNAL_REST_API_KEY

    def is_configured(self) -> bool:
        """Check if OneSignal is properly configured."""
        return bool(self.app_id and self.rest_api_key)

    async def send_notification(self, title: str, body: str, **kwargs) -> bool:
        """
        Send a push notification via OneSignal.

        OneSignal API: POST /notifications
        """
        if not self.is_configured():
            logger.warning("OneSignal not configured, skipping notification")
            return False

        payload = {
            "app_id": self.app_id,
            "headings": {"en": title},
            "contents": {"en": body},
            "included_segments": ["All"],
        }

        # Optional data
        if kwargs.get("data"):
            payload["data"] = kwargs["data"]
        if kwargs.get("url"):
            payload["url"] = kwargs["url"]

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self.BASE_URL}/notifications",
                    json=payload,
                    headers={
                        "Authorization": f"Basic {self.rest_api_key}",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code == 200:
                    logger.info("OneSignal notification sent successfully")
                    return True
                else:
                    logger.error(f"OneSignal error: {resp.status_code} {resp.text}")
                    return False
        except Exception as e:
            logger.error(f"OneSignal send error: {e}")
            return False

    async def test_notification(self) -> bool:
        """Send a test notification."""
        return await self.send_notification(
            title="SendSMS Test",
            body="This is a test notification from SendSMS. If you see this, OneSignal is working!",
        )
