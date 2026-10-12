"""Bounce-rate watchdog for email campaigns: it reports, it never stops one.

WHY THE CIRCUIT BREAKER IS GONE
-------------------------------
This module replaces the *bounce circuit breaker*, which read the same numbers
and ended them with a ``pause``: an email campaign above 2% bounces was stopped
mid-run, and putting it back on needed an explicit ``acknowledge_breaker=true``.
That is the wrong trade. A campaign that halts itself halfway down its list is
worse than one that keeps going with a dozen bad addresses -- every good contact
queued behind them simply never hears from you, and the only person who could
fix the list finds out whenever they happen to look.

So the breaker is gone: every automatic pause, the ``paused_reason`` it wrote,
the 409 ``BREAKER_TRIPPED`` and the ``acknowledge_breaker`` escape hatch with
it. What is left is a watchdog with the same eyes and no hands. It measures the
same rolling window per campaign, in both campaign systems (the classic
``campaigns`` and the Ads Manager's ``ads_campaigns``), and when the numbers
pass the same thresholds it *says so* -- an in-app notification plus a browser
push, which is the point, because the person who can fix the list is not sitting
on the campaign page.

Nothing here pauses, stops, deletes or blocks a campaign, and no acknowledgement
is ever required to resume one.

It also never nags: one alert per campaign per ``bounce_alert_cooldown_hours``
(6 by default), however many bounces land in between.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Message

logger = logging.getLogger(__name__)

#: The ``NotificationEvent.event_type`` written for every alert. The in-app
#: notification centre and the frontend's ``notificationDisplay`` key off it.
ALERT_EVENT = "bounce_rate_alert"

#: ``failed`` messages whose last_error starts with this never reached the
#: provider (we refused to send), so they say nothing about deliverability.
_NOT_SENT_PREFIX = "Filtered before send"


def _utcnow() -> datetime:
    """The clock. A seam so tests can pin it."""
    return datetime.now(timezone.utc)


async def campaign_health(
    db: AsyncSession,
    *,
    campaign_id: Optional[int] = None,
    ads_campaign_id: Optional[int] = None,
    since: Optional[datetime] = None,
    now: Optional[datetime] = None,
    window_hours: int = 168,
) -> dict:
    """Counts for one campaign over the window: sent, bounced, rejected, complaints.

    * ``sent`` -- messages the provider accepted (``sent_at`` set), whatever
      happened to them afterwards;
    * ``bounced`` -- of those, the ones that bounced/blocked/were reported as spam;
    * ``rejected`` -- messages the provider refused outright (never sent), not
      counting ones *we* declined to send;
    * ``complaints`` -- spam-complaint events.
    """
    from app.models.email import EmailEvent

    now = now or _utcnow()
    since = since or (now - timedelta(hours=window_hours))
    base = _filter(campaign_id, ads_campaign_id)

    async def count(*where) -> int:
        return (
            await db.execute(select(func.count(Message.id)).where(*base, *where))
        ).scalar() or 0

    sent = await count(Message.sent_at.is_not(None), Message.sent_at >= since)
    bounced = await count(
        Message.sent_at.is_not(None), Message.sent_at >= since, Message.bounced_at.is_not(None)
    )
    rejected = await count(
        Message.status == "failed",
        Message.sent_at.is_(None),
        Message.failed_at.is_not(None),
        Message.failed_at >= since,
        or_(Message.last_error.is_(None), ~Message.last_error.like(f"{_NOT_SENT_PREFIX}%")),
    )
    event_filter = [EmailEvent.event_type == "spam", EmailEvent.created_at >= since]
    if campaign_id is not None:
        event_filter.append(EmailEvent.campaign_id == campaign_id)
    if ads_campaign_id is not None:
        event_filter.append(EmailEvent.ads_campaign_id == ads_campaign_id)
    complaints = (
        await db.execute(select(func.count(EmailEvent.id)).where(*event_filter))
    ).scalar() or 0

    attempts = sent + rejected
    return {
        "since": since.isoformat(),
        "sent": sent,
        "bounced": bounced,
        "rejected": rejected,
        "complaints": complaints,
        "attempts": attempts,
        "bounce_rate_pct": round((bounced + rejected) / attempts * 100, 2) if attempts else 0.0,
        "complaint_rate_pct": round(complaints / sent * 100, 3) if sent else 0.0,
    }


def _filter(campaign_id: Optional[int], ads_campaign_id: Optional[int]) -> list:
    clauses = [Message.direction == "outgoing", Message.channel == "email"]
    if campaign_id is not None:
        clauses.append(Message.campaign_id == campaign_id)
    if ads_campaign_id is not None:
        clauses.append(Message.ads_campaign_id == ads_campaign_id)
    return clauses


def _span(hours: int) -> str:
    return f"{hours} hours" if hours < 48 else f"{round(hours / 24)} days"


def verdict(health: dict, rules: dict) -> Optional[str]:
    """The sentence to tell the operator, or None. Pure: numbers in, words out."""
    min_sample = int(rules["bounce_alert_min_sample"])
    reasons: list[str] = []

    bounce_limit = float(rules["bounce_alert_bounce_pct"])
    if health["attempts"] >= min_sample and health["bounce_rate_pct"] > bounce_limit:
        bad = health["bounced"] + health["rejected"]
        detail = []
        if health["rejected"]:
            detail.append(f"{health['rejected']} refused by the provider")
        if health["bounced"]:
            detail.append(f"{health['bounced']} bounced")
        reasons.append(
            f"bounce rate {health['bounce_rate_pct']:.1f}% ({bad} of {health['attempts']} sends "
            f"in the last {_span(int(rules['bounce_alert_window_hours']))}; {', '.join(detail)}) "
            f"is above the {bounce_limit:.1f}% limit"
        )

    complaint_limit = float(rules["bounce_alert_complaint_pct"])
    if health["sent"] >= min_sample and health["complaint_rate_pct"] > complaint_limit:
        reasons.append(
            f"spam complaint rate {health['complaint_rate_pct']:.2f}% "
            f"({health['complaints']} of {health['sent']} sends) "
            f"is above the {complaint_limit:.2f}% limit"
        )

    if not reasons:
        return None
    sentence = "; ".join(reasons)
    return sentence[0].upper() + sentence[1:] + "."


async def _load(db: AsyncSession, campaign_id: Optional[int], ads_campaign_id: Optional[int]):
    """(kind, campaign) for whichever system the id belongs to, or (None, None)."""
    if campaign_id is not None:
        from app.models.campaign import Campaign

        row = (
            await db.execute(select(Campaign).where(Campaign.id == campaign_id))
        ).scalar_one_or_none()
        return "campaign", row
    if ads_campaign_id is not None:
        from app.models.ads import AdsCampaign

        row = (
            await db.execute(select(AdsCampaign).where(AdsCampaign.id == ads_campaign_id))
        ).scalar_one_or_none()
        return "ads", row
    return None, None


#: A campaign is "sending" in these statuses; only a sending campaign is watched.
_SENDING = {"campaign": "running", "ads": "active"}

#: Where a tap on the alert lands: the page that lists the campaign.
_ALERT_URL = {"campaign": "/campaigns", "ads": "/sms-manager"}


async def _alerted_recently(
    db: AsyncSession, kind: str, campaign_id: int, since: datetime
) -> bool:
    """True when this campaign already has an alert inside the cooldown.

    A campaign with a bad list bounces on every send. Without this the watchdog
    would fire a notification per bounce and bury the notification centre -- the
    opposite of useful.
    """
    from app.models.notification import NotificationEvent
    from app.services.push_service import BROWSER_PROVIDER

    found = (
        await db.execute(
            select(NotificationEvent.id).where(
                NotificationEvent.event_type == ALERT_EVENT,
                NotificationEvent.provider == BROWSER_PROVIDER,
                NotificationEvent.reference_id == campaign_id,
                NotificationEvent.reference_type == kind,
                NotificationEvent.created_at >= since,
            ).limit(1)
        )
    ).scalar_one_or_none()
    return found is not None


async def check_and_alert(
    db: AsyncSession,
    *,
    campaign_id: Optional[int] = None,
    ads_campaign_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """Judge one campaign and notify when it is over the limit. It never pauses.

    Returns None when there is nothing to judge (the watchdog is off, the
    campaign is not an email campaign, or it is not sending); otherwise the
    health numbers with ``alerted`` true/false and, when it alerted, ``reason``.
    """
    from app.services.sending_limits import get_sending_rules

    kind, campaign = await _load(db, campaign_id, ads_campaign_id)
    if campaign is None:
        return None
    rules = await get_sending_rules(db)
    if not rules["bounce_alert_enabled"]:
        return None
    if (campaign.channel or "sms") != "email":
        return None
    if campaign.status != _SENDING[kind]:
        return None

    now = now or _utcnow()
    health = await campaign_health(
        db,
        campaign_id=campaign.id if kind == "campaign" else None,
        ads_campaign_id=campaign.id if kind == "ads" else None,
        since=now - timedelta(hours=int(rules["bounce_alert_window_hours"])),
        now=now,
    )
    reason = verdict(health, rules)
    outcome = {
        "kind": kind,
        "id": campaign.id,
        "name": campaign.name,
        "alerted": False,
        **health,
    }
    if reason is None:
        return outcome

    cooldown = timedelta(hours=int(rules["bounce_alert_cooldown_hours"]))
    if await _alerted_recently(db, kind, campaign.id, now - cooldown):
        outcome["reason"] = reason
        outcome["suppressed"] = True
        return outcome

    await _notify(db, kind, campaign, reason)
    logger.warning("BOUNCE WATCH: alerted on %s %s (%s): %s", kind, campaign.id, campaign.name, reason)
    outcome.update(alerted=True, reason=reason)
    return outcome


async def _notify(db: AsyncSession, kind: str, campaign, reason: str) -> None:
    from app.services.push_service import notify_bounce_rate

    await notify_bounce_rate(
        db,
        campaign_name=campaign.name,
        detail=reason,
        campaign_id=campaign.id,
        url=_ALERT_URL[kind],
        reference_type=kind,
    )


async def check_all(db: AsyncSession, now: Optional[datetime] = None) -> list[dict]:
    """Sweep every sending email campaign in both systems. Returns the alerts."""
    from app.models.ads import AdsCampaign
    from app.models.campaign import Campaign

    alerts: list[dict] = []
    classic = (
        await db.execute(
            select(Campaign.id).where(Campaign.status == "running", Campaign.channel == "email")
        )
    ).scalars().all()
    ads = (
        await db.execute(
            select(AdsCampaign.id).where(
                AdsCampaign.status == "active", AdsCampaign.channel == "email"
            )
        )
    ).scalars().all()
    for cid in classic:
        outcome = await check_and_alert(db, campaign_id=cid, now=now)
        if outcome and outcome["alerted"]:
            alerts.append(outcome)
    for aid in ads:
        outcome = await check_and_alert(db, ads_campaign_id=aid, now=now)
        if outcome and outcome["alerted"]:
            alerts.append(outcome)
    return alerts


async def check_after_event(
    db: AsyncSession, *, campaign_id: Optional[int], ads_campaign_id: Optional[int]
) -> None:
    """Hook for the places a bounce is recorded. Never raises: it must not break them."""
    if campaign_id is None and ads_campaign_id is None:
        return
    try:
        if campaign_id is not None:
            await check_and_alert(db, campaign_id=campaign_id)
        if ads_campaign_id is not None:
            await check_and_alert(db, ads_campaign_id=ads_campaign_id)
    except Exception as exc:  # noqa: BLE001 — a watchdog fault must not lose a webhook
        logger.error("bounce watch check failed: %s", exc)
