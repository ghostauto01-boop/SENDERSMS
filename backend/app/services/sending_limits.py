"""Sending limits and pacing.

The Settings -> "Sending Rules" page stored hourly / daily / per-minute limits
but nothing ever enforced them, so a campaign could blast the whole list the
moment it started and get the SIM flagged by the carrier.

This module makes those rules real, and adds the pacing the user asked for:
instead of dumping N messages the instant the window opens, sends are spaced
evenly across the window (e.g. 100/hour -> one message every ~36 seconds).

It answers a single question before every outbound SMS:

    SendingGate(db).check()  -> (allowed, wait_seconds, reason)

Counters are read straight from the ``messages`` table (outgoing rows marked
sent/delivered), so every path -- campaigns, follow-ups, direct sends, auto
replies -- is counted together, and the numbers survive restarts and multiple
workers without extra state.

Time windows use the deployment timezone (DEFAULT_TIMEZONE) so "hourly" means
a real clock hour for the operator, not a UTC rolling window.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.conversation import Message
from app.models.system import SystemSetting

logger = logging.getLogger(__name__)

CATEGORY = "sending_rules"

# Extra pacing rules appended to the existing sending_rules category.
ENABLE_PACING = "enable_pacing"
MIN_DELAY_SECONDS = "min_delay_seconds"

# Which message statuses count as "a real SMS left this device".
COUNTED_STATUSES = ("sent", "delivered")

DEFAULTS = {
    "enable_hourly_limit": False,
    "hourly_maximum": 100,
    "enable_daily_limit": False,
    "daily_maximum": 1000,
    "enable_per_minute_limit": False,
    "messages_per_minute": 10,
    "sending_start_time": "08:00",
    "sending_end_time": "20:00",
    "allow_weekends": True,
    "allow_holidays": True,
    ENABLE_PACING: True,
    MIN_DELAY_SECONDS: 30,
}


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.DEFAULT_TIMEZONE or "Africa/Lagos")
    except Exception:
        return ZoneInfo("UTC")


def _parse_time(value: Optional[str]) -> Optional[time]:
    if not value:
        return None
    try:
        return time.fromisoformat(str(value).strip()[:5])
    except ValueError:
        return None


def _parse_int(value, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _parse_bool(value, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


async def get_sending_rules(db: AsyncSession) -> dict:
    """Return the effective sending rules, with defaults for unset keys."""
    rows = (
        await db.execute(
            select(SystemSetting).where(SystemSetting.category == CATEGORY)
        )
    ).scalars().all()
    raw = {r.key: r.value for r in rows}
    rules: dict = dict(DEFAULTS)
    rules["enable_hourly_limit"] = _parse_bool(raw.get("enable_hourly_limit"), DEFAULTS["enable_hourly_limit"])
    rules["hourly_maximum"] = _parse_int(raw.get("hourly_maximum"), DEFAULTS["hourly_maximum"])
    rules["enable_daily_limit"] = _parse_bool(raw.get("enable_daily_limit"), DEFAULTS["enable_daily_limit"])
    rules["daily_maximum"] = _parse_int(raw.get("daily_maximum"), DEFAULTS["daily_maximum"])
    rules["enable_per_minute_limit"] = _parse_bool(raw.get("enable_per_minute_limit"), DEFAULTS["enable_per_minute_limit"])
    rules["messages_per_minute"] = _parse_int(raw.get("messages_per_minute"), DEFAULTS["messages_per_minute"])
    rules["sending_start_time"] = raw.get("sending_start_time") or DEFAULTS["sending_start_time"]
    rules["sending_end_time"] = raw.get("sending_end_time") or DEFAULTS["sending_end_time"]
    rules["allow_weekends"] = _parse_bool(raw.get("allow_weekends"), DEFAULTS["allow_weekends"])
    rules["allow_holidays"] = _parse_bool(raw.get("allow_holidays"), DEFAULTS["allow_holidays"])
    rules[ENABLE_PACING] = _parse_bool(raw.get(ENABLE_PACING), DEFAULTS[ENABLE_PACING])
    rules[MIN_DELAY_SECONDS] = _parse_int(raw.get(MIN_DELAY_SECONDS), DEFAULTS[MIN_DELAY_SECONDS])
    # Guard against a zero/negative maximum silently becoming "send nothing".
    for key in ("hourly_maximum", "daily_maximum", "messages_per_minute"):
        if rules[key] <= 0:
            rules[key] = DEFAULTS[key]
    return rules


async def _count_since(db: AsyncSession, since_utc: datetime, until_utc: datetime) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Message)
        .where(
            Message.direction == "outgoing",
            Message.status.in_(COUNTED_STATUSES),
            Message.sent_at.is_not(None),
            Message.sent_at >= since_utc,
            Message.sent_at < until_utc,
        )
    )
    return result.scalar() or 0


async def _last_sent_at(db: AsyncSession) -> Optional[datetime]:
    result = await db.execute(
        select(func.max(Message.sent_at)).where(
            Message.direction == "outgoing",
            Message.status.in_(COUNTED_STATUSES),
        )
    )
    value = result.scalar()
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class SendingGate:
    """Answers "may I send now, or must I wait?" before an outbound SMS."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def check(self, now: Optional[datetime] = None) -> dict:
        """Return {allowed, wait_seconds, reason, next_window}."""
        rules = await get_sending_rules(self.db)
        now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        now_local = now_utc.astimezone(_tz())

        # 1. Sending window (start/end time + weekends).
        window = self._window_state(rules, now_local)
        if not window["allowed"]:
            return window

        # 2. Per-minute cap.
        minute_start = now_local.replace(second=0, microsecond=0)
        next_minute = minute_start + timedelta(minutes=1)
        if rules["enable_per_minute_limit"]:
            sent = await _count_since(
                self.db,
                minute_start.astimezone(timezone.utc),
                next_minute.astimezone(timezone.utc),
            )
            if sent >= rules["messages_per_minute"]:
                return self._wait_until(next_minute, f"per-minute limit ({rules['messages_per_minute']}) reached")

        # 3. Hourly cap.
        hour_start = now_local.replace(minute=0, second=0, microsecond=0)
        next_hour = hour_start + timedelta(hours=1)
        if rules["enable_hourly_limit"]:
            sent = await _count_since(
                self.db,
                hour_start.astimezone(timezone.utc),
                next_hour.astimezone(timezone.utc),
            )
            if sent >= rules["hourly_maximum"]:
                return self._wait_until(next_hour, f"hourly limit ({rules['hourly_maximum']}) reached")

        # 4. Daily cap.
        day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        next_day = day_start + timedelta(days=1)
        if rules["enable_daily_limit"]:
            sent = await _count_since(
                self.db,
                day_start.astimezone(timezone.utc),
                next_day.astimezone(timezone.utc),
            )
            if sent >= rules["daily_maximum"]:
                return self._wait_until(next_day, f"daily limit ({rules['daily_maximum']}) reached")

        # 5. Pacing: minimum spacing between consecutive messages.
        if rules[ENABLE_PACING]:
            interval = self.effective_interval(rules)
            if interval > 0:
                last = await _last_sent_at(self.db)
                if last is not None:
                    elapsed = (now_utc - last).total_seconds()
                    if elapsed < interval:
                        wait = int(interval - elapsed) + 1
                        return {
                            "allowed": False,
                            "wait_seconds": wait,
                            "reason": f"pacing: at least {interval}s between messages",
                            "next_window": None,
                        }

        return {"allowed": True, "wait_seconds": 0, "reason": None, "next_window": None}

    def effective_interval(self, rules: dict) -> int:
        """Seconds to leave between consecutive messages.

        With an hourly limit, spacing = 3600 / hourly_maximum guarantees the
        whole allowance is used evenly across the hour. The operator's
        ``min_delay_seconds`` acts as a floor (so 1/hour still gets some room,
        but 10/hour -> ~360s is respected unless they want a tighter minimum).
        """
        floor = max(0, int(rules.get(MIN_DELAY_SECONDS, DEFAULTS[MIN_DELAY_SECONDS])))
        if rules["enable_hourly_limit"]:
            per_hour = int(rules["hourly_maximum"])
            if per_hour > 0:
                return max(floor, int(3600 // per_hour))
        return floor

    def _wait_until(self, local_dt: datetime, reason: str) -> dict:
        utc_dt = local_dt.astimezone(timezone.utc)
        wait = max(1, int((utc_dt - datetime.now(timezone.utc)).total_seconds()) + 1)
        return {
            "allowed": False,
            "wait_seconds": wait,
            "reason": reason,
            "next_window": utc_dt.isoformat(),
        }

    def _window_state(self, rules: dict, now_local: datetime) -> dict:
        """Apply start/end time and the weekend toggle."""
        start = _parse_time(rules.get("sending_start_time"))
        end = _parse_time(rules.get("sending_end_time"))

        weekday = now_local.weekday()
        is_weekend = weekday >= 5

        # Weekend toggle
        if is_weekend and not rules["allow_weekends"]:
            next_start = self._next_window_start(now_local, start)
            return self._wait_until(next_start, "sending paused on weekends")

        # Time-of-day window
        if start and end:
            t = now_local.time().replace(microsecond=0)
            if start < end:
                if not (start <= t < end):
                    next_start = self._next_window_start(now_local, start)
                    return self._wait_until(next_start, "outside sending hours")
            else:
                # Window wraps midnight, e.g. 20:00 -> 08:00.
                if not (t >= start or t < end):
                    next_start = self._next_window_start(now_local, start)
                    return self._wait_until(next_start, "outside sending hours")

        return {"allowed": True, "wait_seconds": 0, "reason": None, "next_window": None}

    @staticmethod
    def _next_window_start(now_local: datetime, start: Optional[time]) -> datetime:
        candidate = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        if start is not None:
            candidate = candidate.replace(hour=start.hour, minute=start.minute)
        if candidate <= now_local:
            candidate += timedelta(days=1)
        return candidate

    async def status(self) -> dict:
        rules = await get_sending_rules(self.db)
        now_local = datetime.now(timezone.utc).astimezone(_tz())
        hour_start = now_local.replace(minute=0, second=0, microsecond=0)
        minute_start = now_local.replace(second=0, microsecond=0)
        day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

        async def count(since, until):
            return await _count_since(self.db, since.astimezone(timezone.utc), until.astimezone(timezone.utc))

        last = await _last_sent_at(self.db)
        check = await self.check()
        return {
            "rules": rules,
            "timezone": settings.DEFAULT_TIMEZONE,
            "counters": {
                "minute": await count(minute_start, minute_start + timedelta(minutes=1)),
                "hour": await count(hour_start, hour_start + timedelta(hours=1)),
                "day": await count(day_start, day_start + timedelta(days=1)),
            },
            "interval_seconds": self.effective_interval(rules),
            "last_sent_at": last.isoformat() if last else None,
            "allowed_now": check["allowed"],
            "wait_seconds": check["wait_seconds"],
            "reason": check["reason"],
            "next_window": check["next_window"],
        }


async def await_slot(db: AsyncSession, cap_seconds: int = 8) -> dict:
    """Wait (bounded) for the next sending slot, then return the final check.

    Used by interactive sends (direct send / retry) where blocking the request
    for a short while is friendlier than an immediate error.
    """
    import asyncio

    gate = SendingGate(db)
    check = await gate.check()
    if check["allowed"]:
        return check
    waited = 0
    while waited < cap_seconds:
        step = min(5, int(check["wait_seconds"] or 5), cap_seconds - waited)
        if step <= 0:
            break
        await asyncio.sleep(step)
        waited += step
        check = await gate.check()
        if check["allowed"]:
            return check
    return check
