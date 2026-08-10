"""Pushover — needs user_key + app_token. Default token provided."""
import logging, httpx
from typing import Optional
from app.providers.base import NotificationProvider
from app.config import settings

logger = logging.getLogger(__name__)
# Default Pushover application token (must be set per-user at https://pushover.net/apps/build)
# User creates their own app and puts the token here, or sets PUSHOVER_APP_TOKEN env var
DEFAULT_TOKEN = settings.PUSHOVER_APP_TOKEN or ""

class PushoverProvider(NotificationProvider):
    BASE = "https://api.pushover.net/1"

    def __init__(self, app_token=None, user_key=None):
        self.app_token = app_token or DEFAULT_TOKEN
        self.user_key = user_key or settings.PUSHOVER_USER_KEY

    def is_configured(self):
        return bool(self.user_key and self.user_key.strip()) and bool(self.app_token and self.app_token.strip())

    async def send_notification(self, title, body, **kwargs):
        if not self.is_configured():
            logger.warning("Pushover: not configured (need user_key + app_token)")
            return False
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(f"{self.BASE}/messages.json", data={"token": self.app_token, "user": self.user_key.strip(), "title": title, "message": body})
                d = r.json()
                ok = r.status_code == 200 and d.get("status") == 1
                if ok: logger.info("Pushover sent")
                else: logger.error(f"Pushover: {d}")
                return ok
        except Exception as e:
            logger.error(f"Pushover error: {e}")
            return False

    async def test_notification(self):
        return await self.send_notification("SMS SENDER", "Test notification — Pushover is working!")
