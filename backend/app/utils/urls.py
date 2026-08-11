"""Helpers for building absolute URLs that point back at this deployment."""

from typing import Optional

from app.config import settings

WEBHOOK_PATH = "/api/v1/webhooks/smsgateway"


def public_base_url() -> Optional[str]:
    """The externally reachable base URL of this deployment, if configured.

    Falls back to Render's injected RENDER_EXTERNAL_URL so existing Render
    deployments keep working without extra configuration.
    """
    base = settings.PUBLIC_BASE_URL
    if not base:
        import os

        base = os.environ.get("RENDER_EXTERNAL_URL")
    if not base:
        return None
    return base.rstrip("/")


def webhook_url() -> Optional[str]:
    """Absolute URL the SMS gateway should POST events to."""
    base = public_base_url()
    return f"{base}{WEBHOOK_PATH}" if base else None
