"""Campaign-scoped sending controls.

There is deliberately no application-wide sending gate. A campaign can choose
its own daily/hourly/minute caps, delay, and local send window; direct sends are
not affected by campaign settings.
"""

from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.conversation import Message


_SUCCESS = ("sent", "delivered")
_IN_FLIGHT = ("queued", "sending", "retrying")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class CampaignSendingGate:
    """Evaluate optional campaign rules for classic Campaign messages."""

    def __init__(self, db: AsyncSession, campaign):
        self.db = db
        self.campaign = campaign
        try:
            self.zone = ZoneInfo(getattr(campaign, "timezone_name", None) or settings.DEFAULT_TIMEZONE)
        except Exception:  # noqa: BLE001 — a bad saved zone should not strand a campaign
            self.zone = ZoneInfo("UTC")

    def _window_state(self, now: datetime) -> tuple[bool, datetime | None]:
        local = now.astimezone(self.zone)
        if not bool(getattr(self.campaign, "allow_weekends", True)) and local.weekday() >= 5:
            return False, self._next_window(now)

        start = getattr(self.campaign, "send_start_hour", None)
        end = getattr(self.campaign, "send_end_hour", None)
        if start is None or end is None or int(start) == int(end):
            return True, None
        start, end = int(start), int(end)
        hour = local.hour + local.minute / 60 + local.second / 3600
        inside = (start <= hour < end) if start < end else (hour >= start or hour < end)
        return (True, None) if inside else (False, self._next_window(now))

    def _next_window(self, now: datetime) -> datetime:
        local = now.astimezone(self.zone)
        start = getattr(self.campaign, "send_start_hour", None)
        end = getattr(self.campaign, "send_end_hour", None)
        has_window = start is not None and end is not None and int(start) != int(end)

        for offset in range(0, 9):
            day = local.date() + timedelta(days=offset)
            if not bool(getattr(self.campaign, "allow_weekends", True)) and day.weekday() >= 5:
                continue
            hour = int(start) if has_window else 0
            candidate = datetime.combine(day, time(hour=hour), tzinfo=self.zone)
            if candidate > local:
                return candidate.astimezone(timezone.utc)
        return (now + timedelta(days=8)).astimezone(timezone.utc)

    async def _sent_count(self, since: datetime | None = None) -> int:
        query = select(func.count()).select_from(Message).where(
            Message.campaign_id == self.campaign.id,
            Message.direction == "outgoing",
            Message.status.in_(_SUCCESS),
            Message.sent_at.is_not(None),
        )
        if since is not None:
            query = query.where(Message.sent_at >= since)
        return int((await self.db.execute(query)).scalar() or 0)

    async def _in_flight_count(self, exclude_message_id: int | None = None) -> int:
        query = select(func.count()).select_from(Message).where(
            Message.campaign_id == self.campaign.id,
            Message.direction == "outgoing",
            Message.status.in_(_IN_FLIGHT),
        )
        if exclude_message_id is not None:
            query = query.where(Message.id != exclude_message_id)
        return int((await self.db.execute(query)).scalar() or 0)

    async def _last_sent_at(self) -> datetime | None:
        value = (await self.db.execute(
            select(Message.sent_at)
            .where(
                Message.campaign_id == self.campaign.id,
                Message.direction == "outgoing",
                Message.status.in_(_SUCCESS),
                Message.sent_at.is_not(None),
            )
            .order_by(Message.sent_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        return _aware(value)

    def _delay_for(self, last_sent: datetime) -> int:
        minimum = max(int(getattr(self.campaign, "min_delay", None) or 0), 0)
        maximum = max(int(getattr(self.campaign, "max_delay", None) or minimum), minimum)
        if maximum <= minimum:
            return minimum
        # Keep the chosen delay stable across checks/retries for a given send slot.
        seed = hashlib.sha256(f"{self.campaign.id}:{last_sent.isoformat()}".encode()).digest()
        return random.Random(int.from_bytes(seed[:8], "big")).randint(minimum, maximum)

    async def check(self, *, exclude_message_id: int | None = None) -> dict:
        """Return whether one message may send now and the next retry delay."""
        now = _utcnow()
        allowed, next_window = self._window_state(now)
        if not allowed:
            return {
                "allowed": False,
                "reason": "The campaign's sending window is closed.",
                "wait_seconds": max(5, int(((next_window or now + timedelta(minutes=1)) - now).total_seconds())),
                "next_window": next_window.isoformat() if next_window else None,
            }

        in_flight = await self._in_flight_count(exclude_message_id)
        caps = (
            ("daily_limit", timedelta(days=1), "daily"),
            ("hourly_limit", timedelta(hours=1), "hourly"),
            ("per_minute_limit", timedelta(minutes=1), "per-minute"),
        )
        for field, period, label in caps:
            cap = getattr(self.campaign, field, None)
            if cap is None or int(cap) <= 0:
                continue
            sent = await self._sent_count(now - period)
            if sent + in_flight >= int(cap):
                return {
                    "allowed": False,
                    "reason": f"The campaign {label} limit has been reached.",
                    "wait_seconds": max(5, int(period.total_seconds())),
                    "next_window": None,
                }

        minimum = max(int(getattr(self.campaign, "min_delay", None) or 0), 0)
        maximum = max(int(getattr(self.campaign, "max_delay", None) or minimum), minimum)
        if minimum or maximum:
            if in_flight:
                return {
                    "allowed": False,
                    "reason": "The campaign is waiting for its configured delay between sends.",
                    "wait_seconds": max(minimum, 5),
                    "next_window": None,
                }
            last_sent = await self._last_sent_at()
            if last_sent is not None:
                wait_until = last_sent + timedelta(seconds=self._delay_for(last_sent))
                if wait_until > now:
                    return {
                        "allowed": False,
                        "reason": "The campaign is waiting for its configured delay between sends.",
                        "wait_seconds": max(5, int((wait_until - now).total_seconds())),
                        "next_window": None,
                    }

        return {"allowed": True, "reason": None, "wait_seconds": 0, "next_window": None}

    async def room(self) -> int | None:
        """How many messages can be released now, or None when uncapped."""
        now = _utcnow()
        allowed, _ = self._window_state(now)
        if not allowed:
            return 0

        in_flight = await self._in_flight_count()
        rooms: list[int] = []
        for field, period in (
            ("daily_limit", timedelta(days=1)),
            ("hourly_limit", timedelta(hours=1)),
            ("per_minute_limit", timedelta(minutes=1)),
        ):
            cap = getattr(self.campaign, field, None)
            if cap is None or int(cap) <= 0:
                continue
            rooms.append(max(0, int(cap) - await self._sent_count(now - period) - in_flight))

        minimum = max(int(getattr(self.campaign, "min_delay", None) or 0), 0)
        maximum = max(int(getattr(self.campaign, "max_delay", None) or minimum), minimum)
        if minimum or maximum:
            if in_flight:
                rooms.append(0)
            else:
                last_sent = await self._last_sent_at()
                if last_sent is not None:
                    wait_until = last_sent + timedelta(seconds=self._delay_for(last_sent))
                    if wait_until > now:
                        rooms.append(0)
                    else:
                        rooms.append(1)
                else:
                    rooms.append(1)

        return min(rooms) if rooms else None


def project_campaign(campaign, sendable: int, *, channel: str) -> dict:
    """Small campaign-only projection for validation and audience summaries."""
    daily_limit = getattr(campaign, "daily_limit", None)
    active_days = 7 if bool(getattr(campaign, "allow_weekends", True)) else 5
    warnings: list[str] = []
    if daily_limit and int(daily_limit) > 0:
        days_needed = (max(sendable, 0) + int(daily_limit) - 1) // int(daily_limit)
        estimate_days = (days_needed * 7 + active_days - 1) // active_days
    else:
        estimate_days = 0
    if getattr(campaign, "end_date", None) and estimate_days:
        end = campaign.end_date
        if _aware(end) and _aware(end) < _utcnow() + timedelta(days=estimate_days):
            warnings.append("The campaign's own end date may arrive before its daily limit finishes sending.")
    return {
        "campaign_only": True,
        "channel": channel,
        "sendable_contacts": max(sendable, 0),
        "daily_limit": daily_limit,
        "estimated_calendar_days": estimate_days,
        "warnings": warnings,
    }
