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
    AdsAudience,
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
    "OPTIMIZATION_RUN",
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


def _csv_ids(raw: str | list[int] | None) -> list[int]:
    """Parse a CSV id list (or pass through a list) into ints."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [int(x) for x in raw]
    return [int(x) for x in str(raw).split(",") if x.strip().isdigit()]


def _csv_names(raw: str | list[str] | None) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [p.strip() for p in str(raw).split(",") if p.strip()]


async def resolve_contacts_for_filters(
    db: AsyncSession,
    *,
    list_ids: str | list[int] | None = None,
    contact_ids: str | list[int] | None = None,
    include_tags: str | list[str] | None = None,
    exclude_tags: str | list[str] | None = None,
    include_statuses: str | list[str] | None = None,
    exclude_statuses: str | list[str] | None = None,
    city: str | None = None,
    state: str | None = None,
    industry: str | None = None,
    activity_filter: str | None = None,
    exclude_campaign_ids: str | list[int] | None = None,
) -> list[Contact]:
    """Return contacts matching a targeting definition, before eligibility.

    Shared by SMS sets and saved audiences. Reuses the existing contacts /
    lists / tags tables -- nothing is copied.

    IMPORTANT: no targeting means NO contacts. An empty definition (no lists,
    no contacts, no tags, no geo/status/activity filters) matches zero
    contacts -- never the whole database. A set only fills up once the user
    explicitly adds at least one list, contact, audience or filter.
    """
    lists = _csv_ids(list_ids)
    explicit = _csv_ids(contact_ids)
    inc_tags = _csv_names(include_tags)
    city = (city or "").strip() or None
    state = (state or "").strip() or None
    industry = (industry or "").strip() or None
    inc_statuses = [s.lower() for s in _csv_names(include_statuses)]
    activity = (activity_filter or "any").strip().lower()

    has_targeting = bool(
        lists
        or explicit
        or inc_tags
        or city
        or state
        or industry
        or inc_statuses
        or activity not in ("", "any")
    )
    if not has_targeting:
        return []

    if lists or explicit:
        # Base = union of list members and explicitly picked contacts.
        base_ids: set[int] = set(explicit)
        if lists:
            member_ids = (
                await db.execute(
                    select(ContactListMember.contact_id).where(
                        ContactListMember.list_id.in_(lists)
                    )
                )
            ).scalars().all()
            base_ids.update(member_ids)
        if not base_ids:
            # Lists selected but currently empty (or contacts since deleted).
            return []
        query = select(Contact).where(Contact.id.in_(sorted(base_ids)))
    else:
        # Filter-only targeting (e.g. a city on its own) starts from everyone.
        query = select(Contact)

    if city:
        query = query.where(func.lower(Contact.city) == city.lower())
    if state:
        query = query.where(func.lower(Contact.state) == state.lower())
    if industry:
        query = query.where(func.lower(Contact.industry) == industry.lower())

    if inc_statuses:
        query = query.where(func.lower(Contact.lead_status).in_(inc_statuses))
    exc_statuses = [s.lower() for s in _csv_names(exclude_statuses)]
    if exc_statuses:
        query = query.where(func.lower(Contact.lead_status).notin_(exc_statuses))

    if activity == "never_contacted":
        query = query.where(Contact.last_contacted_at.is_(None))
    elif activity == "contacted":
        query = query.where(Contact.last_contacted_at.is_not(None))
    elif activity == "replied":
        query = query.where(Contact.last_reply_at.is_not(None))
    elif activity == "not_replied":
        query = query.where(Contact.last_reply_at.is_(None))

    contacts = list((await db.execute(query)).scalars().all())

    if inc_tags:
        keep = await _tagged_contact_ids(db, inc_tags)
        contacts = [c for c in contacts if c.id in keep]
    exc_tags = _csv_names(exclude_tags)
    if exc_tags:
        drop = await _tagged_contact_ids(db, exc_tags)
        contacts = [c for c in contacts if c.id not in drop]

    exclude_campaigns = _csv_ids(exclude_campaign_ids)
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


async def _merged_set_targeting(db: AsyncSession, ads_set: AdsSet) -> dict:
    """Merge a set's own targeting with its linked saved audience (if any).

    Lists and contacts are unioned; include/exclude tag and status lists are
    unioned; scalar geo filters prefer the set's own value, falling back to
    the audience's.
    """
    merged: dict = {
        "list_ids": ads_set.csv("list_ids"),
        "contact_ids": ads_set.csv("contact_ids"),
        "include_tags": ads_set.csv("include_tags"),
        "exclude_tags": ads_set.csv("exclude_tags"),
        "include_statuses": ads_set.csv("include_statuses"),
        "exclude_statuses": ads_set.csv("exclude_statuses"),
        "city": (ads_set.city or "").strip() or None,
        "state": (ads_set.state or "").strip() or None,
        "industry": (ads_set.industry or "").strip() or None,
        "activity_filter": (ads_set.activity_filter or "").strip() or None,
        "exclude_campaign_ids": ads_set.csv("exclude_campaign_ids"),
    }
    audience_id = getattr(ads_set, "audience_id", None)
    if audience_id:
        audience = (
            await db.execute(select(AdsAudience).where(AdsAudience.id == audience_id))
        ).scalar_one_or_none()
        if audience is not None:
            for key in (
                "list_ids",
                "contact_ids",
                "include_tags",
                "exclude_tags",
                "include_statuses",
                "exclude_statuses",
                "exclude_campaign_ids",
            ):
                merged[key] = sorted(set(merged[key]) | set(audience.csv(key)), key=str)
            for key in ("city", "state", "industry", "activity_filter"):
                if not merged[key]:
                    merged[key] = (getattr(audience, key) or "").strip() or None
    return merged


async def resolve_audience(db: AsyncSession, ads_set: AdsSet) -> list[Contact]:
    """Return the contacts this SMS Set targets, before eligibility checks."""
    merged = await _merged_set_targeting(db, ads_set)
    return await resolve_contacts_for_filters(db, **merged)


async def resolve_audience_contacts(db: AsyncSession, audience: AdsAudience) -> list[Contact]:
    """Return the contacts a saved audience currently matches."""
    return await resolve_contacts_for_filters(
        db,
        list_ids=audience.csv("list_ids"),
        contact_ids=audience.csv("contact_ids"),
        include_tags=audience.csv("include_tags"),
        exclude_tags=audience.csv("exclude_tags"),
        include_statuses=audience.csv("include_statuses"),
        exclude_statuses=audience.csv("exclude_statuses"),
        city=audience.city,
        state=audience.state,
        industry=audience.industry,
        activity_filter=audience.activity_filter,
        exclude_campaign_ids=audience.csv("exclude_campaign_ids"),
    )


async def preview_filters(
    db: AsyncSession,
    filters: dict,
    campaign: AdsCampaign | None = None,
    *,
    sample_size: int = 10,
) -> dict:
    """Live size estimate for set/audience editors. Touches nothing."""
    # A linked saved audience merges in exactly like a set's does.
    audience_id = filters.get("audience_id")
    if audience_id:
        audience = (
            await db.execute(select(AdsAudience).where(AdsAudience.id == audience_id))
        ).scalar_one_or_none()
        if audience is not None:
            merged = dict(filters)
            for key in (
                "list_ids",
                "contact_ids",
                "include_tags",
                "exclude_tags",
                "include_statuses",
                "exclude_statuses",
                "exclude_campaign_ids",
            ):
                merged[key] = sorted(
                    set(_csv_names(filters.get(key))) | set(audience.csv(key)), key=str
                )
            for key in ("city", "state", "industry", "activity_filter"):
                merged[key] = (filters.get(key) or "").strip() or (getattr(audience, key) or None)
            filters = merged
    contacts = await resolve_contacts_for_filters(db, **filters)
    eligible, skipped = await screen_contacts(db, campaign, contacts)
    return {
        "matched": len(contacts),
        "eligible": len(eligible),
        "skipped": skipped,
        "has_targeting": bool(contacts) or True,  # matched==0 with targeting is still valid
        "sample": [
            {
                "id": c.id,
                "name": " ".join(filter(None, [c.first_name, c.last_name])).strip()
                or c.business_name,
                "phone_number": c.phone_number,
            }
            for c in eligible[:sample_size]
        ],
    }


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
    db: AsyncSession, campaign: AdsCampaign | None, contacts: list[Contact]
) -> tuple[list[Contact], dict[str, int]]:
    """Apply eligibility rules. Returns (eligible, {skip_reason: count}).

    ``campaign`` may be None for standalone audience previews, in which case
    the already-in-this-campaign check is skipped.
    """
    counts: dict[str, int] = {}

    def bump(reason: str) -> None:
        counts[reason] = counts.get(reason, 0) + 1

    existing: set[int] = set()
    if campaign is not None:
        existing = set(
            (
                await db.execute(
                    select(AdsAssignment.contact_id).where(
                        AdsAssignment.campaign_id == campaign.id
                    )
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


async def _counts_for(
    db: AsyncSession,
    where,
    *,
    campaign_id: int | None = None,
    set_id: int | None = None,
    creative_id: int | None = None,
) -> dict:
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
    reply_contacts = {r.contact_id for r in rows if r.reply_status}

    # Link clicks come from the append-only event stream (recorded by the
    # click-tracking redirect and the manual track endpoint).
    event_query = select(AdsEvent).where(AdsEvent.event_type == "LINK_CLICKED")
    if campaign_id is not None:
        event_query = event_query.where(AdsEvent.campaign_id == campaign_id)
    if set_id is not None:
        event_query = event_query.where(AdsEvent.set_id == set_id)
    if creative_id is not None:
        event_query = event_query.where(AdsEvent.creative_id == creative_id)
    click_events = list((await db.execute(event_query)).scalars().all())
    clicks = len(click_events)
    click_contacts = {e.contact_id for e in click_events if e.contact_id}

    # SMS has no pixel-based "open" signal. A contact that replied or tapped
    # the link demonstrably opened the message, so opens = distinct contacts
    # who replied OR clicked. Honest, auditable, and it grows as tracking is
    # used rather than pretending to know what it cannot.
    opens = len(reply_contacts | click_contacts)
    open_base = delivered or sent
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
        "clicks": clicks,
        "opens": opens,
        "delivery_rate": _rate(delivered, sent),
        "reply_rate": _rate(replies, sent),
        "positive_reply_rate": _rate(positive, sent),
        "negative_reply_rate": _rate(negative, sent),
        "conversion_rate": _rate(conversions, sent),
        "opt_out_rate": _rate(opt_outs, sent),
        "failure_rate": _rate(failed, sent),
        "click_rate": _rate(clicks, sent),
        "open_rate": _rate(opens, open_base),
        "engagement_rate": _rate(replies + clicks, sent),
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
    click = stats.get("click_rate", 0) or 0
    if obj == "meetings":
        score = conv * 3 + positive * 2 + reply
    elif obj in ("leads", "replies"):
        score = positive * 3 + reply * 2 + delivery * 0.2
    elif obj in ("promotion", "website_visits"):
        score = conv * 2 + reply + click * 2 + delivery * 0.5
    else:
        score = reply * 2 + positive * 2 + click + delivery * 0.3
    return round(max(score - penalty, 0), 1)


#: Sends needed before the UI crowns a visible winner. The automatic
#: optimizer uses the stricter per-campaign ``optimize_min_sends``.
DISPLAY_WINNER_MIN_SENDS = 10


def pick_winners(creatives: list[dict], *, min_sends: int) -> dict[int, int | None]:
    """Best creative id per set: highest score among creatives with enough data.

    Returns {set_id: creative_id | None}. None means "no winner yet -- needs
    more sends", which the UI shows as an emerging-data state instead of a
    misleading crown.
    """
    by_set: dict[int, list[dict]] = {}
    for c in creatives:
        by_set.setdefault(c["set_id"], []).append(c)
    winners: dict[int, int | None] = {}
    for set_id, group in by_set.items():
        eligible = [c for c in group if (c.get("sent") or 0) >= min_sends]
        if not eligible:
            winners[set_id] = None
        else:
            best = max(
                eligible, key=lambda c: (c.get("score") or 0, c.get("sent") or 0)
            )
            winners[set_id] = best["id"]
    return winners


async def campaign_analytics(db: AsyncSession, campaign: AdsCampaign) -> dict:
    stats = await _counts_for(
        db, [AdsAssignment.campaign_id == campaign.id], campaign_id=campaign.id
    )
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
        s = await _counts_for(
            db,
            [AdsAssignment.set_id == ads_set.id],
            campaign_id=campaign.id,
            set_id=ads_set.id,
        )
        s["score"] = performance_score(campaign.objective, s)
        sets.append(
            {
                "id": ads_set.id,
                "name": ads_set.name,
                "status": ads_set.status,
                "winner_id": None,  # filled below once creatives are scored
                **s,
            }
        )

    creatives = []
    for creative in (
        await db.execute(
            select(AdsCreative).where(AdsCreative.campaign_id == campaign.id, AdsCreative.is_deleted.is_(False))
        )
    ).scalars().all():
        c = await _counts_for(
            db,
            [AdsAssignment.creative_id == creative.id],
            campaign_id=campaign.id,
            set_id=creative.set_id,
            creative_id=creative.id,
        )
        c["score"] = performance_score(campaign.objective, c)
        creatives.append(
            {
                "id": creative.id,
                "name": creative.name,
                "set_id": creative.set_id,
                "status": creative.status,
                "version": creative.current_version,
                "body": creative.body,
                "is_winner": False,  # filled below
                "needs_more_data": (c.get("sent") or 0) < DISPLAY_WINNER_MIN_SENDS,
                **c,
            }
        )

    winners = pick_winners(creatives, min_sends=DISPLAY_WINNER_MIN_SENDS)
    for c in creatives:
        c["is_winner"] = winners.get(c["set_id"]) == c["id"]
    for s in sets:
        s["winner_id"] = winners.get(s["id"])

    alerts = build_alerts(stats, creatives, campaign)
    return {"campaign": stats, "sets": sets, "creatives": creatives, "alerts": alerts}


def build_alerts(
    stats: dict, creatives: list[dict], campaign: AdsCampaign | None = None
) -> list[dict]:
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
    # Andromeda nudge: a clear winner exists but the automatic optimizer is off.
    if campaign is not None:
        winners = [c for c in creatives if c.get("is_winner")]
        if winners and not getattr(campaign, "auto_optimize", False):
            names = ", ".join(sorted({c["name"] for c in winners}))
            alerts.append(
                {
                    "level": "info",
                    "message": (
                        f"Andromeda found a winning creative ({names}). Turn on "
                        "auto-optimization to shift the remaining spend to it automatically."
                    ),
                }
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
        # A clone never inherits a live optimizer: the user must opt in again.
        auto_optimize=False,
        optimize_metric=campaign.optimize_metric,
        optimize_min_sends=campaign.optimize_min_sends,
        optimize_min_gap_pct=campaign.optimize_min_gap_pct,
        optimize_action=campaign.optimize_action,
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
            contact_ids=getattr(ads_set, "contact_ids", None) if copy_audience else None,
            audience_id=getattr(ads_set, "audience_id", None) if copy_audience else None,
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
            auto_optimize=getattr(ads_set, "auto_optimize", True),
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


# ==========================================================================
# SMS set management: duplicate, audience removal
# ==========================================================================


async def duplicate_set(
    db: AsyncSession, ads_set: AdsSet, *, name: str | None = None, actor: str = "user"
) -> AdsSet:
    """Clone an SMS set with its creatives. Assignments are NOT copied: the new
    set starts with a fresh audience and splits it on the next build/launch."""
    siblings = set(
        (
            await db.execute(
                select(AdsSet.name).where(AdsSet.campaign_id == ads_set.campaign_id)
            )
        ).scalars().all()
    )
    base = (name or f"{ads_set.name} (Copy)")[:255]
    candidate = base
    n = 2
    while candidate in siblings:
        candidate = f"{base} ({n})"[:255]
        n += 1

    clone = AdsSet(
        campaign_id=ads_set.campaign_id,
        name=candidate,
        status=ads_set.status,
        list_ids=ads_set.list_ids,
        contact_ids=getattr(ads_set, "contact_ids", None),
        audience_id=getattr(ads_set, "audience_id", None),
        include_tags=ads_set.include_tags,
        exclude_tags=ads_set.exclude_tags,
        include_statuses=ads_set.include_statuses,
        exclude_statuses=ads_set.exclude_statuses,
        city=ads_set.city,
        state=ads_set.state,
        industry=ads_set.industry,
        activity_filter=ads_set.activity_filter,
        exclude_campaign_ids=ads_set.exclude_campaign_ids,
        daily_limit=ads_set.daily_limit,
        split_mode=ads_set.split_mode,
        auto_optimize=getattr(ads_set, "auto_optimize", True),
    )
    db.add(clone)
    await db.flush()

    for creative in (
        await db.execute(
            select(AdsCreative).where(
                AdsCreative.set_id == ads_set.id, AdsCreative.is_deleted.is_(False)
            )
        )
    ).scalars().all():
        new_creative = AdsCreative(
            set_id=clone.id,
            campaign_id=clone.campaign_id,
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

    log_activity(
        db,
        "set_duplicated",
        campaign_id=ads_set.campaign_id,
        entity_type="set",
        entity_id=clone.id,
        actor=actor,
        detail=f"Copied from '{ads_set.name}'",
    )
    await db.flush()
    return clone


async def remove_assignment(
    db: AsyncSession, assignment_id: int, *, campaign_id: int | None = None
) -> AdsAssignment:
    """Remove one contact from a campaign's audience.

    Only contacts that have NOT been sent to yet can be removed -- deleting a
    sent row would rewrite analytics history. Raises ValueError("already_sent")
    or ValueError("not_found").
    """
    assignment = (
        await db.execute(select(AdsAssignment).where(AdsAssignment.id == assignment_id))
    ).scalar_one_or_none()
    if assignment is None or (campaign_id is not None and assignment.campaign_id != campaign_id):
        raise ValueError("not_found")
    if assignment.sent_at is not None or assignment.send_status in (
        "sent",
        "delivered",
        "failed",
        "sending",
    ):
        raise ValueError("already_sent")
    await db.delete(assignment)
    await db.flush()
    return assignment


async def bulk_remove_assignments(
    db: AsyncSession, campaign_id: int, ids: list[int], *, actor: str = "user"
) -> dict:
    removed = 0
    skipped_sent = 0
    missing = 0
    for assignment_id in ids:
        try:
            await remove_assignment(db, assignment_id, campaign_id=campaign_id)
            removed += 1
        except ValueError as e:
            if str(e) == "already_sent":
                skipped_sent += 1
            else:
                missing += 1
    if removed:
        log_activity(
            db,
            "contacts_removed",
            campaign_id=campaign_id,
            actor=actor,
            detail=f"{removed} contact(s) removed from audience",
        )
        await db.flush()
    return {"removed": removed, "skipped_sent": skipped_sent, "missing": missing}


# ==========================================================================
# Saved audiences
# ==========================================================================


async def preview_saved_audience(
    db: AsyncSession, audience: AdsAudience, *, sample_size: int = 10
) -> dict:
    contacts = await resolve_audience_contacts(db, audience)
    eligible, skipped = await screen_contacts(db, None, contacts)
    breakdown: list[dict] = []
    for list_id in _csv_ids(audience.list_ids):
        breakdown.append({"list_id": list_id, "members": 0})
    if breakdown:
        from app.models.contact_list import ContactList, ContactListMember

        counts = dict(
            (
                await db.execute(
                    select(ContactListMember.list_id, func.count()).where(
                        ContactListMember.list_id.in_([b["list_id"] for b in breakdown])
                    ).group_by(ContactListMember.list_id)
                )
            ).all()
        )
        names = dict(
            (
                await db.execute(
                    select(ContactList.id, ContactList.name).where(
                        ContactList.id.in_([b["list_id"] for b in breakdown])
                    )
                )
            ).all()
        )
        for b in breakdown:
            b["members"] = counts.get(b["list_id"], 0)
            b["name"] = names.get(b["list_id"], f"List {b['list_id']}")
    return {
        "matched": len(contacts),
        "eligible": len(eligible),
        "skipped": skipped,
        "explicit_contacts": len(_csv_ids(audience.contact_ids)),
        "lists": breakdown,
        "sample": [
            {
                "id": c.id,
                "name": " ".join(filter(None, [c.first_name, c.last_name])).strip()
                or c.business_name,
                "phone_number": c.phone_number,
            }
            for c in eligible[:sample_size]
        ],
    }


def _copy_audience_onto_set(audience: AdsAudience, ads_set: AdsSet) -> None:
    """Replace a set's targeting definition with the saved audience's."""
    ads_set.audience_id = audience.id
    ads_set.list_ids = audience.list_ids
    ads_set.contact_ids = audience.contact_ids
    ads_set.include_tags = audience.include_tags
    ads_set.exclude_tags = audience.exclude_tags
    ads_set.include_statuses = audience.include_statuses
    ads_set.exclude_statuses = audience.exclude_statuses
    ads_set.city = audience.city
    ads_set.state = audience.state
    ads_set.industry = audience.industry
    ads_set.activity_filter = audience.activity_filter
    ads_set.exclude_campaign_ids = audience.exclude_campaign_ids


async def create_set_from_audience(
    db: AsyncSession,
    campaign: AdsCampaign,
    audience: AdsAudience,
    *,
    name: str | None = None,
    actor: str = "user",
) -> AdsSet:
    ads_set = AdsSet(campaign_id=campaign.id, name=(name or f"{audience.name} set")[:255])
    _copy_audience_onto_set(audience, ads_set)
    db.add(ads_set)
    await db.flush()
    log_activity(
        db,
        "set_created_from_audience",
        campaign_id=campaign.id,
        entity_type="set",
        entity_id=ads_set.id,
        actor=actor,
        detail=audience.name,
    )
    await db.flush()
    return ads_set


# ==========================================================================
# Click tracking (powers click rate + open rate)
# ==========================================================================


async def record_link_click(
    db: AsyncSession,
    *,
    campaign_id: int,
    creative_id: int,
    contact_id: int,
    set_id: int | None = None,
    assignment_id: int | None = None,
    detail: str | None = None,
) -> AdsEvent:
    """Record a tracked-link tap. Never raises for missing rows -- a click from
    an old message after a creative was deleted still counts."""
    if set_id is None:
        creative = (
            await db.execute(select(AdsCreative).where(AdsCreative.id == creative_id))
        ).scalar_one_or_none()
        if creative is not None:
            set_id = creative.set_id
    if assignment_id is None:
        assignment = (
            await db.execute(
                select(AdsAssignment).where(
                    AdsAssignment.campaign_id == campaign_id,
                    AdsAssignment.contact_id == contact_id,
                )
            )
        ).scalars().first()
        if assignment is not None:
            assignment_id = assignment.id
            if set_id is None:
                set_id = assignment.set_id
    if assignment_id is not None:
        assignment = (
            await db.execute(select(AdsAssignment).where(AdsAssignment.id == assignment_id))
        ).scalar_one_or_none()
        if assignment is not None:
            assignment.last_action_at = now_utc()
    event = record_event(
        db,
        "LINK_CLICKED",
        campaign_id=campaign_id,
        set_id=set_id,
        creative_id=creative_id,
        contact_id=contact_id,
        assignment_id=assignment_id,
        detail=detail,
    )
    await db.flush()
    return event


# ==========================================================================
# Andromeda auto-optimization
# ==========================================================================
#
# Strategy (runs ONLY when the campaign's master ``auto_optimize`` switch is
# ON, and only for sets whose own ``auto_optimize`` placement toggle is ON):
#
# 1. Score every active creative in the set on the campaign's chosen metric.
# 2. A creative can only win/lose once it has ``optimize_min_sends`` sends --
#    no decisions on noise.
# 3. The winner must beat each loser by ``optimize_min_gap_pct`` percent.
# 4. Pending (unsent) contacts on losing creatives are moved to the winner, so
#    the remaining spend flows to what works. Sent history is never touched.
# 5. With action "shift_and_pause", losers are also paused and future splits go
#    100% to the winner. With "shift", future splits rebalance proportionally
#    to the metric (gradual Andromeda-style shift) and nothing is paused.
# 6. Everything is logged (activity + OPTIMIZATION_RUN event) so the user can
#    see exactly what moved and why.


OPTIMIZE_METRICS = ("score", "reply_rate", "positive_reply_rate", "conversion_rate")
OPTIMIZE_ACTIONS = ("shift", "shift_and_pause")


async def _pending_by_creative(db: AsyncSession, set_id: int) -> dict[int, int]:
    rows = (
        await db.execute(
            select(AdsAssignment.creative_id, func.count()).where(
                AdsAssignment.set_id == set_id,
                AdsAssignment.send_status == "pending",
            ).group_by(AdsAssignment.creative_id)
        )
    ).all()
    return {int(cid): int(n) for cid, n in rows}


async def optimization_plan(db: AsyncSession, campaign: AdsCampaign) -> dict:
    """What Andromeda WOULD do right now. Never changes anything."""
    metric = (campaign.optimize_metric or "score").lower()
    if metric not in OPTIMIZE_METRICS:
        metric = "score"
    min_sends = max(int(campaign.optimize_min_sends or 30), 1)
    gap_pct = max(float(campaign.optimize_min_gap_pct or 0), 0)
    loser_min = min(10, min_sends)

    sets = list(
        (await db.execute(select(AdsSet).where(AdsSet.campaign_id == campaign.id))).scalars().all()
    )
    per_set: list[dict] = []
    total_moveable = 0
    for ads_set in sets:
        creatives = list(
            (
                await db.execute(
                    select(AdsCreative).where(
                        AdsCreative.set_id == ads_set.id,
                        AdsCreative.is_deleted.is_(False),
                    )
                )
            ).scalars().all()
        )
        active = [c for c in creatives if c.status == "active"]
        entry: dict = {
            "set_id": ads_set.id,
            "set_name": ads_set.name,
            "set_status": ads_set.status,
            "set_auto_optimize": bool(getattr(ads_set, "auto_optimize", True)),
            "eligible": True,
            "reason": None,
            "winner_id": None,
            "winner_name": None,
            "losers": [],
            "would_move": 0,
        }
        if ads_set.status != "active":
            entry["eligible"] = False
            entry["reason"] = f"Set is {ads_set.status}"
            per_set.append(entry)
            continue
        if not getattr(ads_set, "auto_optimize", True):
            entry["eligible"] = False
            entry["reason"] = "Auto-optimization is turned off for this set"
            per_set.append(entry)
            continue
        if len(active) < 2:
            entry["eligible"] = False
            entry["reason"] = "Needs at least 2 active creatives to compare"
            per_set.append(entry)
            continue

        stats: dict[int, dict] = {}
        for c in active:
            s = await _counts_for(
                db,
                [AdsAssignment.creative_id == c.id],
                campaign_id=campaign.id,
                set_id=ads_set.id,
                creative_id=c.id,
            )
            s["score"] = performance_score(campaign.objective, s)
            stats[c.id] = s
        pending = await _pending_by_creative(db, ads_set.id)

        contenders = [c for c in active if (stats[c.id].get("sent") or 0) >= min_sends]
        if not contenders:
            entry["eligible"] = False
            entry["reason"] = (
                f"No creative has {min_sends} sends yet "
                f"(best: {max((stats[c.id].get('sent') or 0) for c in active)})"
            )
            per_set.append(entry)
            continue
        winner = max(contenders, key=lambda c: (stats[c.id].get(metric) or 0, stats[c.id].get("sent") or 0))
        winner_value = stats[winner.id].get(metric) or 0
        if winner_value <= 0:
            entry["eligible"] = False
            entry["reason"] = "No engagement yet -- nothing to optimize on"
            per_set.append(entry)
            continue
        entry["winner_id"] = winner.id
        entry["winner_name"] = winner.name
        entry["winner_value"] = winner_value
        entry["winner_sends"] = stats[winner.id].get("sent") or 0

        for loser in active:
            if loser.id == winner.id:
                continue
            loser_sent = stats[loser.id].get("sent") or 0
            loser_value = stats[loser.id].get(metric) or 0
            if loser_sent < loser_min:
                entry["losers"].append(
                    {
                        "creative_id": loser.id,
                        "name": loser.name,
                        "action": "hold",
                        "reason": f"Only {loser_sent} sends -- needs {loser_min} before it can lose",
                        "pending": pending.get(loser.id, 0),
                    }
                )
                continue
            threshold = loser_value * (1 + gap_pct / 100)
            if winner_value < threshold:
                entry["losers"].append(
                    {
                        "creative_id": loser.id,
                        "name": loser.name,
                        "action": "hold",
                        "reason": (
                            f"Gap too small ({winner_value} vs {loser_value}, "
                            f"needs {gap_pct}% lead)"
                        ),
                        "pending": pending.get(loser.id, 0),
                    }
                )
                continue
            move = pending.get(loser.id, 0)
            entry["losers"].append(
                {
                    "creative_id": loser.id,
                    "name": loser.name,
                    "action": "shift",
                    "reason": f"Losing {winner_value} vs {loser_value} on {metric}",
                    "pending": move,
                }
            )
            entry["would_move"] += move
        total_moveable += entry["would_move"]
        per_set.append(entry)

    return {
        "campaign_id": campaign.id,
        "master_enabled": bool(getattr(campaign, "auto_optimize", False)),
        "metric": metric,
        "min_sends": min_sends,
        "min_gap_pct": gap_pct,
        "action": campaign.optimize_action or "shift",
        "last_run_at": as_utc(campaign.optimize_last_run_at),
        "total_moveable": total_moveable,
        "per_set": per_set,
    }


async def run_optimization(
    db: AsyncSession, campaign: AdsCampaign, *, actor: str = "system", dry_run: bool = False
) -> dict:
    """Execute one Andromeda optimization pass.

    A real (non-dry-run) pass refuses to run unless the campaign's master
    ``auto_optimize`` switch is ON -- the strategy never acts without the
    user's explicit opt-in. Dry runs always work so the user can preview.
    """
    if not dry_run and not getattr(campaign, "auto_optimize", False):
        return {"ok": False, "error": "disabled", "moved": 0, "paused": []}

    plan = await optimization_plan(db, campaign)
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan, "moved": 0, "paused": []}

    moved = 0
    paused: list[dict] = []
    action = (campaign.optimize_action or "shift").lower()
    for entry in plan["per_set"]:
        if not entry["eligible"] or not entry["winner_id"]:
            continue
        winner = (
            await db.execute(select(AdsCreative).where(AdsCreative.id == entry["winner_id"]))
        ).scalar_one_or_none()
        if winner is None:
            continue
        version = await ensure_version(db, winner)
        for loser in entry["losers"]:
            if loser["action"] != "shift" or loser["pending"] <= 0:
                continue
            result = await db.execute(
                update(AdsAssignment)
                .where(
                    AdsAssignment.set_id == entry["set_id"],
                    AdsAssignment.creative_id == loser["creative_id"],
                    AdsAssignment.send_status == "pending",
                )
                .values(
                    creative_id=winner.id,
                    creative_version_id=version.id,
                    skip_reason=None,
                    next_attempt_at=None,
                    experiment_group=winner.name,
                )
            )
            moved += result.rowcount or 0

        if action == "shift_and_pause":
            # Losers with a decisive gap are paused; the winner takes 100%.
            ads_set = (
                await db.execute(select(AdsSet).where(AdsSet.id == entry["set_id"]))
            ).scalar_one_or_none()
            for loser in entry["losers"]:
                if loser["action"] != "shift":
                    continue
                row = (
                    await db.execute(
                        select(AdsCreative).where(AdsCreative.id == loser["creative_id"])
                    )
                ).scalar_one_or_none()
                if row is not None and row.status == "active":
                    row.status = "paused"
                    row.allocation = 0.0
                    record_event(
                        db, "CREATIVE_PAUSED", campaign_id=campaign.id, creative_id=row.id,
                        detail="andromeda_auto_pause",
                    )
                    paused.append({"creative_id": row.id, "name": row.name})
            if paused and ads_set is not None:
                winner.allocation = 100.0
                ads_set.split_mode = "percentage"
        else:
            # Gradual shift: future splits rebalance proportionally to the
            # metric so the winner gets more new contacts without pausing
            # anyone. Epsilon keeps losers on a trickle for learning.
            metric = plan["metric"]
            weights: dict[int, float] = {}
            for c in (
                await db.execute(
                    select(AdsCreative).where(
                        AdsCreative.set_id == entry["set_id"],
                        AdsCreative.status == "active",
                        AdsCreative.is_deleted.is_(False),
                    )
                )
            ).scalars().all():
                s = await _counts_for(
                    db,
                    [AdsAssignment.creative_id == c.id],
                    campaign_id=campaign.id,
                    set_id=entry["set_id"],
                    creative_id=c.id,
                )
                s["score"] = performance_score(campaign.objective, s)
                weights[c.id] = max(float(s.get(metric) or 0), 0.5)
            if weights and sum(weights.values()) > 0:
                ads_set = (
                    await db.execute(select(AdsSet).where(AdsSet.id == entry["set_id"]))
                ).scalar_one_or_none()
                if ads_set is not None:
                    ads_set.split_mode = "weighted"
                    for cid, w in weights.items():
                        row = (
                            await db.execute(select(AdsCreative).where(AdsCreative.id == cid))
                        ).scalar_one_or_none()
                        if row is not None:
                            row.allocation = round(w, 2)

    if moved or paused:
        record_event(
            db,
            "OPTIMIZATION_RUN",
            campaign_id=campaign.id,
            detail=f"Moved {moved} pending contact(s) to winner(s); paused {len(paused)} creative(s)",
        )
        log_activity(
            db,
            "auto_optimized",
            campaign_id=campaign.id,
            actor=actor,
            detail=f"{moved} contact(s) shifted to winner(s), {len(paused)} creative(s) paused",
        )
    campaign.optimize_last_run_at = now_utc()
    await db.flush()
    return {"ok": True, "moved": moved, "paused": paused, "plan": plan}


async def pause_losing_creatives(
    db: AsyncSession, set_id: int, *, min_sends: int = 10, actor: str = "user"
) -> dict:
    """One-click manual action: pause every creative in the set except the
    current winner, and move their pending contacts onto the winner."""
    ads_set = (await db.execute(select(AdsSet).where(AdsSet.id == set_id))).scalar_one_or_none()
    if ads_set is None:
        raise ValueError("not_found")
    campaign = (
        await db.execute(select(AdsCampaign).where(AdsCampaign.id == ads_set.campaign_id))
    ).scalar_one_or_none()
    if campaign is None:
        raise ValueError("not_found")

    creatives = list(
        (
            await db.execute(
                select(AdsCreative).where(
                    AdsCreative.set_id == set_id,
                    AdsCreative.status == "active",
                    AdsCreative.is_deleted.is_(False),
                )
            )
        ).scalars().all()
    )
    if len(creatives) < 2:
        raise ValueError("need_two_creatives")
    scored = []
    for c in creatives:
        s = await _counts_for(
            db,
            [AdsAssignment.creative_id == c.id],
            campaign_id=campaign.id,
            set_id=set_id,
            creative_id=c.id,
        )
        s["score"] = performance_score(campaign.objective, s)
        scored.append((c, s))
    contenders = [(c, s) for c, s in scored if (s.get("sent") or 0) >= min_sends]
    if not contenders:
        raise ValueError("not_enough_data")
    winner, _ = max(contenders, key=lambda pair: (pair[1]["score"], pair[1]["sent"]))
    version = await ensure_version(db, winner)

    moved = 0
    paused = []
    for c, _ in scored:
        if c.id == winner.id:
            continue
        result = await db.execute(
            update(AdsAssignment)
            .where(
                AdsAssignment.set_id == set_id,
                AdsAssignment.creative_id == c.id,
                AdsAssignment.send_status == "pending",
            )
            .values(
                creative_id=winner.id,
                creative_version_id=version.id,
                skip_reason=None,
                next_attempt_at=None,
                experiment_group=winner.name,
            )
        )
        moved += result.rowcount or 0
        c.status = "paused"
        c.allocation = 0.0
        record_event(db, "CREATIVE_PAUSED", campaign_id=campaign.id, creative_id=c.id)
        paused.append({"creative_id": c.id, "name": c.name})
    winner.allocation = 100.0
    ads_set.split_mode = "percentage"
    log_activity(
        db,
        "losers_paused",
        campaign_id=campaign.id,
        entity_type="set",
        entity_id=set_id,
        actor=actor,
        detail=f"Winner '{winner.name}' kept; {len(paused)} paused, {moved} contact(s) moved",
    )
    await db.flush()
    return {"winner_id": winner.id, "winner_name": winner.name, "moved": moved, "paused": paused}
