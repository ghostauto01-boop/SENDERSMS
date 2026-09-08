"""SMS Ads Manager engine.

Everything the new campaign manager actually *does* lives here: audience
resolution, creative splitting, duplicate protection, the dispatcher, follow-up
automation, analytics and simulation.

Design rules honoured throughout:

* Existing infrastructure is reused, never duplicated. Contacts, lists, tags,
  the suppression list, the ``messages`` table, ``SendingGate`` (limits/pacing)
  and the SMS provider are all the pre-existing ones.
* A contact is assigned exactly one creative per campaign, enforced by a UNIQUE
  constraint, so no retry, restart or double worker can duplicate a send.
* Creative edits create a new version; sent history keeps its version.
* Suppression / opt-out is re-checked immediately before dispatch, not only
  when the audience was built.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ads import (
    AdsActivityLog,
    AdsAssignment,
    AdsCalendarEvent,
    AdsCampaign,
    AdsCreative,
    AdsCreativeVersion,
    AdsEvent,
    AdsFollowUpStep,
    AdsFollowUpTask,
    AdsSet,
)
from app.models.contact import Contact, ContactTag, Tag
from app.models.contact_list import ContactListMember
from app.models.conversation import Conversation, Message
from app.models.suppression import SuppressionEntry

logger = logging.getLogger(__name__)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ==========================================================================
# Event + activity helpers
# ==========================================================================

EVENT_TYPES = (
    "MESSAGE_QUEUED",
    "MESSAGE_SENT",
    "MESSAGE_DELIVERED",
    "MESSAGE_FAILED",
    "MESSAGE_SKIPPED",
    "REPLY_RECEIVED",
    "REPLY_CLASSIFIED",
    "LINK_CLICKED",
    "FOLLOW_UP_CREATED",
    "MEETING_BOOKED",
    "CONTACT_CONVERTED",
    "CONTACT_OPTED_OUT",
    "CREATIVE_PAUSED",
)


def record_event(
    db: AsyncSession,
    event_type: str,
    *,
    campaign_id: int | None = None,
    set_id: int | None = None,
    creative_id: int | None = None,
    creative_version_id: int | None = None,
    contact_id: int | None = None,
    assignment_id: int | None = None,
    detail: str | None = None,
) -> AdsEvent:
    event = AdsEvent(
        event_type=event_type,
        campaign_id=campaign_id,
        set_id=set_id,
        creative_id=creative_id,
        creative_version_id=creative_version_id,
        contact_id=contact_id,
        assignment_id=assignment_id,
        detail=detail,
    )
    db.add(event)
    return event


def log_activity(
    db: AsyncSession,
    action: str,
    *,
    campaign_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    detail: str | None = None,
    actor: str = "system",
) -> AdsActivityLog:
    row = AdsActivityLog(
        action=action,
        campaign_id=campaign_id,
        entity_type=entity_type,
        entity_id=entity_id,
        detail=detail,
        actor=actor,
    )
    db.add(row)
    return row


# ==========================================================================
# Creative versioning
# ==========================================================================


async def ensure_version(db: AsyncSession, creative: AdsCreative) -> AdsCreativeVersion:
    """Return the current version row, creating it if the creative has none."""
    row = (
        await db.execute(
            select(AdsCreativeVersion).where(
                AdsCreativeVersion.creative_id == creative.id,
                AdsCreativeVersion.version == creative.current_version,
            )
        )
    ).scalar_one_or_none()
    if row:
        return row
    row = AdsCreativeVersion(
        creative_id=creative.id,
        version=creative.current_version,
        body=creative.body or "",
        cta=creative.cta,
    )
    db.add(row)
    await db.flush()
    return row


async def bump_version(db: AsyncSession, creative: AdsCreative, body: str, cta: str | None) -> AdsCreativeVersion:
    """Write a NEW immutable version. Never mutates an existing one."""
    creative.current_version = (creative.current_version or 0) + 1
    creative.body = body
    creative.cta = cta
    row = AdsCreativeVersion(
        creative_id=creative.id,
        version=creative.current_version,
        body=body,
        cta=cta,
    )
    db.add(row)
    await db.flush()
    return row


# ==========================================================================
# Audience engine
# ==========================================================================


async def _tagged_contact_ids(db: AsyncSession, names: list[str]) -> set[int]:
    if not names:
        return set()
    rows = await db.execute(
        select(ContactTag.contact_id)
        .join(Tag, Tag.id == ContactTag.tag_id)
        .where(func.lower(Tag.name).in_([n.lower() for n in names]))
    )
    return set(rows.scalars().all())


async def resolve_audience(db: AsyncSession, ads_set: AdsSet) -> list[Contact]:
    """Return the contacts this SMS Set targets, before eligibility checks.

    Reuses the existing contacts / lists / tags tables. Nothing is copied.
    """
    query = select(Contact)

    list_ids = [int(x) for x in ads_set.csv("list_ids") if x.isdigit()]
    if list_ids:
        query = query.where(
            Contact.id.in_(
                select(ContactListMember.contact_id).where(ContactListMember.list_id.in_(list_ids))
            )
        )

    if ads_set.city:
        query = query.where(func.lower(Contact.city) == ads_set.city.lower())
    if ads_set.state:
        query = query.where(func.lower(Contact.state) == ads_set.state.lower())
    if ads_set.industry:
        query = query.where(func.lower(Contact.industry) == ads_set.industry.lower())

    inc = [s.lower() for s in ads_set.csv("include_statuses")]
    if inc:
        query = query.where(func.lower(Contact.lead_status).in_(inc))
    exc = [s.lower() for s in ads_set.csv("exclude_statuses")]
    if exc:
        query = query.where(func.lower(Contact.lead_status).notin_(exc))

    activity = (ads_set.activity_filter or "any").lower()
    if activity == "never_contacted":
        query = query.where(Contact.last_contacted_at.is_(None))
    elif activity == "contacted":
        query = query.where(Contact.last_contacted_at.is_not(None))
    elif activity == "replied":
        query = query.where(Contact.last_reply_at.is_not(None))
    elif activity == "not_replied":
        query = query.where(Contact.last_reply_at.is_(None))

    contacts = list((await db.execute(query)).scalars().all())

    include_tags = ads_set.csv("include_tags")
    if include_tags:
        keep = await _tagged_contact_ids(db, include_tags)
        contacts = [c for c in contacts if c.id in keep]
    exclude_tags = ads_set.csv("exclude_tags")
    if exclude_tags:
        drop = await _tagged_contact_ids(db, exclude_tags)
        contacts = [c for c in contacts if c.id not in drop]

    exclude_campaigns = [int(x) for x in ads_set.csv("exclude_campaign_ids") if x.isdigit()]
    if exclude_campaigns:
        drop = set(
            (
                await db.execute(
                    select(AdsAssignment.contact_id).where(
                        AdsAssignment.campaign_id.in_(exclude_campaigns)
                    )
                )
            ).scalars().all()
        )
        contacts = [c for c in contacts if c.id not in drop]

    return contacts


async def _suppressed_numbers(db: AsyncSession, numbers: Iterable[str]) -> set[str]:
    numbers = [n for n in numbers if n]
    if not numbers:
        return set()
    rows = await db.execute(
        select(SuppressionEntry.phone_number).where(SuppressionEntry.phone_number.in_(numbers))
    )
    return set(rows.scalars().all())


def normalize(phone: str | None) -> str | None:
    """Normalize to E.164 using the app's existing phone utilities."""
    if not phone:
        return None
    from app.utils.phone import clean_phone_number, normalize_nigerian_number

    return normalize_nigerian_number(phone) or clean_phone_number(phone) or None


SKIP_REASONS = (
    "duplicate",
    "suppressed",
    "opted_out",
    "already_sent",
    "frequency_limit",
    "outside_schedule",
    "campaign_paused",
    "creative_paused",
    "insufficient_balance",
    "invalid_number",
)


async def screen_contacts(
    db: AsyncSession, campaign: AdsCampaign, contacts: list[Contact]
) -> tuple[list[Contact], dict[str, int]]:
    """Apply eligibility rules. Returns (eligible, {skip_reason: count})."""
    counts: dict[str, int] = {}

    def bump(reason: str) -> None:
        counts[reason] = counts.get(reason, 0) + 1

    existing = set(
        (
            await db.execute(
                select(AdsAssignment.contact_id).where(AdsAssignment.campaign_id == campaign.id)
            )
        ).scalars().all()
    )
    suppressed = await _suppressed_numbers(db, [c.phone_number for c in contacts])

    eligible: list[Contact] = []
    seen_numbers: set[str] = set()
    for contact in contacts:
        number = normalize(contact.phone_number)
        if not number:
            bump("invalid_number")
            continue
        if contact.id in existing:
            bump("already_sent")
            continue
        if number in seen_numbers:
            bump("duplicate")
            continue
        if contact.is_opted_out:
            bump("opted_out")
            continue
        if contact.phone_number in suppressed or number in suppressed:
            bump("suppressed")
            continue
        seen_numbers.add(number)
        eligible.append(contact)

    return eligible, counts


# ==========================================================================
# Creative split
# ==========================================================================


def split_counts(total: int, weights: list[float]) -> list[int]:
    """Distribute ``total`` across weights, losing nothing to rounding.

    ``split_counts(10000, [1,1,1]) -> [3334, 3333, 3333]``
    """
    if total <= 0 or not weights:
        return [0] * len(weights)
    weight_sum = sum(weights)
    if weight_sum <= 0:
        weights = [1.0] * len(weights)
        weight_sum = float(len(weights))
    raw = [total * w / weight_sum for w in weights]
    counts = [int(x) for x in raw]
    remainder = total - sum(counts)
    # Hand the leftovers to the largest fractional parts, biggest first, so the
    # split is deterministic and the first bucket absorbs the extra.
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - counts[i]), i))
    for i in range(remainder):
        counts[order[i % len(order)]] += 1
    return counts


def creative_weights(ads_set: AdsSet, creatives: list[AdsCreative]) -> list[float]:
    mode = (ads_set.split_mode or "equal").lower()
    if mode in ("percentage", "weighted") and any((c.allocation or 0) > 0 for c in creatives):
        return [float(c.allocation or 0) for c in creatives]
    return [1.0] * len(creatives)


def active_creatives(creatives: list[AdsCreative]) -> list[AdsCreative]:
    return [c for c in creatives if c.status == "active" and not c.is_deleted]


async def assign_contacts(
    db: AsyncSession,
    campaign: AdsCampaign,
    ads_set: AdsSet,
    contacts: list[Contact],
    *,
    dry_run: bool = False,
) -> dict:
    """Split ``contacts`` across the set's active creatives and persist.

    The user never divides their list by hand: this does it, once, and the
    assignment is sticky for the life of the experiment.
    """
    creatives = active_creatives(
        list(
            (
                await db.execute(
                    select(AdsCreative).where(
                        AdsCreative.set_id == ads_set.id, AdsCreative.is_deleted.is_(False)
                    ).order_by(AdsCreative.id)
                )
            ).scalars().all()
        )
    )
    if not creatives or not contacts:
        return {"assigned": 0, "per_creative": {}, "creatives": len(creatives)}

    pool = list(contacts)
    if (ads_set.split_mode or "equal").lower() == "random":
        random.Random(campaign.id * 7919 + ads_set.id).shuffle(pool)

    counts = split_counts(len(pool), creative_weights(ads_set, creatives))
    per_creative: dict[int, int] = {}
    cursor = 0
    assigned = 0

    for creative, take in zip(creatives, counts):
        chunk = pool[cursor : cursor + take]
        cursor += take
        per_creative[creative.id] = len(chunk)
        if dry_run:
            continue
        version = await ensure_version(db, creative)
        for contact in chunk:
            db.add(
                AdsAssignment(
                    campaign_id=campaign.id,
                    set_id=ads_set.id,
                    creative_id=creative.id,
                    creative_version_id=version.id,
                    contact_id=contact.id,
                    phone_number=normalize(contact.phone_number),
                    assignment_method=(ads_set.split_mode or "equal"),
                    experiment_group=creative.name,
                    send_status="pending",
                )
            )
            assigned += 1

    if not dry_run:
        await db.flush()
        record_event(
            db,
            "MESSAGE_QUEUED",
            campaign_id=campaign.id,
            set_id=ads_set.id,
            detail=f"{assigned} contacts assigned",
        )
        log_activity(
            db,
            "audience_assigned",
            campaign_id=campaign.id,
            entity_type="set",
            entity_id=ads_set.id,
            detail=f"{assigned} contacts assigned across {len(creatives)} creative(s)",
        )

    return {
        "assigned": assigned if not dry_run else sum(per_creative.values()),
        "per_creative": per_creative,
        "creatives": len(creatives),
    }


async def build_audience(
    db: AsyncSession, campaign: AdsCampaign, *, dry_run: bool = False
) -> dict:
    """Resolve, screen and assign every set's audience. Idempotent.

    Called at launch, when the user clicks "Add contacts", and on every cycle
    of an always-on campaign to pick up newly matching contacts.
    """
    sets = list(
        (
            await db.execute(
                select(AdsSet).where(AdsSet.campaign_id == campaign.id, AdsSet.status == "active")
            )
        ).scalars().all()
    )
    summary = {
        "audience": 0,
        "added": 0,
        "skipped": {},
        "per_set": [],
    }
    for ads_set in sets:
        contacts = await resolve_audience(db, ads_set)
        eligible, skipped = await screen_contacts(db, campaign, contacts)
        result = await assign_contacts(db, campaign, ads_set, eligible, dry_run=dry_run)
        summary["audience"] += len(contacts)
        summary["added"] += result["assigned"]
        for k, v in skipped.items():
            summary["skipped"][k] = summary["skipped"].get(k, 0) + v
        summary["per_set"].append(
            {
                "set_id": ads_set.id,
                "set_name": ads_set.name,
                "matched": len(contacts),
                "eligible": len(eligible),
                "assigned": result["assigned"],
                "per_creative": result["per_creative"],
                "skipped": skipped,
            }
        )
    return summary


# ==========================================================================
# Scheduling / pacing
# ==========================================================================


def _campaign_tz(campaign: AdsCampaign):
    from zoneinfo import ZoneInfo

    from app.config import settings

    name = campaign.timezone_name or getattr(settings, "default_timezone", None) or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - a bad tz must not stop sending
        return timezone.utc


def window_state(campaign: AdsCampaign, when: datetime | None = None) -> tuple[bool, str | None]:
    """Is the campaign inside its own sending window right now?"""
    when = as_utc(when or now_utc())
    local = when.astimezone(_campaign_tz(campaign))

    if not campaign.weekday_allowed(local.weekday()):
        return False, "outside_sending_window"
    start, end = campaign.send_start_hour, campaign.send_end_hour
    if start is not None and end is not None and start != end:
        if start < end:
            inside = start <= local.hour < end
        else:  # window crosses midnight
            inside = local.hour >= start or local.hour < end
        if not inside:
            return False, "outside_sending_window"
    if campaign.start_date and when < as_utc(campaign.start_date):
        return False, "waiting_for_schedule"
    if campaign.end_date and when > as_utc(campaign.end_date):
        return False, "completed"
    return True, None


async def sent_today(db: AsyncSession, campaign: AdsCampaign) -> int:
    local_midnight = now_utc().astimezone(_campaign_tz(campaign)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    since = local_midnight.astimezone(timezone.utc)
    return (
        await db.execute(
            select(func.count())
            .select_from(AdsAssignment)
            .where(
                AdsAssignment.campaign_id == campaign.id,
                AdsAssignment.sent_at.is_not(None),
                AdsAssignment.sent_at >= since,
            )
        )
    ).scalar() or 0


async def sent_total(db: AsyncSession, campaign: AdsCampaign) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(AdsAssignment)
            .where(AdsAssignment.campaign_id == campaign.id, AdsAssignment.sent_at.is_not(None))
        )
    ).scalar() or 0


def drip_allowance(campaign: AdsCampaign, last_sent_at: datetime | None) -> int:
    """How many messages the drip configuration permits in this tick."""
    mode = (campaign.drip_mode or "off").lower()
    if mode == "off":
        return 10_000
    interval = max(int(campaign.drip_interval_minutes or 0), 0)
    batch = max(int(campaign.drip_batch_size or 1), 1)
    if mode in ("interval", "batch"):
        if interval and last_sent_at:
            elapsed = (now_utc() - as_utc(last_sent_at)).total_seconds() / 60.0
            if elapsed < interval:
                return 0
        return 1 if mode == "interval" else batch
    if mode in ("daily", "smart"):
        # Daily budget is enforced separately; smart pacing spreads the daily
        # limit over the window so each tick releases a proportional slice.
        return batch if mode == "smart" else 10_000
    return 10_000


async def campaign_state(db: AsyncSession, campaign: AdsCampaign) -> str:
    """The system status shown to the user (never overwrites ``status``)."""
    if campaign.status != "active":
        return campaign.status
    ok, reason = window_state(campaign, now_utc())
    if not ok:
        return reason or "waiting_for_schedule"
    if campaign.daily_limit and await sent_today(db, campaign) >= campaign.daily_limit:
        return "daily_limit_reached"
    if campaign.total_limit and await sent_total(db, campaign) >= campaign.total_limit:
        return "completed"
    pending = (
        await db.execute(
            select(func.count())
            .select_from(AdsAssignment)
            .where(AdsAssignment.campaign_id == campaign.id, AdsAssignment.send_status == "pending")
        )
    ).scalar() or 0
    if not pending:
        return "no_eligible_contacts"
    return "sending"


# ==========================================================================
# Dispatcher
# ==========================================================================


async def _frequency_blocked(db: AsyncSession, campaign: AdsCampaign, contact_id: int) -> bool:
    """Campaign-level per-contact frequency caps (never loosen global rules)."""
    if not campaign.max_per_contact_per_day and not campaign.max_per_contact_per_week:
        return False
    now = now_utc()
    if campaign.max_per_contact_per_day:
        count = (
            await db.execute(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.contact_id == contact_id,
                    Message.direction == "outgoing",
                    Message.created_at >= now - timedelta(days=1),
                )
            )
        ).scalar() or 0
        if count >= campaign.max_per_contact_per_day:
            return True
    if campaign.max_per_contact_per_week:
        count = (
            await db.execute(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.contact_id == contact_id,
                    Message.direction == "outgoing",
                    Message.created_at >= now - timedelta(days=7),
                )
            )
        ).scalar() or 0
        if count >= campaign.max_per_contact_per_week:
            return True
    return False


async def _creative_body(db: AsyncSession, assignment: AdsAssignment, campaign: AdsCampaign) -> tuple[str | None, AdsCreative | None]:
    creative = (
        await db.execute(select(AdsCreative).where(AdsCreative.id == assignment.creative_id))
    ).scalar_one_or_none()
    if creative is None:
        return None, None
    if creative.is_deleted or creative.status != "active":
        return None, creative

    # Version policy: "keep" (default) sends the version the contact was
    # assigned, so an edit mid-flight never rewrites what a queued contact was
    # promised. "update" opts in to the latest text.
    if (campaign.queued_edit_policy or "keep") == "update":
        return creative.body, creative
    if assignment.creative_version_id:
        version = (
            await db.execute(
                select(AdsCreativeVersion).where(
                    AdsCreativeVersion.id == assignment.creative_version_id
                )
            )
        ).scalar_one_or_none()
        if version:
            return version.body, creative
    return creative.body, creative


async def dispatch_campaign(
    db: AsyncSession, campaign: AdsCampaign, *, limit: int = 25, send_inline: bool = False
) -> dict:
    """Send the next slice of a campaign. Safe to run concurrently.

    Every message passes the full final validation immediately before dispatch:
    campaign/set/creative active, contact not opted out, not suppressed,
    frequency, window, daily+total budget, balance, idempotency.
    """
    result = {"sent": 0, "skipped": 0, "state": campaign.status, "reasons": {}}

    def skip(reason: str) -> None:
        result["skipped"] += 1
        result["reasons"][reason] = result["reasons"].get(reason, 0) + 1

    if campaign.status != "active":
        result["state"] = campaign.status
        return result

    ok, reason = window_state(campaign)
    if not ok:
        if reason == "completed":
            campaign.status = "completed"
            log_activity(db, "campaign_completed", campaign_id=campaign.id, detail="End date reached")
        result["state"] = reason or "waiting_for_schedule"
        campaign.last_state = result["state"]
        campaign.last_state_at = now_utc()
        await db.flush()
        return result

    # Budgets
    day_used = await sent_today(db, campaign)
    total_used = await sent_total(db, campaign)
    room = limit
    if campaign.daily_limit:
        room = min(room, max(campaign.daily_limit - day_used, 0))
        if room == 0:
            result["state"] = "daily_limit_reached"
            campaign.last_state = result["state"]
            await db.flush()
            return result
    if campaign.total_limit:
        room = min(room, max(campaign.total_limit - total_used, 0))
        if room == 0:
            campaign.status = "completed"
            result["state"] = "completed"
            log_activity(db, "campaign_completed", campaign_id=campaign.id, detail="Total SMS limit reached")
            await db.flush()
            return result

    # Drip pacing
    last_sent = (
        await db.execute(
            select(func.max(AdsAssignment.sent_at)).where(AdsAssignment.campaign_id == campaign.id)
        )
    ).scalar()
    room = min(room, drip_allowance(campaign, last_sent))
    if room <= 0:
        result["state"] = "sending"
        return result

    # Global sending limits / pacing -- the SAME gate the rest of the app uses.
    if not campaign.test_mode:
        from app.services.sending_limits import SendingGate

        gate = await SendingGate(db).check()
        if not gate["allowed"]:
            result["state"] = "sending"
            result["reasons"]["rate_limited"] = 1
            campaign.last_state = "sending"
            await db.flush()
            return result

    rows = list(
        (
            await db.execute(
                select(AdsAssignment)
                .where(
                    AdsAssignment.campaign_id == campaign.id,
                    AdsAssignment.send_status == "pending",
                    or_(
                        AdsAssignment.next_attempt_at.is_(None),
                        AdsAssignment.next_attempt_at <= now_utc(),
                    ),
                )
                .order_by(AdsAssignment.id)
                .limit(room)
            )
        ).scalars().all()
    )

    outbox: list[int] = []
    for assignment in rows:
        # Atomic claim: two workers can never both send this row.
        claim = await db.execute(
            update(AdsAssignment)
            .where(AdsAssignment.id == assignment.id, AdsAssignment.send_status == "pending")
            .values(send_status="sending")
        )
        if claim.rowcount != 1:
            continue

        contact = (
            await db.execute(select(Contact).where(Contact.id == assignment.contact_id))
        ).scalar_one_or_none()
        if contact is None:
            assignment.send_status = "skipped"
            assignment.skip_reason = "invalid_number"
            skip("invalid_number")
            continue

        # FINAL send-time re-validation. A contact may have opted out after the
        # queue was built.
        if contact.is_opted_out:
            assignment.send_status = "skipped"
            assignment.skip_reason = "opted_out"
            skip("opted_out")
            record_event(db, "MESSAGE_SKIPPED", campaign_id=campaign.id, contact_id=contact.id, detail="opted_out")
            continue
        number = normalize(contact.phone_number)
        if not number:
            assignment.send_status = "skipped"
            assignment.skip_reason = "invalid_number"
            skip("invalid_number")
            continue
        if await _suppressed_numbers(db, [contact.phone_number, number]):
            assignment.send_status = "skipped"
            assignment.skip_reason = "suppressed"
            skip("suppressed")
            record_event(db, "MESSAGE_SKIPPED", campaign_id=campaign.id, contact_id=contact.id, detail="suppressed")
            continue
        if await _frequency_blocked(db, campaign, contact.id):
            assignment.send_status = "pending"
            assignment.skip_reason = "frequency_limit"
            assignment.next_attempt_at = now_utc() + timedelta(hours=6)
            skip("frequency_limit")
            continue

        ads_set = (
            await db.execute(select(AdsSet).where(AdsSet.id == assignment.set_id))
        ).scalar_one_or_none()
        if ads_set is None or ads_set.status != "active":
            assignment.send_status = "pending"
            assignment.skip_reason = "campaign_paused"
            assignment.next_attempt_at = now_utc() + timedelta(minutes=30)
            skip("campaign_paused")
            continue

        body, creative = await _creative_body(db, assignment, campaign)
        if creative is None or not body or not body.strip():
            # A paused creative must NOT be dropped: put the contact back so it
            # resumes if the creative is re-activated.
            assignment.send_status = "pending"
            assignment.skip_reason = "creative_paused"
            assignment.next_attempt_at = now_utc() + timedelta(minutes=30)
            skip("creative_paused")
            continue

        from app.services.variable_service import render_for_contact

        rendered = await render_for_contact(db, body, contact)

        if campaign.test_mode:
            # Simulated send: nothing leaves the server, no credits consumed.
            assignment.send_status = "sent"
            assignment.sent_at = now_utc()
            assignment.last_action_at = now_utc()
            assignment.last_error = None
            assignment.skip_reason = None
            assignment.delivery_status = "simulated"
            record_event(
                db,
                "MESSAGE_SENT",
                campaign_id=campaign.id,
                set_id=assignment.set_id,
                creative_id=assignment.creative_id,
                creative_version_id=assignment.creative_version_id,
                contact_id=contact.id,
                assignment_id=assignment.id,
                detail="test_mode",
            )
            result["sent"] += 1
            await _schedule_followups(db, campaign, assignment)
            continue

        # Idempotency: one durable key per assignment. A retry after a crash
        # re-uses it, and the unique index on messages.idempotency_key means a
        # duplicate insert cannot produce a second SMS.
        if not assignment.idempotency_key:
            assignment.idempotency_key = f"ads-{campaign.id}-{assignment.id}-{uuid.uuid4().hex[:8]}"
        existing = (
            await db.execute(
                select(Message).where(Message.idempotency_key == assignment.idempotency_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            assignment.message_id = existing.id
            assignment.send_status = "sent" if existing.status in ("sent", "delivered") else "pending"
            continue

        conversation = (
            await db.execute(
                select(Conversation).where(Conversation.contact_id == contact.id).order_by(Conversation.id).limit(1)
            )
        ).scalars().first()
        if conversation is None:
            conversation = Conversation(contact_id=contact.id, status="active")
            db.add(conversation)
            await db.flush()

        from app.utils.phone import count_sms_segments

        char_count, segment_count = count_sms_segments(rendered)
        message = Message(
            conversation_id=conversation.id,
            contact_id=contact.id,
            direction="outgoing",
            body=rendered,
            segment_count=segment_count,
            char_count=char_count,
            status="queued",
            provider="smsgate",
            idempotency_key=assignment.idempotency_key,
        )
        db.add(message)
        await db.flush()

        conversation.message_count = (conversation.message_count or 0) + 1
        conversation.last_message_preview = rendered[:100]
        conversation.last_message_at = now_utc()

        assignment.message_id = message.id
        assignment.send_status = "sent"
        assignment.sent_at = now_utc()
        assignment.last_action_at = now_utc()
        contact.last_contacted_at = now_utc()

        record_event(
            db,
            "MESSAGE_SENT",
            campaign_id=campaign.id,
            set_id=assignment.set_id,
            creative_id=assignment.creative_id,
            creative_version_id=assignment.creative_version_id,
            contact_id=contact.id,
            assignment_id=assignment.id,
        )
        outbox.append(message.id)
        result["sent"] += 1
        await _schedule_followups(db, campaign, assignment)

    campaign.sent_count = (campaign.sent_count or 0) + result["sent"]
    campaign.last_activity_at = now_utc()
    campaign.last_state = "sending" if result["sent"] else campaign.last_state
    await db.commit()

    # Publish only after the rows are durable, exactly like the existing
    # campaign engine, so a worker can never outrun the transaction.
    if outbox:
        if send_inline:
            from app.tasks.sms_tasks import _send_one

            for mid in outbox:
                try:
                    await _send_one(mid, final_on_failure=True)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Ads inline send %s failed: %s", mid, exc)
        else:
            from app.tasks.queue import try_enqueue
            from app.tasks.sms_tasks import send_sms

            for mid in outbox:
                try_enqueue(send_sms, mid)

    result["state"] = await campaign_state(db, campaign)
    return result


# ==========================================================================
# Follow-up automation
# ==========================================================================


async def _schedule_followups(db: AsyncSession, campaign: AdsCampaign, assignment: AdsAssignment) -> None:
    """Create the pending follow-up tasks for a contact we just messaged."""
    steps = list(
        (
            await db.execute(
                select(AdsFollowUpStep)
                .where(
                    AdsFollowUpStep.campaign_id == campaign.id,
                    AdsFollowUpStep.is_active.is_(True),
                )
                .order_by(AdsFollowUpStep.step_order)
            )
        ).scalars().all()
    )
    if not steps:
        return
    cumulative = 0
    for step in steps:
        cumulative += max(int(step.wait_hours or 0), 0)
        exists = (
            await db.execute(
                select(AdsFollowUpTask.id).where(
                    AdsFollowUpTask.step_id == step.id,
                    AdsFollowUpTask.contact_id == assignment.contact_id,
                )
            )
        ).scalar_one_or_none()
        if exists:
            continue
        db.add(
            AdsFollowUpTask(
                campaign_id=campaign.id,
                step_id=step.id,
                assignment_id=assignment.id,
                contact_id=assignment.contact_id,
                due_at=now_utc() + timedelta(hours=cumulative),
                status="pending",
                body=step.body,
            )
        )
        record_event(
            db,
            "FOLLOW_UP_CREATED",
            campaign_id=campaign.id,
            contact_id=assignment.contact_id,
            assignment_id=assignment.id,
        )
    assignment.followup_status = "scheduled"


async def _has_reply_since(db: AsyncSession, contact_id: int, since: datetime | None) -> bool:
    query = select(func.count()).select_from(Message).where(
        Message.contact_id == contact_id, Message.direction == "incoming"
    )
    if since:
        query = query.where(Message.created_at >= as_utc(since))
    return ((await db.execute(query)).scalar() or 0) > 0


async def _condition_met(db: AsyncSession, step: AdsFollowUpStep, assignment: AdsAssignment | None, contact: Contact) -> bool:
    cond = (step.condition or "no_reply").lower()
    since = assignment.sent_at if assignment else None
    if cond == "always":
        return True
    replied = await _has_reply_since(db, contact.id, since)
    if cond == "no_reply":
        return not replied
    if cond == "replied":
        return replied
    if cond == "positive":
        return (assignment.reply_status if assignment else None) == "positive"
    if cond == "negative":
        return (assignment.reply_status if assignment else None) == "negative"
    if cond == "interested":
        return (contact.lead_status or "").lower() == "interested"
    if cond == "not_interested":
        return (contact.lead_status or "").lower() == "not_interested"
    if cond == "converted":
        return (contact.lead_status or "").lower() in ("converted", "customer")
    if cond == "meeting_scheduled":
        count = (
            await db.execute(
                select(func.count()).select_from(AdsCalendarEvent).where(
                    AdsCalendarEvent.contact_id == contact.id,
                    AdsCalendarEvent.event_type == "meeting",
                    AdsCalendarEvent.status == "scheduled",
                )
            )
        ).scalar() or 0
        return count > 0
    return True


async def process_followups(db: AsyncSession, *, limit: int = 50, send_inline: bool = False) -> dict:
    """Execute due follow-up tasks. Stop conditions are enforced here."""
    totals = {"sent": 0, "cancelled": 0, "completed": 0}
    tasks = list(
        (
            await db.execute(
                select(AdsFollowUpTask)
                .where(AdsFollowUpTask.status == "pending", AdsFollowUpTask.due_at <= now_utc())
                .order_by(AdsFollowUpTask.due_at)
                .limit(limit)
            )
        ).scalars().all()
    )
    outbox: list[int] = []
    for task in tasks:
        campaign = (
            await db.execute(select(AdsCampaign).where(AdsCampaign.id == task.campaign_id))
        ).scalar_one_or_none()
        if campaign is None or campaign.status in ("paused", "archived", "draft"):
            continue
        contact = (
            await db.execute(select(Contact).where(Contact.id == task.contact_id))
        ).scalar_one_or_none()
        if contact is None:
            task.status = "cancelled"
            task.note = "Contact removed"
            totals["cancelled"] += 1
            continue

        # Automatic stop conditions.
        if contact.is_opted_out or await _suppressed_numbers(db, [contact.phone_number]):
            task.status = "cancelled"
            task.note = "Contact opted out / suppressed"
            totals["cancelled"] += 1
            continue

        step = (
            await db.execute(select(AdsFollowUpStep).where(AdsFollowUpStep.id == task.step_id))
        ).scalar_one_or_none()
        assignment = (
            await db.execute(select(AdsAssignment).where(AdsAssignment.id == task.assignment_id))
        ).scalar_one_or_none() if task.assignment_id else None

        if step is None:
            # Manual task: leave for the human, just mark it overdue-visible.
            continue
        if not step.is_active:
            task.status = "cancelled"
            task.note = "Step disabled"
            totals["cancelled"] += 1
            continue
        if not await _condition_met(db, step, assignment, contact):
            task.status = "cancelled"
            task.note = f"Condition '{step.condition}' not met"
            totals["cancelled"] += 1
            continue

        action = (step.action or "send_sms").lower()
        if action == "send_sms" and (step.body or task.body):
            body = step.body or task.body or ""
            mid = await send_adhoc_sms(db, contact, body, test_mode=campaign.test_mode)
            if mid:
                outbox.append(mid)
            task.status = "completed"
            task.completed_at = now_utc()
            totals["sent"] += 1
        elif action == "add_tag" and step.action_value:
            await add_tag(db, contact.id, step.action_value)
            task.status = "completed"
            totals["completed"] += 1
        elif action == "remove_tag" and step.action_value:
            await remove_tag(db, contact.id, step.action_value)
            task.status = "completed"
            totals["completed"] += 1
        elif action == "change_status" and step.action_value:
            contact.lead_status = step.action_value
            task.status = "completed"
            totals["completed"] += 1
        elif action == "suppress":
            await suppress_contact(db, contact, reason="Automation")
            task.status = "completed"
            totals["completed"] += 1
        elif action == "stop":
            await db.execute(
                update(AdsFollowUpTask)
                .where(
                    AdsFollowUpTask.contact_id == contact.id,
                    AdsFollowUpTask.campaign_id == campaign.id,
                    AdsFollowUpTask.status == "pending",
                )
                .values(status="cancelled", note="Workflow stopped")
            )
            task.status = "completed"
            totals["completed"] += 1
        else:
            task.status = "completed"
            totals["completed"] += 1
        task.completed_at = task.completed_at or now_utc()

    await db.commit()

    if outbox:
        if send_inline:
            from app.tasks.sms_tasks import _send_one

            for mid in outbox:
                try:
                    await _send_one(mid, final_on_failure=True)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Ads follow-up send %s failed: %s", mid, exc)
        else:
            from app.tasks.queue import try_enqueue
            from app.tasks.sms_tasks import send_sms

            for mid in outbox:
                try_enqueue(send_sms, mid)
    return totals


async def send_adhoc_sms(db: AsyncSession, contact: Contact, body: str, *, test_mode: bool = False) -> int | None:
    """Queue one personalised SMS through the existing message pipeline."""
    if test_mode:
        return None
    from app.services.variable_service import render_for_contact
    from app.utils.phone import count_sms_segments

    rendered = await render_for_contact(db, body, contact)
    conversation = (
        await db.execute(
            select(Conversation).where(Conversation.contact_id == contact.id).order_by(Conversation.id).limit(1)
        )
    ).scalars().first()
    if conversation is None:
        conversation = Conversation(contact_id=contact.id, status="active")
        db.add(conversation)
        await db.flush()
    char_count, segment_count = count_sms_segments(rendered)
    message = Message(
        conversation_id=conversation.id,
        contact_id=contact.id,
        direction="outgoing",
        body=rendered,
        segment_count=segment_count,
        char_count=char_count,
        status="queued",
        provider="smsgate",
        idempotency_key=f"ads-fu-{contact.id}-{uuid.uuid4().hex[:10]}",
    )
    db.add(message)
    await db.flush()
    conversation.message_count = (conversation.message_count or 0) + 1
    conversation.last_message_preview = rendered[:100]
    conversation.last_message_at = now_utc()
    contact.last_contacted_at = now_utc()
    return message.id


# ==========================================================================
# CRM helpers (reuse the existing tag + suppression tables)
# ==========================================================================


async def add_tag(db: AsyncSession, contact_id: int, name: str) -> None:
    name = name.strip()
    if not name:
        return
    tag = (
        await db.execute(select(Tag).where(func.lower(Tag.name) == name.lower()))
    ).scalar_one_or_none()
    if tag is None:
        tag = Tag(name=name)
        db.add(tag)
        await db.flush()
    exists = (
        await db.execute(
            select(ContactTag.id).where(ContactTag.contact_id == contact_id, ContactTag.tag_id == tag.id)
        )
    ).scalar_one_or_none()
    if not exists:
        db.add(ContactTag(contact_id=contact_id, tag_id=tag.id))


async def remove_tag(db: AsyncSession, contact_id: int, name: str) -> None:
    tag = (
        await db.execute(select(Tag).where(func.lower(Tag.name) == name.strip().lower()))
    ).scalar_one_or_none()
    if tag is None:
        return
    row = (
        await db.execute(
            select(ContactTag).where(ContactTag.contact_id == contact_id, ContactTag.tag_id == tag.id)
        )
    ).scalar_one_or_none()
    if row:
        await db.delete(row)


async def suppress_contact(db: AsyncSession, contact: Contact, *, reason: str = "Manual", source: str = "manual") -> None:
    """Add to the EXISTING global suppression list and stop future automation."""
    number = normalize(contact.phone_number) or contact.phone_number
    exists = (
        await db.execute(select(SuppressionEntry).where(SuppressionEntry.phone_number == number))
    ).scalar_one_or_none()
    if exists is None:
        db.add(
            SuppressionEntry(
                phone_number=number,
                contact_id=contact.id,
                reason=reason,
                source=source,
            )
        )
    contact.is_opted_out = True
    contact.opted_out_at = now_utc()
    contact.opt_out_reason = reason
    # Cancel everything still pending for this contact across the ads manager.
    await db.execute(
        update(AdsAssignment)
        .where(AdsAssignment.contact_id == contact.id, AdsAssignment.send_status == "pending")
        .values(send_status="skipped", skip_reason="suppressed")
    )
    await db.execute(
        update(AdsFollowUpTask)
        .where(AdsFollowUpTask.contact_id == contact.id, AdsFollowUpTask.status == "pending")
        .values(status="cancelled", note="Contact suppressed")
    )
    record_event(db, "CONTACT_OPTED_OUT", contact_id=contact.id, detail=reason)


# ==========================================================================
# Reply association + classification
# ==========================================================================

POSITIVE_WORDS = ("yes", "interested", "sure", "ok", "okay", "send", "please", "how much", "info")
NEGATIVE_WORDS = ("no", "not interested", "stop", "remove", "never", "don't", "dont")
MEETING_WORDS = ("meet", "call me", "schedule", "appointment", "visit")


def classify_reply(text: str) -> str:
    body = (text or "").strip().lower()
    if not body:
        return "other"
    if any(w in body for w in MEETING_WORDS):
        return "meeting_request"
    if any(body.startswith(w) or w in body for w in NEGATIVE_WORDS):
        return "negative"
    if any(w in body for w in POSITIVE_WORDS):
        return "positive"
    if body.endswith("?"):
        return "question"
    return "other"


async def associate_reply(db: AsyncSession, contact_id: int, body: str) -> Optional[AdsAssignment]:
    """Attach an inbound reply to the most recent ads assignment."""
    assignment = (
        await db.execute(
            select(AdsAssignment)
            .where(AdsAssignment.contact_id == contact_id, AdsAssignment.sent_at.is_not(None))
            .order_by(AdsAssignment.sent_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if assignment is None:
        return None
    classification = classify_reply(body)
    assignment.reply_status = classification
    assignment.last_action_at = now_utc()
    record_event(
        db,
        "REPLY_RECEIVED",
        campaign_id=assignment.campaign_id,
        set_id=assignment.set_id,
        creative_id=assignment.creative_id,
        creative_version_id=assignment.creative_version_id,
        contact_id=contact_id,
        assignment_id=assignment.id,
        detail=classification,
    )
    record_event(
        db,
        "REPLY_CLASSIFIED",
        campaign_id=assignment.campaign_id,
        contact_id=contact_id,
        assignment_id=assignment.id,
        detail=classification,
    )
    return assignment


# ==========================================================================
# Analytics
# ==========================================================================


def _rate(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


async def sync_delivery(db: AsyncSession, campaign_id: int | None = None) -> int:
    """Copy provider outcomes from ``messages`` onto the assignments.

    The gateway result lands on the Message row (shared pipeline). Rather than
    duplicating that bookkeeping in a second place -- which is how counters
    drift -- the ads layer reads it back here.
    """
    query = select(AdsAssignment).where(AdsAssignment.message_id.is_not(None))
    if campaign_id is not None:
        query = query.where(AdsAssignment.campaign_id == campaign_id)
    rows = list((await db.execute(query)).scalars().all())
    if not rows:
        return 0
    messages = {
        m.id: m
        for m in (
            await db.execute(select(Message).where(Message.id.in_([r.message_id for r in rows])))
        ).scalars().all()
    }
    changed = 0
    for assignment in rows:
        message = messages.get(assignment.message_id)
        if message is None:
            continue
        if message.status == "delivered" and assignment.delivery_status != "delivered":
            assignment.delivery_status = "delivered"
            assignment.send_status = "delivered"
            record_event(
                db,
                "MESSAGE_DELIVERED",
                campaign_id=assignment.campaign_id,
                creative_id=assignment.creative_id,
                contact_id=assignment.contact_id,
                assignment_id=assignment.id,
            )
            changed += 1
        elif message.status == "failed" and assignment.send_status != "failed":
            assignment.send_status = "failed"
            assignment.delivery_status = "failed"
            assignment.last_error = message.last_error
            record_event(
                db,
                "MESSAGE_FAILED",
                campaign_id=assignment.campaign_id,
                creative_id=assignment.creative_id,
                contact_id=assignment.contact_id,
                assignment_id=assignment.id,
                detail=message.last_error,
            )
            changed += 1
        elif message.status == "sent" and assignment.send_status == "sending":
            assignment.send_status = "sent"
            changed += 1
    if changed:
        await db.flush()
    return changed


async def _counts_for(db: AsyncSession, where) -> dict:
    rows = list(
        (await db.execute(select(AdsAssignment).where(*where))).scalars().all()
    )
    sent = sum(1 for r in rows if r.sent_at is not None)
    delivered = sum(1 for r in rows if (r.delivery_status or "") in ("delivered", "simulated"))
    failed = sum(1 for r in rows if r.send_status == "failed")
    pending = sum(1 for r in rows if r.send_status == "pending")
    skipped = sum(1 for r in rows if r.send_status == "skipped")
    replies = sum(1 for r in rows if r.reply_status)
    positive = sum(1 for r in rows if r.reply_status == "positive")
    negative = sum(1 for r in rows if r.reply_status == "negative")
    conversions = sum(1 for r in rows if r.conversion_status == "converted")
    opt_outs = sum(1 for r in rows if r.skip_reason in ("opted_out", "suppressed"))
    return {
        "assigned": len(rows),
        "sent": sent,
        "delivered": delivered,
        "failed": failed,
        "pending": pending,
        "skipped": skipped,
        "replies": replies,
        "positive_replies": positive,
        "negative_replies": negative,
        "conversions": conversions,
        "opt_outs": opt_outs,
        "delivery_rate": _rate(delivered, sent),
        "reply_rate": _rate(replies, sent),
        "positive_reply_rate": _rate(positive, sent),
        "negative_reply_rate": _rate(negative, sent),
        "conversion_rate": _rate(conversions, sent),
        "opt_out_rate": _rate(opt_outs, sent),
        "failure_rate": _rate(failed, sent),
    }


def performance_score(objective: str, stats: dict) -> float:
    """A single comparable number, weighted by the campaign's objective.

    Raw metrics are always returned alongside it -- this never hides data.
    """
    obj = (objective or "replies").lower()
    reply = stats["reply_rate"]
    positive = stats["positive_reply_rate"]
    conv = stats["conversion_rate"]
    delivery = stats["delivery_rate"]
    penalty = stats["opt_out_rate"] * 2
    if obj == "meetings":
        score = conv * 3 + positive * 2 + reply
    elif obj in ("leads", "replies"):
        score = positive * 3 + reply * 2 + delivery * 0.2
    elif obj in ("promotion", "website_visits"):
        score = conv * 2 + reply + delivery * 0.5
    else:
        score = reply * 2 + positive * 2 + delivery * 0.3
    return round(max(score - penalty, 0), 1)


async def campaign_analytics(db: AsyncSession, campaign: AdsCampaign) -> dict:
    stats = await _counts_for(db, [AdsAssignment.campaign_id == campaign.id])
    stats["credits_used"] = stats["sent"]
    stats["score"] = performance_score(campaign.objective, stats)

    followups_due = (
        await db.execute(
            select(func.count()).select_from(AdsFollowUpTask).where(
                AdsFollowUpTask.campaign_id == campaign.id, AdsFollowUpTask.status == "pending"
            )
        )
    ).scalar() or 0
    meetings = (
        await db.execute(
            select(func.count()).select_from(AdsCalendarEvent).where(
                AdsCalendarEvent.campaign_id == campaign.id,
                AdsCalendarEvent.event_type == "meeting",
            )
        )
    ).scalar() or 0
    stats["followups_due"] = followups_due
    stats["meetings"] = meetings
    stats["meeting_rate"] = _rate(meetings, stats["sent"])
    stats["state"] = await campaign_state(db, campaign)

    sets = []
    for ads_set in (
        await db.execute(select(AdsSet).where(AdsSet.campaign_id == campaign.id))
    ).scalars().all():
        s = await _counts_for(db, [AdsAssignment.set_id == ads_set.id])
        s["score"] = performance_score(campaign.objective, s)
        sets.append({"id": ads_set.id, "name": ads_set.name, "status": ads_set.status, **s})

    creatives = []
    for creative in (
        await db.execute(
            select(AdsCreative).where(AdsCreative.campaign_id == campaign.id, AdsCreative.is_deleted.is_(False))
        )
    ).scalars().all():
        c = await _counts_for(db, [AdsAssignment.creative_id == creative.id])
        c["score"] = performance_score(campaign.objective, c)
        creatives.append(
            {
                "id": creative.id,
                "name": creative.name,
                "set_id": creative.set_id,
                "status": creative.status,
                "version": creative.current_version,
                "body": creative.body,
                **c,
            }
        )

    alerts = build_alerts(stats, creatives)
    return {"campaign": stats, "sets": sets, "creatives": creatives, "alerts": alerts}


def build_alerts(stats: dict, creatives: list[dict]) -> list[dict]:
    alerts: list[dict] = []
    if stats["sent"] >= 20 and stats["opt_out_rate"] > 5:
        alerts.append({"level": "warning", "message": f"Opt-out rate is high ({stats['opt_out_rate']}%)."})
    if stats["sent"] >= 20 and stats["failure_rate"] > 20:
        alerts.append({"level": "warning", "message": f"Failure rate is high ({stats['failure_rate']}%)."})
    if stats["pending"] == 0 and stats["assigned"] > 0:
        alerts.append({"level": "info", "message": "No eligible contacts remain in this campaign."})
    live = [c for c in creatives if c["sent"] >= 20]
    if len(live) > 1:
        best = max(live, key=lambda c: c["score"])
        worst = min(live, key=lambda c: c["score"])
        if best["score"] > worst["score"] * 1.5:
            alerts.append(
                {
                    "level": "info",
                    "message": f"{best['name']} is outperforming {worst['name']}. Consider promoting the winner.",
                }
            )
        if worst["opt_out_rate"] > best["opt_out_rate"] + 5:
            alerts.append(
                {"level": "warning", "message": f"{worst['name']} has a much higher opt-out rate."}
            )
    return alerts


# ==========================================================================
# Simulation + pre-launch validation
# ==========================================================================


async def simulate(db: AsyncSession, campaign: AdsCampaign) -> dict:
    """Show what WOULD happen. Touches nothing, sends nothing, costs nothing."""
    sets = list(
        (await db.execute(select(AdsSet).where(AdsSet.campaign_id == campaign.id))).scalars().all()
    )
    per_set = []
    total_matched = total_eligible = 0
    skipped_totals: dict[str, int] = {}
    for ads_set in sets:
        contacts = await resolve_audience(db, ads_set)
        eligible, skipped = await screen_contacts(db, campaign, contacts)
        plan = await assign_contacts(db, campaign, ads_set, eligible, dry_run=True)
        creatives = active_creatives(
            list(
                (
                    await db.execute(
                        select(AdsCreative).where(
                            AdsCreative.set_id == ads_set.id, AdsCreative.is_deleted.is_(False)
                        ).order_by(AdsCreative.id)
                    )
                ).scalars().all()
            )
        )
        names = {c.id: c.name for c in creatives}
        per_set.append(
            {
                "set_id": ads_set.id,
                "set_name": ads_set.name,
                "matched": len(contacts),
                "eligible": len(eligible),
                "split": [
                    {"creative_id": cid, "name": names.get(cid, str(cid)), "contacts": n}
                    for cid, n in plan["per_creative"].items()
                ],
                "skipped": skipped,
            }
        )
        total_matched += len(contacts)
        total_eligible += len(eligible)
        for k, v in skipped.items():
            skipped_totals[k] = skipped_totals.get(k, 0) + v

    pending = (
        await db.execute(
            select(func.count()).select_from(AdsAssignment).where(
                AdsAssignment.campaign_id == campaign.id, AdsAssignment.send_status == "pending"
            )
        )
    ).scalar() or 0
    to_send = total_eligible + pending
    daily = campaign.daily_limit or 0
    duration = (to_send + daily - 1) // daily if daily else (1 if to_send else 0)
    steps = (
        await db.execute(
            select(func.count()).select_from(AdsFollowUpStep).where(
                AdsFollowUpStep.campaign_id == campaign.id, AdsFollowUpStep.is_active.is_(True)
            )
        )
    ).scalar() or 0

    return {
        "audience": total_matched,
        "eligible": total_eligible,
        "already_queued": pending,
        "skipped": skipped_totals,
        "per_set": per_set,
        "daily_limit": campaign.daily_limit,
        "total_limit": campaign.total_limit,
        "estimated_days": duration,
        "drip": {
            "mode": campaign.drip_mode,
            "batch": campaign.drip_batch_size,
            "interval_minutes": campaign.drip_interval_minutes,
            "pacing": campaign.pacing,
        },
        "followup_steps": steps,
        "test_mode": campaign.test_mode,
    }


async def validate_campaign(db: AsyncSession, campaign: AdsCampaign) -> dict:
    """Pre-launch checks. Returns {ok, errors[], warnings[], summary}."""
    errors: list[str] = []
    warnings: list[str] = []

    if not (campaign.name or "").strip():
        errors.append("Campaign needs a name")
    if not campaign.objective:
        warnings.append("No objective selected; metrics will not be prioritised")

    sets = list(
        (
            await db.execute(
                select(AdsSet).where(AdsSet.campaign_id == campaign.id, AdsSet.status == "active")
            )
        ).scalars().all()
    )
    if not sets:
        errors.append("Add at least one active SMS set")

    creative_count = 0
    for ads_set in sets:
        creatives = active_creatives(
            list(
                (
                    await db.execute(
                        select(AdsCreative).where(AdsCreative.set_id == ads_set.id)
                    )
                ).scalars().all()
            )
        )
        creative_count += len(creatives)
        if not creatives:
            errors.append(f"SMS set '{ads_set.name}' has no active creative")
        for creative in creatives:
            if not (creative.body or "").strip():
                errors.append(f"Creative '{creative.name}' has no message text")
        if (ads_set.split_mode or "") == "percentage" and creatives:
            total = round(sum(c.allocation or 0 for c in creatives), 2)
            if abs(total - 100) > 0.01:
                errors.append(
                    f"SMS set '{ads_set.name}' percentage split totals {total}%, must be 100%"
                )

    sim = await simulate(db, campaign)
    if sim["eligible"] + sim["already_queued"] == 0:
        errors.append("No eligible contacts. Widen the audience or clear exclusions.")

    if not campaign.test_mode:
        from app.config import settings

        if not getattr(settings, "smsgate_configured", False):
            warnings.append("No SMS gateway configured; messages will fail until one is set up.")

    if campaign.daily_limit is None:
        warnings.append("No daily SMS limit set: the whole audience may go out at once.")

    return {"ok": not errors, "errors": errors, "warnings": warnings, "summary": sim}


# ==========================================================================
# Lifecycle
# ==========================================================================


async def launch(db: AsyncSession, campaign: AdsCampaign, *, actor: str = "user") -> dict:
    check = await validate_campaign(db, campaign)
    if not check["ok"]:
        return {"ok": False, "errors": check["errors"], "warnings": check["warnings"]}
    built = await build_audience(db, campaign)
    campaign.status = "scheduled" if campaign.start_date and as_utc(campaign.start_date) > now_utc() else "active"
    campaign.last_state = "preparing_audience"
    campaign.last_activity_at = now_utc()
    log_activity(
        db,
        "campaign_launched",
        campaign_id=campaign.id,
        actor=actor,
        detail=f"{built['added']} contacts assigned",
    )
    await db.commit()
    return {"ok": True, "status": campaign.status, "audience": built, "warnings": check["warnings"]}


async def refresh_always_on(db: AsyncSession) -> int:
    """Pull newly-matching contacts into always-on campaigns."""
    campaigns = list(
        (
            await db.execute(
                select(AdsCampaign).where(
                    AdsCampaign.status == "active", AdsCampaign.always_on.is_(True)
                )
            )
        ).scalars().all()
    )
    added = 0
    for campaign in campaigns:
        result = await build_audience(db, campaign)
        added += result["added"]
    if added:
        await db.commit()
    return added


async def duplicate_campaign(
    db: AsyncSession, campaign: AdsCampaign, *, copy_audience: bool = False, name: str | None = None
) -> AdsCampaign:
    clone = AdsCampaign(
        name=(name or f"{campaign.name} (Copy)")[:255],
        description=campaign.description,
        objective=campaign.objective,
        status="draft",
        daily_limit=campaign.daily_limit,
        total_limit=campaign.total_limit,
        send_start_hour=campaign.send_start_hour,
        send_end_hour=campaign.send_end_hour,
        send_days=campaign.send_days,
        timezone_name=campaign.timezone_name,
        drip_mode=campaign.drip_mode,
        drip_batch_size=campaign.drip_batch_size,
        drip_interval_minutes=campaign.drip_interval_minutes,
        pacing=campaign.pacing,
        continuous=campaign.continuous,
        always_on=campaign.always_on,
        max_per_contact_per_day=campaign.max_per_contact_per_day,
        max_per_contact_per_week=campaign.max_per_contact_per_week,
        optimization_mode=campaign.optimization_mode,
        queued_edit_policy=campaign.queued_edit_policy,
        priority=campaign.priority,
        test_mode=campaign.test_mode,
    )
    db.add(clone)
    await db.flush()

    for ads_set in (
        await db.execute(select(AdsSet).where(AdsSet.campaign_id == campaign.id))
    ).scalars().all():
        new_set = AdsSet(
            campaign_id=clone.id,
            name=ads_set.name,
            status=ads_set.status,
            list_ids=ads_set.list_ids if copy_audience else None,
            include_tags=ads_set.include_tags if copy_audience else None,
            exclude_tags=ads_set.exclude_tags,
            include_statuses=ads_set.include_statuses,
            exclude_statuses=ads_set.exclude_statuses,
            city=ads_set.city if copy_audience else None,
            state=ads_set.state if copy_audience else None,
            industry=ads_set.industry if copy_audience else None,
            activity_filter=ads_set.activity_filter,
            exclude_campaign_ids=ads_set.exclude_campaign_ids,
            daily_limit=ads_set.daily_limit,
            split_mode=ads_set.split_mode,
        )
        db.add(new_set)
        await db.flush()
        for creative in (
            await db.execute(
                select(AdsCreative).where(
                    AdsCreative.set_id == ads_set.id, AdsCreative.is_deleted.is_(False)
                )
            )
        ).scalars().all():
            new_creative = AdsCreative(
                set_id=new_set.id,
                campaign_id=clone.id,
                name=creative.name,
                status=creative.status,
                body=creative.body,
                cta=creative.cta,
                tracking_link=creative.tracking_link,
                allocation=creative.allocation,
                current_version=1,
            )
            db.add(new_creative)
            await db.flush()
            await ensure_version(db, new_creative)

    for step in (
        await db.execute(select(AdsFollowUpStep).where(AdsFollowUpStep.campaign_id == campaign.id))
    ).scalars().all():
        db.add(
            AdsFollowUpStep(
                campaign_id=clone.id,
                step_order=step.step_order,
                name=step.name,
                wait_hours=step.wait_hours,
                condition=step.condition,
                action=step.action,
                body=step.body,
                action_value=step.action_value,
                is_active=step.is_active,
            )
        )

    log_activity(db, "campaign_duplicated", campaign_id=clone.id, detail=f"Copied from #{campaign.id}")
    await db.flush()
    return clone
