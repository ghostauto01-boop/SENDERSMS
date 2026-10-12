"""Backward-compatible bridge to campaign-scoped pacing.

Application-wide sending limits were retired: direct sends are unrestricted by
application rules, and optional caps/windows/delays are enforced by
:mod:`app.services.campaign_sending` for the campaign that owns a message.

This module keeps the old ``SendingGate`` import as a permissive shim for older
integrations, while exposing the email circuit-breaker settings consumed by
``circuit_breaker``. It must never reintroduce global rate limits.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system import SystemSetting

CATEGORY = "sending_rules"
BREAKER_ENABLED = "breaker_enabled"
BREAKER_BOUNCE_PCT = "breaker_bounce_pct"
BREAKER_COMPLAINT_PCT = "breaker_complaint_pct"
BREAKER_MIN_SAMPLE = "breaker_min_sample"
BREAKER_WINDOW_HOURS = "breaker_window_hours"

# Only campaign-health safety settings remain in this legacy category. Sending
# caps and windows are never loaded from SystemSetting or applied globally.
DEFAULTS = {
    BREAKER_ENABLED: True,
    BREAKER_BOUNCE_PCT: 2.0,
    BREAKER_COMPLAINT_PCT: 0.10,
    BREAKER_MIN_SAMPLE: 20,
    BREAKER_WINDOW_HOURS: 168,
}


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _float(value: str | None, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _int(value: str | None, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(value)) if value is not None else default
    except (TypeError, ValueError):
        return default


async def get_sending_rules(db: AsyncSession) -> dict:
    """Return only the email circuit-breaker configuration.

    The historical function name stays for compatibility with the breaker, but
    global daily/hourly/minute limits, windows and pacing are intentionally not
    returned or enforced.
    """
    keys = tuple(DEFAULTS)
    rows = (
        await db.execute(
            select(SystemSetting).where(
                SystemSetting.category == CATEGORY,
                SystemSetting.key.in_(keys),
            )
        )
    ).scalars().all()
    raw = {row.key: row.value for row in rows}
    return {
        BREAKER_ENABLED: _bool(raw.get(BREAKER_ENABLED), DEFAULTS[BREAKER_ENABLED]),
        BREAKER_BOUNCE_PCT: max(
            0.01, _float(raw.get(BREAKER_BOUNCE_PCT), DEFAULTS[BREAKER_BOUNCE_PCT])
        ),
        BREAKER_COMPLAINT_PCT: max(
            0.001, _float(raw.get(BREAKER_COMPLAINT_PCT), DEFAULTS[BREAKER_COMPLAINT_PCT])
        ),
        BREAKER_MIN_SAMPLE: _int(raw.get(BREAKER_MIN_SAMPLE), DEFAULTS[BREAKER_MIN_SAMPLE]),
        BREAKER_WINDOW_HOURS: _int(raw.get(BREAKER_WINDOW_HOURS), DEFAULTS[BREAKER_WINDOW_HOURS]),
    }


class SendingGate:
    """Deprecated permissive shim; use ``CampaignSendingGate`` for campaigns."""

    def __init__(
        self,
        db: AsyncSession,
        channel: Optional[str] = None,
        account_id: Optional[int] = None,
    ):
        self.db = db
        self.channel = channel
        self.account_id = account_id

    async def check(self, now: Optional[datetime] = None) -> dict:
        """Legacy no-op: application-wide sending restrictions no longer exist."""
        return {"allowed": True, "wait_seconds": 0, "reason": None, "next_window": None}

    async def room(self, now: Optional[datetime] = None) -> None:
        """There is no global sending room to calculate."""
        return None

    def effective_interval(self, rules: dict | None = None) -> int:
        """Global pacing is disabled; campaign delay is handled elsewhere."""
        return 0

    async def status(self) -> dict:
        """Expose the retired global-gate state for legacy internal callers."""
        return {"campaign_only": True, "global_limits_disabled": True}


async def gate_for_campaign(db: AsyncSession, campaign):
    """Compatibility adapter returning the campaign-owned gate."""
    from app.services.campaign_sending import CampaignSendingGate

    return CampaignSendingGate(db, campaign)


async def await_slot(
    db: AsyncSession,
    cap_seconds: int = 8,
    channel: Optional[str] = None,
) -> dict:
    """Legacy interactive-send shim: direct sends are not globally throttled."""
    return {"allowed": True, "wait_seconds": 0, "reason": None, "next_window": None}


def project_send(
    *,
    channel: str,
    sendable: int,
    campaign_daily_limit: Optional[int] = None,
    **_ignored,
) -> dict:
    """Small legacy projection that reports only a campaign's explicitly set cap."""
    count = max(0, int(sendable or 0))
    cap = campaign_daily_limit if campaign_daily_limit and campaign_daily_limit > 0 else None
    days = (count + cap - 1) // cap if count and cap else (1 if count else 0)
    return {
        "campaign_only": True,
        "channel": channel,
        "sendable": count,
        "mailboxes": None,
        "daily_cap": cap,
        "daily_cap_source": f"campaign limit {cap}/day" if cap else "no daily cap",
        "pacing_seconds": 0,
        "effective_per_day": cap,
        "sending_days_needed": days,
        "calendar_days": days,
        "starts": None,
        "projected_finish": None,
        "window": "any time of day",
    }


def projection_warning(projection: dict) -> str | None:
    """The retired global projection no longer produces a sending warning."""
    return None
