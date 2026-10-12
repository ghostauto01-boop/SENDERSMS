"""Backward-compatible bridge to campaign-scoped pacing.

Application-wide sending limits were retired: direct sends are unrestricted by
application rules, and optional caps/windows/delays are enforced by
:mod:`app.services.campaign_sending` for the campaign that owns a message.

This module keeps the old ``SendingGate`` import as a permissive shim for older
integrations, while exposing the bounce-watch thresholds consumed by
:mod:`app.services.bounce_watch`. It must never reintroduce global rate limits,
and none of these settings may ever stop a campaign.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system import SystemSetting

CATEGORY = "sending_rules"
BOUNCE_ALERT_ENABLED = "bounce_alert_enabled"
BOUNCE_ALERT_BOUNCE_PCT = "bounce_alert_bounce_pct"
BOUNCE_ALERT_COMPLAINT_PCT = "bounce_alert_complaint_pct"
BOUNCE_ALERT_MIN_SAMPLE = "bounce_alert_min_sample"
BOUNCE_ALERT_WINDOW_HOURS = "bounce_alert_window_hours"
BOUNCE_ALERT_COOLDOWN_HOURS = "bounce_alert_cooldown_hours"

# Only bounce-watch thresholds remain in this legacy category. Sending caps and
# windows are never loaded from SystemSetting or applied globally.
DEFAULTS = {
    BOUNCE_ALERT_ENABLED: True,
    BOUNCE_ALERT_BOUNCE_PCT: 2.0,
    BOUNCE_ALERT_COMPLAINT_PCT: 0.10,
    BOUNCE_ALERT_MIN_SAMPLE: 20,
    BOUNCE_ALERT_WINDOW_HOURS: 168,
    BOUNCE_ALERT_COOLDOWN_HOURS: 6,
}

#: The keys the retired bounce *circuit breaker* used. A value already saved
#: under one of them is honoured, so an operator who tuned the old thresholds
#: keeps them after the upgrade instead of silently falling back to defaults.
LEGACY_KEYS = {
    BOUNCE_ALERT_ENABLED: "breaker_enabled",
    BOUNCE_ALERT_BOUNCE_PCT: "breaker_bounce_pct",
    BOUNCE_ALERT_COMPLAINT_PCT: "breaker_complaint_pct",
    BOUNCE_ALERT_MIN_SAMPLE: "breaker_min_sample",
    BOUNCE_ALERT_WINDOW_HOURS: "breaker_window_hours",
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
    """Return only the bounce-watch configuration.

    The historical function name stays for compatibility with older callers, but
    global daily/hourly/minute limits, windows and pacing are intentionally not
    returned or enforced.
    """
    keys = tuple(DEFAULTS) + tuple(LEGACY_KEYS.values())
    rows = (
        await db.execute(
            select(SystemSetting).where(
                SystemSetting.category == CATEGORY,
                SystemSetting.key.in_(keys),
            )
        )
    ).scalars().all()
    raw = {row.key: row.value for row in rows}

    def _setting(key: str) -> str | None:
        """The current value, or the one the old circuit breaker saved."""
        if key in raw:
            return raw[key]
        return raw.get(LEGACY_KEYS.get(key, ""))

    return {
        BOUNCE_ALERT_ENABLED: _bool(_setting(BOUNCE_ALERT_ENABLED), DEFAULTS[BOUNCE_ALERT_ENABLED]),
        BOUNCE_ALERT_BOUNCE_PCT: max(
            0.01, _float(_setting(BOUNCE_ALERT_BOUNCE_PCT), DEFAULTS[BOUNCE_ALERT_BOUNCE_PCT])
        ),
        BOUNCE_ALERT_COMPLAINT_PCT: max(
            0.001,
            _float(_setting(BOUNCE_ALERT_COMPLAINT_PCT), DEFAULTS[BOUNCE_ALERT_COMPLAINT_PCT]),
        ),
        BOUNCE_ALERT_MIN_SAMPLE: _int(
            _setting(BOUNCE_ALERT_MIN_SAMPLE), DEFAULTS[BOUNCE_ALERT_MIN_SAMPLE]
        ),
        BOUNCE_ALERT_WINDOW_HOURS: _int(
            _setting(BOUNCE_ALERT_WINDOW_HOURS), DEFAULTS[BOUNCE_ALERT_WINDOW_HOURS]
        ),
        BOUNCE_ALERT_COOLDOWN_HOURS: _int(
            _setting(BOUNCE_ALERT_COOLDOWN_HOURS), DEFAULTS[BOUNCE_ALERT_COOLDOWN_HOURS]
        ),
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
