"""One definition of "which channels exist", and the one way to reject a typo.

A ``channel`` filter that silently matches nothing makes a typo look like "no conversations".
Endpoints validate it with :func:`validate_channel` instead and answer 422.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

CHANNELS = ("sms", "email")


def campaign_manager_location(channel: str | None, campaign_id: int) -> dict[str, str | int]:
    """Where a classic campaign is edited in the operator UI.

    ``draft`` is a lifecycle state, not a destination. MCP and in-app agents
    return this location so they never send people to the unified Drafts tab
    when the requested home is the SMS or Email Manager.
    """
    normalized = "email" if str(channel or "").lower() == "email" else "sms"
    manager = "Email Manager" if normalized == "email" else "SMS Manager"
    return {
        "manager": manager,
        "manager_section": "Bulk Sends",
        "manager_url": f"/{normalized}-manager?section=Bulk%20Sends&campaign={int(campaign_id)}",
        "location": f"{manager} → Bulk Sends",
    }


def validate_channel(raw: Optional[str], *, default: Optional[str] = None, allow_all: bool = True) -> Optional[str]:
    """The normalised channel (``sms`` / ``email`` / ``all``), or ``default`` when none was given.

    Anything else is a 422 that names the value the caller sent.
    """
    value = (raw or "").strip().lower()
    if not value:
        return default
    allowed = CHANNELS + (("all",) if allow_all else ())
    if value not in allowed:
        listed = ", ".join(f"'{c}'" for c in allowed[:-1]) + f" or '{allowed[-1]}'"
        raise HTTPException(422, f"channel must be {listed} — got '{raw}'.")
    return value
