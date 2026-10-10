"""Sending limits and pacing.

The Settings -> "Sending Rules" page stored hourly / daily / per-minute limits
but nothing ever enforced them, so a campaign could blast the whole list the
moment it started and get the SIM flagged by the carrier.

This module makes those rules real, and adds the pacing the user asked for:
instead of dumping N messages the instant the window opens, sends are spaced
evenly across the window (e.g. 100/hour -> one message every ~36 seconds).

It answers two questions about outbound traffic:

    SendingGate(db, channel).check()  -> (allowed, wait_seconds, reason)
    SendingGate(db, channel).room()   -> how many more may be released right now

``check`` is the per-message safety net; ``room`` is what a *producer* (a
campaign engine) uses to decide how many messages to create at all, so a
60-day audience is never queued in one go.

The rules are ON by default: a 09:00-17:00 window, weekends off, a daily cap.
For **email** the daily cap is not a single number: it is a per-mailbox default
(``email_daily_per_mailbox``) times the number of usable mailboxes, because a
mailbox -- like a SIM -- has a reputation that a burst destroys. An account's own
``daily_limit`` can only lower that default.

Counters are read straight from the ``messages`` table (outgoing rows marked
sent/delivered), per channel, so every path -- campaigns, follow-ups, direct
sends, auto replies -- is counted together, and the numbers survive restarts and
multiple workers without extra state. Messages still waiting to go out are
counted too when deciding how many *more* to release.

Time windows use the deployment timezone (DEFAULT_TIMEZONE) so "hourly" means
a real clock hour for the operator, not a UTC rolling window.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, time, timedelta, timezone
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

# Email-specific cap and the bounce circuit breaker (see services/circuit_breaker).
EMAIL_DAILY_PER_MAILBOX = "email_daily_per_mailbox"
BREAKER_ENABLED = "breaker_enabled"
BREAKER_BOUNCE_PCT = "breaker_bounce_pct"
BREAKER_COMPLAINT_PCT = "breaker_complaint_pct"
BREAKER_MIN_SAMPLE = "breaker_min_sample"
BREAKER_WINDOW_HOURS = "breaker_window_hours"

#: Marks that the one-time "turn the safe defaults on" step has been decided for
#: this database (applied, or deliberately skipped). Stored beside the rules.
SAFE_DEFAULTS_MARKER = "safe_defaults_applied"

# Which message statuses count as "a real message left this device".
COUNTED_STATUSES = ("sent", "delivered")
# Messages created but not yet gone: they will count against the day they leave.
IN_FLIGHT_STATUSES = ("queued", "sending", "retrying")

DEFAULTS = {
    "enable_hourly_limit": False,
    "hourly_maximum": 100,
    # The protective rules below are ON out of the box. They used to ship OFF
    # (the window because "outside sending hours" confused a fresh install),
    # which meant nothing stood between a new mailbox and its whole audience.
    # Every one can still be switched off in Settings -> Sending Rules; the
    # reason a send is held is always spelled out ("outside sending hours",
    # "sending paused on weekends", "daily limit (30) reached") with the time
    # sending resumes.
    "enable_daily_limit": True,
    "daily_maximum": 1000,
    "enable_per_minute_limit": False,
    "messages_per_minute": 10,
    "sending_start_time": "09:00",
    "sending_end_time": "17:00",
    "allow_weekends": False,
    "allow_holidays": True,
    ENABLE_PACING: True,
    MIN_DELAY_SECONDS: 30,
    # 20-50 a day per mailbox is the usual guidance for a mailbox with no
    # reputation yet; 30 sits in the middle. The deployment default comes from
    # EMAIL_DEFAULT_DAILY_LIMIT.
    EMAIL_DAILY_PER_MAILBOX: int(getattr(settings, "EMAIL_DEFAULT_DAILY_LIMIT", 30) or 0),
    BREAKER_ENABLED: True,
    # Pause a campaign when more than this share of its recent sends bounce or
    # are refused, or when spam complaints exceed the (much lower) second limit.
    BREAKER_BOUNCE_PCT: 2.0,
    BREAKER_COMPLAINT_PCT: 0.10,
    # Fewer sends than this is noise, not a rate (one bounce in 5 is "20%").
    BREAKER_MIN_SAMPLE: 20,
    BREAKER_WINDOW_HOURS: 168,
}

#: The values every throttle had before the safe defaults existed. A database
#: whose stored rules are exactly these has never been configured by a person.
_LEGACY_OPEN = {
    "enable_daily_limit": "false",
    "enable_hourly_limit": "false",
    "enable_per_minute_limit": "false",
    "sending_start_time": "",
    "sending_end_time": "",
    "allow_weekends": "true",
}


def _utcnow() -> datetime:
    """The clock the gate uses. A seam: tests pin it instead of racing the real one."""
    return datetime.now(timezone.utc)


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


def _parse_float(value, default: float) -> float:
    try:
        return float(str(value).strip())
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
    # Use the stored value verbatim (only falling back when the key is absent)
    # so that clearing the field in Settings really does remove the window.
    # `or DEFAULTS[...]` treated a deliberate blank as "unset" and reimposed
    # 08:00-20:00, making the window impossible to switch off.
    rules["sending_start_time"] = (
        raw["sending_start_time"] if "sending_start_time" in raw else DEFAULTS["sending_start_time"]
    ) or ""
    rules["sending_end_time"] = (
        raw["sending_end_time"] if "sending_end_time" in raw else DEFAULTS["sending_end_time"]
    ) or ""
    rules["allow_weekends"] = _parse_bool(raw.get("allow_weekends"), DEFAULTS["allow_weekends"])
    rules["allow_holidays"] = _parse_bool(raw.get("allow_holidays"), DEFAULTS["allow_holidays"])
    rules[ENABLE_PACING] = _parse_bool(raw.get(ENABLE_PACING), DEFAULTS[ENABLE_PACING])
    rules[MIN_DELAY_SECONDS] = _parse_int(raw.get(MIN_DELAY_SECONDS), DEFAULTS[MIN_DELAY_SECONDS])
    rules[EMAIL_DAILY_PER_MAILBOX] = max(
        0, _parse_int(raw.get(EMAIL_DAILY_PER_MAILBOX), DEFAULTS[EMAIL_DAILY_PER_MAILBOX])
    )
    rules[BREAKER_ENABLED] = _parse_bool(raw.get(BREAKER_ENABLED), DEFAULTS[BREAKER_ENABLED])
    rules[BREAKER_BOUNCE_PCT] = _parse_float(raw.get(BREAKER_BOUNCE_PCT), DEFAULTS[BREAKER_BOUNCE_PCT])
    rules[BREAKER_COMPLAINT_PCT] = _parse_float(
        raw.get(BREAKER_COMPLAINT_PCT), DEFAULTS[BREAKER_COMPLAINT_PCT]
    )
    rules[BREAKER_MIN_SAMPLE] = max(1, _parse_int(raw.get(BREAKER_MIN_SAMPLE), DEFAULTS[BREAKER_MIN_SAMPLE]))
    rules[BREAKER_WINDOW_HOURS] = max(
        1, _parse_int(raw.get(BREAKER_WINDOW_HOURS), DEFAULTS[BREAKER_WINDOW_HOURS])
    )
    # Guard against a zero/negative maximum silently becoming "send nothing".
    for key in ("hourly_maximum", "daily_maximum", "messages_per_minute"):
        if rules[key] <= 0:
            rules[key] = DEFAULTS[key]
    return rules


def default_mailbox_limit(rules: dict) -> Optional[int]:
    """Daily sends per mailbox when the account sets none itself, or None for no default.

    Tied to the *daily limit* switch: turning the daily limit off in Sending Rules
    removes this default too, so there is one switch for "daily caps", not two.
    """
    per_mailbox = int(rules.get(EMAIL_DAILY_PER_MAILBOX) or 0)
    if not rules.get("enable_daily_limit") or per_mailbox <= 0:
        return None
    return per_mailbox


def _channel_clause(channel: Optional[str]):
    """SQL filter for one channel. NULL channel means SMS (pre-email rows)."""
    if not channel:
        return None
    if channel == "sms":
        return func.coalesce(Message.channel, "sms") == "sms"
    return Message.channel == channel


def _scope(channel: Optional[str], account_id: Optional[int]) -> list:
    clauses = []
    clause = _channel_clause(channel)
    if clause is not None:
        clauses.append(clause)
    if account_id is not None:
        clauses.append(Message.email_account_id == account_id)
    return clauses


async def _count_since(
    db: AsyncSession,
    since_utc: datetime,
    until_utc: datetime,
    channel: Optional[str] = None,
    account_id: Optional[int] = None,
) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Message)
        .where(
            Message.direction == "outgoing",
            Message.status.in_(COUNTED_STATUSES),
            Message.sent_at.is_not(None),
            Message.sent_at >= since_utc,
            Message.sent_at < until_utc,
            *_scope(channel, account_id),
        )
    )
    return result.scalar() or 0


async def _in_flight(
    db: AsyncSession, channel: Optional[str] = None, account_id: Optional[int] = None
) -> int:
    """Outgoing messages created but not yet sent (they will use up today's allowance)."""
    result = await db.execute(
        select(func.count())
        .select_from(Message)
        .where(
            Message.direction == "outgoing",
            Message.status.in_(IN_FLIGHT_STATUSES),
            *_scope(channel, account_id),
        )
    )
    return result.scalar() or 0


async def _last_sent_at(
    db: AsyncSession, channel: Optional[str] = None, account_id: Optional[int] = None
) -> Optional[datetime]:
    result = await db.execute(
        select(func.max(Message.sent_at)).where(
            Message.direction == "outgoing",
            Message.status.in_(COUNTED_STATUSES),
            *_scope(channel, account_id),
        )
    )
    value = result.scalar()
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# Email capacity: a per-mailbox cap times the number of mailboxes
# --------------------------------------------------------------------------


def account_daily_cap(account, rules: dict) -> Optional[int]:
    """How many a single mailbox may send per day, or None for unlimited.

    The account's own ``daily_limit`` can only *lower* the protective default: a
    generous provider quota (Brevo's free plan allows 300 a day) says what the
    provider will accept, not what a brand-new mailbox can safely send.
    """
    explicit = int(account.daily_limit) if account.daily_limit else None
    default = default_mailbox_limit(rules)
    if default is None:
        return explicit
    return default if explicit is None else min(explicit, default)


async def _usable_accounts(db: AsyncSession, account_ids: Optional[list[int]] = None) -> list:
    """Active mailboxes that have what a send needs (key and From address)."""
    from app.models.email import EmailAccount

    query = select(EmailAccount).where(
        EmailAccount.is_active.is_(True),
        EmailAccount.api_key_encrypted.is_not(None),
        EmailAccount.api_key_encrypted != "",
        EmailAccount.from_email.is_not(None),
        EmailAccount.from_email != "",
    )
    if account_ids:
        query = query.where(EmailAccount.id.in_(account_ids))
    return list((await db.execute(query.order_by(EmailAccount.id))).scalars().all())


async def email_capacity(
    db: AsyncSession, rules: Optional[dict] = None, account_ids: Optional[list[int]] = None
) -> dict:
    """What the mailboxes can send per day: the mailbox count and the daily total.

    ``daily_cap`` is None when there is no cap (the daily limit is off and no
    account sets one, or no mailbox is configured at all).
    """
    rules = rules or await get_sending_rules(db)
    accounts = await _usable_accounts(db, account_ids)
    caps = [account_daily_cap(a, rules) for a in accounts]
    daily_cap = None if (not caps or any(c is None for c in caps)) else sum(caps)
    return {
        "mailboxes": len(accounts),
        "per_mailbox": int(rules.get(EMAIL_DAILY_PER_MAILBOX) or 0),
        "daily_cap": daily_cap,
        "accounts": [
            {"id": a.id, "name": a.name, "from_email": a.from_email,
             "daily_cap": account_daily_cap(a, rules)}
            for a in accounts
        ],
    }


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


class SendingGate:
    """Answers "may I send now, and how many?" before outbound traffic.

    ``channel`` scopes the counters ("sms" or "email"; None counts everything, the
    original behaviour). ``account_id`` narrows an email gate to one mailbox, for
    a campaign bound to a specific sender or for the message about to use it.
    """

    def __init__(
        self, db: AsyncSession, channel: Optional[str] = None, account_id: Optional[int] = None
    ):
        self.db = db
        self.account_id = account_id
        self.channel = channel or ("email" if account_id is not None else None)

    # -- the daily cap that applies to this gate ---------------------------

    async def _daily_cap(self, rules: dict) -> tuple[Optional[int], str]:
        """(cap, description) for the day, or (None, "") when no cap applies."""
        if not rules["enable_daily_limit"] and self.channel != "email":
            return None, ""
        if self.channel == "email":
            if self.account_id is not None:
                accounts = await _usable_accounts(self.db, [self.account_id])
                if not accounts:
                    return None, ""
                cap = account_daily_cap(accounts[0], rules)
                return cap, f"mailbox '{accounts[0].name}' daily limit"
            capacity = await email_capacity(self.db, rules)
            return capacity["daily_cap"], "daily limit"
        return int(rules["daily_maximum"]), "daily limit"

    async def check(self, now: Optional[datetime] = None) -> dict:
        """Return {allowed, wait_seconds, reason, next_window}."""
        rules = await get_sending_rules(self.db)
        now_utc = (now or _utcnow()).astimezone(timezone.utc)
        now_local = now_utc.astimezone(_tz())

        # 1. Sending window (start/end time + weekends).
        window = self._window_state(rules, now_local, now_utc)
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
                self.channel, self.account_id,
            )
            if sent >= rules["messages_per_minute"]:
                return self._wait_until(
                    next_minute,
                    f"per-minute limit ({rules['messages_per_minute']}) reached",
                    now_utc,
                )

        # 3. Hourly cap.
        hour_start = now_local.replace(minute=0, second=0, microsecond=0)
        next_hour = hour_start + timedelta(hours=1)
        if rules["enable_hourly_limit"]:
            sent = await _count_since(
                self.db,
                hour_start.astimezone(timezone.utc),
                next_hour.astimezone(timezone.utc),
                self.channel, self.account_id,
            )
            if sent >= rules["hourly_maximum"]:
                return self._wait_until(
                    next_hour, f"hourly limit ({rules['hourly_maximum']}) reached", now_utc
                )

        # 4. Daily cap.
        day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        next_day = day_start + timedelta(days=1)
        cap, label = await self._daily_cap(rules)
        if cap is not None:
            sent = await _count_since(
                self.db,
                day_start.astimezone(timezone.utc),
                next_day.astimezone(timezone.utc),
                self.channel, self.account_id,
            )
            if sent >= cap:
                # Sending resumes when the window next opens on a sending day,
                # not at midnight -- that is the time worth telling the operator.
                reopen = self._next_window_start(
                    next_day - timedelta(seconds=1),
                    _parse_time(rules.get("sending_start_time")),
                    rules["allow_weekends"],
                )
                return self._wait_until(reopen, f"{label} ({cap}) reached", now_utc)

        # 5. Pacing: minimum spacing between consecutive messages.
        if rules[ENABLE_PACING]:
            interval = self.effective_interval(rules)
            if interval > 0:
                last = await _last_sent_at(self.db, self.channel, self.account_id)
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

    async def room(self, now: Optional[datetime] = None) -> Optional[int]:
        """How many more messages may be *released* right now; None = no cap applies.

        Used by producers (the campaign engines) to size a batch. Unlike
        :meth:`check` it subtracts what is already queued: a campaign that
        creates 30 messages and is asked again a minute later, before any of them
        went out, must not create 30 more. Zero when the window is closed.
        """
        rules = await get_sending_rules(self.db)
        now_utc = (now or _utcnow()).astimezone(timezone.utc)
        now_local = now_utc.astimezone(_tz())

        if not self._window_state(rules, now_local, now_utc)["allowed"]:
            return 0

        in_flight = await _in_flight(self.db, self.channel, self.account_id)
        remaining: list[int] = []

        if rules["enable_per_minute_limit"]:
            start = now_local.replace(second=0, microsecond=0)
            used = await _count_since(
                self.db, start.astimezone(timezone.utc),
                (start + timedelta(minutes=1)).astimezone(timezone.utc),
                self.channel, self.account_id,
            )
            remaining.append(int(rules["messages_per_minute"]) - used - in_flight)
        if rules["enable_hourly_limit"]:
            start = now_local.replace(minute=0, second=0, microsecond=0)
            used = await _count_since(
                self.db, start.astimezone(timezone.utc),
                (start + timedelta(hours=1)).astimezone(timezone.utc),
                self.channel, self.account_id,
            )
            remaining.append(int(rules["hourly_maximum"]) - used - in_flight)
        cap, _label = await self._daily_cap(rules)
        if cap is not None:
            start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            used = await _count_since(
                self.db, start.astimezone(timezone.utc),
                (start + timedelta(days=1)).astimezone(timezone.utc),
                self.channel, self.account_id,
            )
            remaining.append(cap - used - in_flight)

        if not remaining:
            return None
        return max(0, min(remaining))

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

    def _wait_until(
        self, local_dt: datetime, reason: str, now_utc: Optional[datetime] = None
    ) -> dict:
        """Build a "not yet" answer, measuring the wait from ``now_utc``.

        The reference time must be the one ``check()`` was given, not the real
        clock: ``check(now=...)`` is how callers and tests evaluate the gate at
        a specific moment, and reading datetime.now() here reported a
        wait_seconds for a completely different instant (often negative, and
        then clamped to 1s, which silently defeated the wait).
        """
        utc_dt = local_dt.astimezone(timezone.utc)
        reference = now_utc or _utcnow()
        wait = max(1, int((utc_dt - reference).total_seconds()) + 1)
        return {
            "allowed": False,
            "wait_seconds": wait,
            "reason": reason,
            "next_window": utc_dt.isoformat(),
        }

    def _window_state(
        self, rules: dict, now_local: datetime, now_utc: Optional[datetime] = None
    ) -> dict:
        """Apply start/end time and the weekend toggle."""
        start = _parse_time(rules.get("sending_start_time"))
        end = _parse_time(rules.get("sending_end_time"))

        weekday = now_local.weekday()
        is_weekend = weekday >= 5

        # Weekend toggle
        if is_weekend and not rules["allow_weekends"]:
            next_start = self._next_window_start(now_local, start, False)
            return self._wait_until(next_start, "sending paused on weekends", now_utc)

        # Time-of-day window
        if start and end:
            t = now_local.time().replace(microsecond=0)
            if start < end:
                if not (start <= t < end):
                    next_start = self._next_window_start(now_local, start, rules["allow_weekends"])
                    return self._wait_until(next_start, "outside sending hours", now_utc)
            else:
                # Window wraps midnight, e.g. 20:00 -> 08:00.
                if not (t >= start or t < end):
                    next_start = self._next_window_start(now_local, start, rules["allow_weekends"])
                    return self._wait_until(next_start, "outside sending hours", now_utc)

        return {"allowed": True, "wait_seconds": 0, "reason": None, "next_window": None}

    @staticmethod
    def _next_window_start(
        now_local: datetime, start: Optional[time], allow_weekends: bool = True
    ) -> datetime:
        """The next moment the window opens: ``start`` on the next sending day."""
        candidate = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        if start is not None:
            candidate = candidate.replace(hour=start.hour, minute=start.minute)
        if candidate <= now_local:
            candidate += timedelta(days=1)
        if not allow_weekends:
            while candidate.weekday() >= 5:
                candidate += timedelta(days=1)
        return candidate

    async def status(self) -> dict:
        rules = await get_sending_rules(self.db)
        now_utc = _utcnow()
        now_local = now_utc.astimezone(_tz())
        hour_start = now_local.replace(minute=0, second=0, microsecond=0)
        minute_start = now_local.replace(second=0, microsecond=0)
        day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

        async def count(since, until, channel=None):
            return await _count_since(
                self.db, since.astimezone(timezone.utc), until.astimezone(timezone.utc), channel
            )

        last = await _last_sent_at(self.db, self.channel, self.account_id)
        check = await self.check()
        capacity = await email_capacity(self.db, rules)
        email_sent_today = await count(day_start, day_start + timedelta(days=1), "email")
        email_in_flight = await _in_flight(self.db, "email")
        out = {
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
            "email": {
                **capacity,
                "sent_today": email_sent_today,
                "in_flight": email_in_flight,
                "room_today": (
                    None if capacity["daily_cap"] is None
                    else max(0, capacity["daily_cap"] - email_sent_today - email_in_flight)
                ),
                "allowed_now": (await SendingGate(self.db, "email").check())["allowed"],
            },
        }
        try:
            from app.services import circuit_breaker

            out["breaker"] = await circuit_breaker.summary(self.db, rules)
        except Exception as exc:  # noqa: BLE001 — status must never fail on the breaker
            logger.warning("sending status: breaker summary failed: %s", exc)
            out["breaker"] = None
        return out


async def gate_for_campaign(db: AsyncSession, campaign) -> SendingGate:
    """The gate that governs a campaign: its channel, and for email its mailbox.

    A campaign bound to one mailbox (or using the default one) can only send what
    *that* mailbox is allowed to, however much room the other mailboxes have.
    """
    channel = (getattr(campaign, "channel", None) or "sms")
    if channel != "email":
        return SendingGate(db, "sms")
    from app.services import email_service

    account = await email_service.get_account(db, getattr(campaign, "email_account_id", None))
    account = account or await email_service.get_default_account(db)
    return SendingGate(db, "email", account_id=account.id if account else None)


async def await_slot(db: AsyncSession, cap_seconds: int = 8, channel: Optional[str] = None) -> dict:
    """Wait (bounded) for the next sending slot, then return the final check.

    Used by interactive sends (direct send / retry) where blocking the request
    for a short while is friendlier than an immediate error.
    """
    import asyncio

    gate = SendingGate(db, channel)
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


# --------------------------------------------------------------------------
# How long will this audience take?
# --------------------------------------------------------------------------

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


async def project_send(
    db: AsyncSession,
    *,
    channel: str,
    sendable: int,
    account_ids: Optional[list[int]] = None,
    campaign_daily_limit: Optional[int] = None,
    allowed_weekdays: Optional[set[int]] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Project how long ``sendable`` messages take under the rules in force.

    Pure arithmetic over the same rules the gate enforces: the daily cap (for
    email, mailboxes x per-mailbox), any campaign-level daily limit, the pacing
    ceiling, and which weekdays sending is allowed. It tells the operator, before
    they start, that 925 contacts on one mailbox is a month of sending, not an
    afternoon -- which is the whole point of the cap.
    """
    rules = await get_sending_rules(db)
    now_utc = (now or _utcnow()).astimezone(timezone.utc)
    now_local = now_utc.astimezone(_tz())
    start = _parse_time(rules.get("sending_start_time"))
    end = _parse_time(rules.get("sending_end_time"))

    mailboxes = None
    if channel == "email":
        capacity = await email_capacity(db, rules, account_ids)
        mailboxes = capacity["mailboxes"]
        daily_cap = capacity["daily_cap"]
        if daily_cap is None:
            source = "no daily cap"
        elif mailboxes:
            source = f"{mailboxes} mailbox(es), at most {capacity['per_mailbox']}/day each"
        else:
            source = "no usable mailbox"
    else:
        daily_cap = int(rules["daily_maximum"]) if rules["enable_daily_limit"] else None
        source = "daily limit" if daily_cap is not None else "no daily cap"

    if campaign_daily_limit:
        daily_cap = min(daily_cap, campaign_daily_limit) if daily_cap else int(campaign_daily_limit)
        source += f"; campaign limit {campaign_daily_limit}/day"

    # Pacing: one message every `interval` seconds inside the window.
    interval = SendingGate(db).effective_interval(rules) if rules[ENABLE_PACING] else 0
    if start and end:
        window_seconds = (
            datetime.combine(date.today(), end) - datetime.combine(date.today(), start)
        ).total_seconds()
        if window_seconds <= 0:
            window_seconds += 86400
    else:
        window_seconds = 86400
    pace_cap = int(window_seconds // interval) if interval > 0 else None
    effective = min((c for c in (daily_cap, pace_cap) if c), default=None)

    weekdays = set(range(7)) if rules["allow_weekends"] else set(range(5))
    if allowed_weekdays:
        weekdays &= set(allowed_weekdays)
    weekdays = weekdays or set(range(5))

    sendable = max(0, int(sendable or 0))
    if sendable == 0:
        days_needed = 0
    elif effective is None:
        days_needed = 1
    else:
        days_needed = math.ceil(sendable / effective)

    # Walk the calendar: today counts only if its window has not closed yet.
    first_day = now_local.date()
    if end and now_local.time() >= end:
        first_day += timedelta(days=1)
    starts = None
    finish = None
    counted = 0
    day = first_day
    while days_needed and counted < days_needed and (day - first_day).days < 3660:
        if day.weekday() in weekdays:
            counted += 1
            starts = starts or day
            finish = day
        day += timedelta(days=1)
    calendar_days = ((finish - now_local.date()).days + 1) if finish else 0

    window = (
        f"{rules['sending_start_time']}–{rules['sending_end_time']}"
        if start and end else "any time of day"
    )
    window += f" {settings.DEFAULT_TIMEZONE}"
    if weekdays != set(range(7)):
        days = sorted(weekdays)
        window += f", {_WEEKDAYS[days[0]]}–{_WEEKDAYS[days[-1]]}"

    return {
        "channel": channel,
        "sendable": sendable,
        "mailboxes": mailboxes,
        "daily_cap": daily_cap,
        "daily_cap_source": source,
        "pacing_seconds": interval,
        "effective_per_day": effective,
        "sending_days_per_week": len(weekdays),
        "sending_days_needed": days_needed,
        "calendar_days": calendar_days,
        "starts": starts.isoformat() if starts else None,
        "projected_finish": finish.isoformat() if finish else None,
        "window": window,
    }


def projection_warning(projection: dict) -> Optional[str]:
    """One sentence for ``validate`` when the audience will not go out in a day."""
    days = projection.get("sending_days_needed") or 0
    if days <= 1:
        return None
    return (
        f"At {projection['effective_per_day']}/day, {projection['sendable']} messages take "
        f"{days} sending days ({projection['calendar_days']} calendar days, finishing "
        f"about {projection['projected_finish']}). Limit: {projection['daily_cap_source']}; "
        f"window {projection['window']}."
    )


# --------------------------------------------------------------------------
# One-time migration for databases that predate the safe defaults
# --------------------------------------------------------------------------


async def apply_safe_defaults(db: AsyncSession) -> dict:
    """Turn the protective defaults ON for a database that was never configured.

    Flipping :data:`DEFAULTS` only helps installs that have not saved any rules:
    once the Sending Rules page has been saved, every throttle is stored
    explicitly -- as OFF. Those installs would keep their wide-open settings no
    matter what the defaults say, so this runs once at startup:

    * a database whose stored throttles are *exactly* the old all-open values has
      never been tuned by a person: the safe values are written;
    * a database where any throttle has been set to something else belongs to an
      operator who made a choice: it is left alone;
    * either way a marker records that the decision was made, so an operator who
      later switches a rule back off is never overridden again.

    Idempotent, and returns what it did so the startup log can say so.
    """
    existing = (
        await db.execute(select(SystemSetting).where(SystemSetting.category == CATEGORY))
    ).scalars().all()
    rows = {r.key: r for r in existing}

    def mark(note: str) -> None:
        stamp = f"{_utcnow().isoformat()} {note}"
        if SAFE_DEFAULTS_MARKER in rows:
            rows[SAFE_DEFAULTS_MARKER].value = stamp
        else:
            db.add(SystemSetting(
                key=SAFE_DEFAULTS_MARKER, value=stamp, category=CATEGORY,
                description="One-time safe sending defaults: when/what was decided",
            ))

    if SAFE_DEFAULTS_MARKER in rows:
        return {"applied": False, "reason": "already_applied"}

    explicit = {k: rows[k].value for k in _LEGACY_OPEN if k in rows}
    if not explicit:
        mark("fresh install: defaults already safe")
        await db.flush()
        return {"applied": False, "reason": "defaults_already_safe"}

    def normalised(key: str) -> str:
        value = (explicit[key] or "").strip().lower()
        return value

    if any(normalised(k) != _LEGACY_OPEN[k] for k in explicit):
        mark("operator-configured: left untouched")
        await db.flush()
        return {"applied": False, "reason": "operator_configured"}

    safe = {
        "enable_daily_limit": "true",
        "sending_start_time": DEFAULTS["sending_start_time"],
        "sending_end_time": DEFAULTS["sending_end_time"],
        "allow_weekends": "false",
    }
    for key, value in safe.items():
        if key in rows:
            rows[key].value = value
        else:
            db.add(SystemSetting(key=key, value=value, category=CATEGORY))
    mark("every throttle was off (never configured): safe defaults applied")
    await db.flush()
    logger.warning(
        "SENDING RULES: every outbound throttle was off and had never been configured; "
        "applied the safe defaults (daily cap on, window %s-%s, weekends off). "
        "Change them in Settings -> Sending Rules.",
        safe["sending_start_time"], safe["sending_end_time"],
    )
    return {"applied": True, "reason": "all_throttles_were_off", "values": safe}
