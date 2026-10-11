"""The bounce circuit breaker.

WHY THIS EXISTS
---------------
A campaign that is hurting the sender's reputation has to stop on its own. The
QA sweep found a live campaign with a 45% failure rate and 525 contacts still
queued: nothing in the product would ever have paused it, and by the time a
person looked, the damage to the sending domain was done.

The breaker watches each email campaign over a *rolling* window -- both campaign
systems, the classic ``campaigns`` and the Ads Manager's ``ads_campaigns`` -- and
pauses it when

* more than ``breaker_bounce_pct`` (default 2%) of its recent sends bounced or
  were refused by the provider, or
* spam complaints exceed ``breaker_complaint_pct`` (default 0.10%).

Two guards keep it honest. A *minimum sample* (``breaker_min_sample``, 20): one
bounce in five sends is "20%" and also meaningless, so nothing trips below it.
And it only ever pauses; it never stops or deletes -- a person decides what
happens next, and resuming after a trip needs an explicit acknowledgement
(``acknowledge_breaker=true``) that also restarts the window, so the same old
bounces cannot re-pause the campaign the instant it resumes.

The reason is written on the campaign (``paused_reason``) so an automatic pause
is never a mystery.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Message

logger = logging.getLogger(__name__)

#: Every automatic pause starts with this, which is how a breaker pause is told
#: apart from a person pausing a campaign.
BREAKER_PREFIX = "Circuit breaker"

#: ``failed`` messages whose last_error starts with this never reached the
#: provider (we refused to send), so they say nothing about deliverability.
_NOT_SENT_PREFIX = "Filtered before send"


def _utcnow() -> datetime:
    """The clock. A seam so tests can pin it."""
    return datetime.now(timezone.utc)


def is_breaker_pause(campaign) -> bool:
    """True when ``campaign`` is paused *by the breaker* (not by a person)."""
    return (
        getattr(campaign, "status", None) == "paused"
        and (getattr(campaign, "paused_reason", None) or "").startswith(BREAKER_PREFIX)
    )


def acknowledge(campaign, now: Optional[datetime] = None) -> None:
    """Record that a person has seen the trip and chosen to continue.

    Moves the start of the rolling window to now: judging the very same bounces
    again would re-pause the campaign immediately. A fresh sample must build up
    (and pass) before the breaker can trip again.
    """
    campaign.breaker_reset_at = now or _utcnow()


def _since(rules: dict, campaign, now: datetime) -> datetime:
    since = now - timedelta(hours=int(rules["breaker_window_hours"]))
    reset = getattr(campaign, "breaker_reset_at", None)
    if reset is not None:
        if reset.tzinfo is None:
            reset = reset.replace(tzinfo=timezone.utc)
        since = max(since, reset)
    return since


def _filter(campaign_id: Optional[int], ads_campaign_id: Optional[int]) -> list:
    clauses = [Message.direction == "outgoing", Message.channel == "email"]
    if campaign_id is not None:
        clauses.append(Message.campaign_id == campaign_id)
    if ads_campaign_id is not None:
        clauses.append(Message.ads_campaign_id == ads_campaign_id)
    return clauses


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


def _span(hours: int) -> str:
    return f"{hours} hours" if hours < 48 else f"{round(hours / 24)} days"


def verdict(health: dict, rules: dict) -> Optional[str]:
    """The reason to trip, or None. Pure: numbers in, sentence out."""
    min_sample = int(rules["breaker_min_sample"])
    reasons: list[str] = []

    bounce_limit = float(rules["breaker_bounce_pct"])
    if health["attempts"] >= min_sample and health["bounce_rate_pct"] > bounce_limit:
        bad = health["bounced"] + health["rejected"]
        detail = []
        if health["rejected"]:
            detail.append(f"{health['rejected']} refused by the provider")
        if health["bounced"]:
            detail.append(f"{health['bounced']} bounced")
        reasons.append(
            f"bounce rate {health['bounce_rate_pct']:.1f}% ({bad} of {health['attempts']} sends "
            f"in the last {_span(int(rules['breaker_window_hours']))}; {', '.join(detail)}) "
            f"is above the {bounce_limit:.1f}% limit"
        )

    complaint_limit = float(rules["breaker_complaint_pct"])
    if health["sent"] >= min_sample and health["complaint_rate_pct"] > complaint_limit:
        reasons.append(
            f"spam complaint rate {health['complaint_rate_pct']:.2f}% "
            f"({health['complaints']} of {health['sent']} sends) "
            f"is above the {complaint_limit:.2f}% limit"
        )

    if not reasons:
        return None
    return f"{BREAKER_PREFIX}: " + "; ".join(reasons) + "."


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


#: A campaign is "sending" in these statuses; only a sending campaign is paused.
_SENDING = {"campaign": "running", "ads": "active"}


async def check_and_trip(
    db: AsyncSession,
    *,
    campaign_id: Optional[int] = None,
    ads_campaign_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """Judge one campaign and pause it if it is over the limit.

    Returns None when there is nothing to judge (the breaker is off, the campaign
    is not an email campaign, or it is not sending); otherwise the health numbers
    with ``tripped`` true/false.
    """
    from app.services.sending_limits import get_sending_rules

    kind, campaign = await _load(db, campaign_id, ads_campaign_id)
    if campaign is None:
        return None
    rules = await get_sending_rules(db)
    if not rules["breaker_enabled"]:
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
        since=_since(rules, campaign, now),
        now=now,
    )
    reason = verdict(health, rules)
    outcome = {"kind": kind, "id": campaign.id, "name": campaign.name, "tripped": False, **health}
    if reason is None:
        return outcome

    await _pause(db, kind, campaign, reason, now)
    logger.warning("CIRCUIT BREAKER: paused %s %s (%s): %s", kind, campaign.id, campaign.name, reason)
    outcome.update(tripped=True, reason=reason)
    return outcome


async def _pause(db: AsyncSession, kind: str, campaign, reason: str, now: datetime) -> None:
    if kind == "campaign":
        from app.services.campaign_service import CampaignService

        await CampaignService(db).pause_campaign(campaign.id, reason=reason)
        return
    from app.services import ads_service

    campaign.status = "paused"
    campaign.paused_reason = reason[:500]
    campaign.paused_at = now
    ads_service.log_activity(
        db, "campaign_paused", campaign_id=campaign.id, actor="circuit-breaker", detail=reason[:500]
    )
    await db.flush()


async def check_all(db: AsyncSession, now: Optional[datetime] = None) -> list[dict]:
    """Sweep every sending email campaign in both systems. Returns the ones paused."""
    from app.models.ads import AdsCampaign
    from app.models.campaign import Campaign

    tripped: list[dict] = []
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
        outcome = await check_and_trip(db, campaign_id=cid, now=now)
        if outcome and outcome["tripped"]:
            tripped.append(outcome)
    for aid in ads:
        outcome = await check_and_trip(db, ads_campaign_id=aid, now=now)
        if outcome and outcome["tripped"]:
            tripped.append(outcome)
    return tripped


async def check_after_event(
    db: AsyncSession, *, campaign_id: Optional[int], ads_campaign_id: Optional[int]
) -> None:
    """Hook for the places a bounce is recorded. Never raises: it must not break them."""
    if campaign_id is None and ads_campaign_id is None:
        return
    try:
        if campaign_id is not None:
            await check_and_trip(db, campaign_id=campaign_id)
        if ads_campaign_id is not None:
            await check_and_trip(db, ads_campaign_id=ads_campaign_id)
    except Exception as exc:  # noqa: BLE001 — a breaker fault must not lose a webhook
        logger.error("circuit breaker check failed: %s", exc)


async def summary(db: AsyncSession, rules: Optional[dict] = None) -> dict:
    """The breaker's settings and every campaign it is currently holding."""
    from app.models.ads import AdsCampaign
    from app.models.campaign import Campaign
    from app.services.sending_limits import get_sending_rules

    rules = rules or await get_sending_rules(db)
    held: list[dict] = []
    for kind, model in (("campaign", Campaign), ("ads", AdsCampaign)):
        rows = (
            await db.execute(
                select(model).where(
                    model.status == "paused", model.paused_reason.like(f"{BREAKER_PREFIX}%")
                )
            )
        ).scalars().all()
        for row in rows:
            held.append({
                "kind": kind, "id": row.id, "name": row.name, "reason": row.paused_reason,
                "paused_at": row.paused_at.isoformat() if row.paused_at else None,
            })
    return {
        "enabled": bool(rules["breaker_enabled"]),
        "bounce_pct_limit": rules["breaker_bounce_pct"],
        "complaint_pct_limit": rules["breaker_complaint_pct"],
        "min_sample": rules["breaker_min_sample"],
        "window_hours": rules["breaker_window_hours"],
        "tripped": held,
    }
