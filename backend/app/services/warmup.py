"""Informational warm-up guidance for newly authenticated sending domains.

The displayed ramp starts around 5/day and reaches 25/day by week four. It is
advisory only; this module does not gate delivery. Any app-side sending limits
are selected per campaign, while provider-imposed quotas still apply.

The clock starts when the domain first becomes authenticated
(``SendingDomain.warmup_started_at``)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.email_domains import domain_of

#: (days elapsed is below N) -> cap. Whole days since the clock started.
RAMP = ((7, 5), (14, 10), (21, 18))
FINAL_CAP = 25


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def warmup_cap(started_at: Optional[datetime], now: Optional[datetime] = None) -> Optional[int]:
    """The daily cap for one mailbox today, or None when no ramp applies."""
    if started_at is None:
        return None
    days = max((_aware(now or datetime.now(timezone.utc)) - _aware(started_at)).days, 0)
    for below, cap in RAMP:
        if days < below:
            return cap
    return FINAL_CAP


def warmup_state(started_at: Optional[datetime], now: Optional[datetime] = None) -> Optional[dict]:
    """Where a domain is in its ramp (None when it has no clock)."""
    if started_at is None:
        return None
    started = _aware(started_at)
    moment = _aware(now or datetime.now(timezone.utc))
    days = max((moment - started).days, 0)
    cap = warmup_cap(started, moment)
    final_day: date = (started + timedelta(days=RAMP[-1][0])).date()
    return {
        "started_at": started.isoformat(),
        "day": days + 1,
        "week": days // 7 + 1,
        "cap": cap,
        "complete": cap == FINAL_CAP,
        "full_cap_from": final_day.isoformat(),
    }


async def warmup_caps(db: AsyncSession, accounts: list) -> dict[int, Optional[int]]:
    """``{account id: cap or None}`` for the mailboxes whose domain has a warm-up clock."""
    from app.models.sending_domain import SendingDomain

    by_domain = {a.id: domain_of(a.from_email) for a in accounts}
    wanted = {d for d in by_domain.values() if d}
    if not wanted:
        return {a.id: None for a in accounts}
    rows = (
        await db.execute(
            select(SendingDomain).where(
                SendingDomain.domain.in_(wanted), SendingDomain.warmup_started_at.isnot(None)
            )
        )
    ).scalars().all()
    clock = {row.domain: row.warmup_started_at for row in rows}
    return {a.id: warmup_cap(clock.get(by_domain[a.id])) for a in accounts}
