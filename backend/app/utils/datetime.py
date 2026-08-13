"""Timezone helpers shared by dashboard and scheduling APIs."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings


def local_day_utc_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return today's local midnight bounds converted to UTC.

    Follow-up views are user-facing calendar views. Using UTC midnight made an
    item due at 00:30 Lagos time appear under yesterday until 01:00.
    """
    try:
        local_zone = ZoneInfo(settings.DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError:
        local_zone = timezone.utc
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_now = current.astimezone(local_zone)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)
