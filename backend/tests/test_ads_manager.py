"""SMS Ads Manager: engine, safety rules and edge cases.

These cover the behaviour the product depends on and that is easy to break:
the audience split, sticky assignments, duplicate protection, suppression at
send time, creative versioning, budgets, drip pacing and analytics.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.ads import (
    AdsAssignment,
    AdsCampaign,
    AdsCreative,
    AdsCreativeVersion,
    AdsFollowUpStep,
    AdsFollowUpTask,
    AdsSet,
)
from app.models.contact import Contact, ContactTag, Tag
from app.models.contact_list import ContactList, ContactListMember
from app.models.conversation import Message
from app.models.suppression import SuppressionEntry
from app.services import ads_service as svc


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def make_contacts(db, n, start=1000, **kw):
    out = []
    for i in range(n):
        c = Contact(phone_number=f"+23480{start + i:08d}"[:16], country="Nigeria", **kw)
        db.add(c)
        out.append(c)
    await db.flush()
    return out


async def make_list(db, contacts, name="List"):
    lst = ContactList(name=name)
    db.add(lst)
    await db.flush()
    for c in contacts:
        db.add(ContactListMember(list_id=lst.id, contact_id=c.id))
    await db.flush()
    return lst


async def make_campaign(db, **kw):
    params = dict(name="Outreach", objective="replies", status="active", test_mode=True)
    params.update(kw)
    campaign = AdsCampaign(**params)
    db.add(campaign)
    await db.flush()
    return campaign


async def make_set(db, campaign, lst=None, **kw):
    params = dict(campaign_id=campaign.id, name="Set A", status="active")
    if lst is not None:
        params["list_ids"] = str(lst.id)
    params.update(kw)
    ads_set = AdsSet(**params)
    db.add(ads_set)
    await db.flush()
    return ads_set


async def make_creatives(db, ads_set, names, allocations=None):
    out = []
    for i, name in enumerate(names):
        c = AdsCreative(
            set_id=ads_set.id,
            campaign_id=ads_set.campaign_id,
            name=name,
            body=f"Hi {{{{first_name}}}}, message {name}",
            status="active",
            allocation=(allocations[i] if allocations else 0.0),
        )
        db.add(c)
        await db.flush()
        await svc.ensure_version(db, c)
        out.append(c)
    return out


# ---------------------------------------------------------------- splitting


def test_split_counts_loses_nothing():
    assert svc.split_counts(10000, [1, 1, 1]) == [3334, 3333, 3333]
    assert sum(svc.split_counts(9999, [1, 1, 1, 1])) == 9999
    assert svc.split_counts(100, [50, 30, 20]) == [50, 30, 20]
    assert svc.split_counts(0, [1, 1]) == [0, 0]


@pytest.mark.asyncio
async def test_audience_is_split_automatically(db):
    contacts = await make_contacts(db, 10)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A", "B", "C"])

    result = await svc.build_audience(db, campaign)
    assert result["added"] == 10
    counts = {}
    for a in (await db.execute(select(AdsAssignment))).scalars().all():
        counts[a.creative_id] = counts.get(a.creative_id, 0) + 1
    assert sorted(counts.values()) == [3, 3, 4]


@pytest.mark.asyncio
async def test_percentage_split(db):
    contacts = await make_contacts(db, 100)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst, split_mode="percentage")
    creatives = await make_creatives(db, ads_set, ["A", "B", "C"], [50, 30, 20])

    await svc.build_audience(db, campaign)
    counts = {}
    for a in (await db.execute(select(AdsAssignment))).scalars().all():
        counts[a.creative_id] = counts.get(a.creative_id, 0) + 1
    assert counts[creatives[0].id] == 50
    assert counts[creatives[1].id] == 30
    assert counts[creatives[2].id] == 20


@pytest.mark.asyncio
async def test_assignment_is_sticky_and_not_duplicated(db):
    contacts = await make_contacts(db, 6)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A", "B"])

    await svc.build_audience(db, campaign)
    first = {
        a.contact_id: a.creative_id
        for a in (await db.execute(select(AdsAssignment))).scalars().all()
    }
    # Running it again must add nobody and must not reshuffle anyone.
    again = await svc.build_audience(db, campaign)
    assert again["added"] == 0
    assert again["skipped"]["already_sent"] == 6
    second = {
        a.contact_id: a.creative_id
        for a in (await db.execute(select(AdsAssignment))).scalars().all()
    }
    assert first == second


# ------------------------------------------------------------- eligibility


@pytest.mark.asyncio
async def test_suppressed_and_opted_out_are_excluded(db):
    contacts = await make_contacts(db, 3)
    contacts[0].is_opted_out = True
    db.add(SuppressionEntry(phone_number=contacts[1].phone_number, reason="test"))
    await db.flush()
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])

    result = await svc.build_audience(db, campaign)
    assert result["added"] == 1
    assert result["skipped"]["opted_out"] == 1
    assert result["skipped"]["suppressed"] == 1


@pytest.mark.asyncio
async def test_phone_formats_are_deduplicated(db):
    # Same Nigerian number in three formats must count as one contact.
    a = Contact(phone_number="08031234567", country="Nigeria")
    b = Contact(phone_number="+2348031234567", country="Nigeria")
    c = Contact(phone_number="2348031234567", country="Nigeria")
    db.add_all([a, b, c])
    await db.flush()
    lst = await make_list(db, [a, b, c])
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])

    result = await svc.build_audience(db, campaign)
    assert result["added"] == 1
    assert result["skipped"]["duplicate"] == 2


@pytest.mark.asyncio
async def test_tag_targeting(db):
    contacts = await make_contacts(db, 4)
    tag = Tag(name="Jewelry")
    db.add(tag)
    await db.flush()
    db.add_all([ContactTag(contact_id=contacts[0].id, tag_id=tag.id),
                ContactTag(contact_id=contacts[1].id, tag_id=tag.id)])
    await db.flush()
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst, include_tags="Jewelry")
    await make_creatives(db, ads_set, ["A"])

    result = await svc.build_audience(db, campaign)
    assert result["added"] == 2


# ------------------------------------------------------------- dispatching


@pytest.mark.asyncio
async def test_dispatch_respects_daily_limit(db):
    contacts = await make_contacts(db, 10)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db, daily_limit=4)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)

    first = await svc.dispatch_campaign(db, campaign, limit=100)
    assert first["sent"] == 4
    second = await svc.dispatch_campaign(db, campaign, limit=100)
    assert second["sent"] == 0
    assert second["state"] == "daily_limit_reached"


@pytest.mark.asyncio
async def test_total_limit_completes_campaign(db):
    contacts = await make_contacts(db, 5)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db, total_limit=2)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)

    await svc.dispatch_campaign(db, campaign, limit=100)
    await svc.dispatch_campaign(db, campaign, limit=100)
    assert campaign.status == "completed"


@pytest.mark.asyncio
async def test_opt_out_after_queueing_is_caught_at_send_time(db):
    contacts = await make_contacts(db, 2)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)

    # Contact opts out AFTER the queue was built.
    contacts[0].is_opted_out = True
    await db.flush()

    result = await svc.dispatch_campaign(db, campaign, limit=10)
    assert result["sent"] == 1
    assert result["reasons"]["opted_out"] == 1


@pytest.mark.asyncio
async def test_suppression_after_queueing_is_caught_at_send_time(db):
    contacts = await make_contacts(db, 2)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)

    db.add(SuppressionEntry(phone_number=svc.normalize(contacts[1].phone_number), reason="late"))
    await db.flush()

    result = await svc.dispatch_campaign(db, campaign, limit=10)
    assert result["sent"] == 1
    assert result["reasons"]["suppressed"] == 1


@pytest.mark.asyncio
async def test_paused_creative_holds_contacts_instead_of_dropping_them(db):
    contacts = await make_contacts(db, 4)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    creatives = await make_creatives(db, ads_set, ["A", "B"])
    await svc.build_audience(db, campaign)

    creatives[1].status = "paused"
    await db.flush()
    result = await svc.dispatch_campaign(db, campaign, limit=10)
    assert result["sent"] == 2
    assert result["reasons"]["creative_paused"] == 2
    still_pending = [
        a for a in (await db.execute(select(AdsAssignment))).scalars().all()
        if a.send_status == "pending"
    ]
    assert len(still_pending) == 2  # kept, not lost


@pytest.mark.asyncio
async def test_paused_campaign_sends_nothing(db):
    contacts = await make_contacts(db, 3)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)

    campaign.status = "paused"
    result = await svc.dispatch_campaign(db, campaign, limit=10)
    assert result["sent"] == 0

    # Resume: assignments survived and sending continues.
    campaign.status = "active"
    result = await svc.dispatch_campaign(db, campaign, limit=10)
    assert result["sent"] == 3


@pytest.mark.asyncio
async def test_outside_sending_window_defers(db):
    contacts = await make_contacts(db, 2)
    lst = await make_list(db, contacts)
    now = datetime.now(timezone.utc)
    # A one-hour window that certainly is not now.
    closed = (now.hour + 3) % 24
    campaign = await make_campaign(
        db, send_start_hour=closed, send_end_hour=(closed + 1) % 24, timezone_name="UTC"
    )
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)

    result = await svc.dispatch_campaign(db, campaign, limit=10)
    assert result["sent"] == 0
    assert result["state"] == "outside_sending_window"


@pytest.mark.asyncio
async def test_drip_interval_paces_sending(db):
    contacts = await make_contacts(db, 5)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db, drip_mode="interval", drip_interval_minutes=30)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)

    assert (await svc.dispatch_campaign(db, campaign, limit=100))["sent"] == 1
    # Second call within the interval sends nothing.
    assert (await svc.dispatch_campaign(db, campaign, limit=100))["sent"] == 0


@pytest.mark.asyncio
async def test_batch_drip(db):
    contacts = await make_contacts(db, 12)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db, drip_mode="batch", drip_batch_size=5, drip_interval_minutes=60)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)
    assert (await svc.dispatch_campaign(db, campaign, limit=100))["sent"] == 5


@pytest.mark.asyncio
async def test_dispatch_is_idempotent_across_repeated_runs(db):
    contacts = await make_contacts(db, 5)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)

    total = 0
    for _ in range(4):
        total += (await svc.dispatch_campaign(db, campaign, limit=100))["sent"]
    assert total == 5  # never more than the audience


# ---------------------------------------------------------- versioning


@pytest.mark.asyncio
async def test_editing_a_creative_creates_a_version_and_keeps_history(db):
    contacts = await make_contacts(db, 2)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    creative = (await make_creatives(db, ads_set, ["A"]))[0]
    await svc.build_audience(db, campaign)
    await svc.dispatch_campaign(db, campaign, limit=1)

    original_version_id = (
        await db.execute(
            select(AdsAssignment.creative_version_id).where(AdsAssignment.sent_at.is_not(None))
        )
    ).scalars().first()

    await svc.bump_version(db, creative, "Completely new text", None)
    assert creative.current_version == 2
    versions = list(
        (
            await db.execute(
                select(AdsCreativeVersion).where(AdsCreativeVersion.creative_id == creative.id)
            )
        ).scalars().all()
    )
    assert len(versions) == 2
    # The already-sent assignment still points at v1.
    still = (
        await db.execute(
            select(AdsAssignment.creative_version_id).where(AdsAssignment.sent_at.is_not(None))
        )
    ).scalars().first()
    assert still == original_version_id


@pytest.mark.asyncio
async def test_queued_edit_policy_keep_uses_assigned_version(db):
    contacts = await make_contacts(db, 1)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db, test_mode=False, queued_edit_policy="keep")
    ads_set = await make_set(db, campaign, lst)
    creative = (await make_creatives(db, ads_set, ["A"]))[0]
    await svc.build_audience(db, campaign)

    await svc.bump_version(db, creative, "Version two text", None)
    assignment = (await db.execute(select(AdsAssignment))).scalars().first()
    body, _ = await svc._creative_body(db, assignment, campaign)
    assert "message A" in body  # the version they were assigned

    campaign.queued_edit_policy = "update"
    body, _ = await svc._creative_body(db, assignment, campaign)
    assert body == "Version two text"


# -------------------------------------------------------------- follow-ups


@pytest.mark.asyncio
async def test_followups_are_scheduled_and_stop_on_reply(db):
    contacts = await make_contacts(db, 1)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    db.add(
        AdsFollowUpStep(
            campaign_id=campaign.id, step_order=1, wait_hours=0, condition="no_reply",
            action="send_sms", body="Just following up",
        )
    )
    await db.flush()

    await svc.build_audience(db, campaign)
    await svc.dispatch_campaign(db, campaign, limit=10)
    tasks = list((await db.execute(select(AdsFollowUpTask))).scalars().all())
    assert len(tasks) == 1

    # The contact replies -> the no_reply follow-up must be cancelled.
    from app.models.conversation import Conversation

    conv = Conversation(contact_id=contacts[0].id, status="unread")
    db.add(conv)
    await db.flush()
    db.add(
        Message(
            conversation_id=conv.id, contact_id=contacts[0].id, direction="incoming",
            body="Yes please", status="delivered", idempotency_key="in-1",
        )
    )
    await db.flush()

    totals = await svc.process_followups(db)
    assert totals["cancelled"] == 1
    task = (await db.execute(select(AdsFollowUpTask))).scalars().first()
    assert task.status == "cancelled"


@pytest.mark.asyncio
async def test_followup_cancelled_when_contact_suppressed(db):
    contacts = await make_contacts(db, 1)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    db.add(AdsFollowUpStep(campaign_id=campaign.id, wait_hours=0, condition="always", action="send_sms", body="Ping"))
    await db.flush()
    await svc.build_audience(db, campaign)
    await svc.dispatch_campaign(db, campaign, limit=10)

    # Suppressing cancels pending work immediately...
    await svc.suppress_contact(db, contacts[0], reason="Not interested")
    await db.flush()
    task = (await db.execute(select(AdsFollowUpTask))).scalars().first()
    assert task.status == "cancelled"

    # ...and the sweep therefore has nothing left to send.
    totals = await svc.process_followups(db)
    assert totals["sent"] == 0


# --------------------------------------------------------------- analytics


@pytest.mark.asyncio
async def test_analytics_and_score(db):
    contacts = await make_contacts(db, 4)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A", "B"])
    await svc.build_audience(db, campaign)
    await svc.dispatch_campaign(db, campaign, limit=10)

    assignment = (await db.execute(select(AdsAssignment))).scalars().first()
    assignment.reply_status = "positive"
    await db.flush()

    report = await svc.campaign_analytics(db, campaign)
    assert report["campaign"]["sent"] == 4
    assert report["campaign"]["replies"] == 1
    assert report["campaign"]["reply_rate"] == 25.0
    assert report["campaign"]["score"] > 0
    assert len(report["creatives"]) == 2


@pytest.mark.asyncio
async def test_reply_classification():
    assert svc.classify_reply("Yes I am interested") == "positive"
    assert svc.classify_reply("Not interested, stop") == "negative"
    assert svc.classify_reply("Can we meet tomorrow?") == "meeting_request"
    assert svc.classify_reply("What is this?") == "question"


# ------------------------------------------------------- simulate/validate


@pytest.mark.asyncio
async def test_simulation_changes_nothing(db):
    contacts = await make_contacts(db, 9)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db, status="draft", daily_limit=3)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A", "B", "C"])

    sim = await svc.simulate(db, campaign)
    assert sim["eligible"] == 9
    assert sim["estimated_days"] == 3
    assert sum(s["contacts"] for s in sim["per_set"][0]["split"]) == 9
    # Nothing was written.
    assert (await db.execute(select(AdsAssignment))).scalars().first() is None


@pytest.mark.asyncio
async def test_validation_blocks_bad_campaigns(db):
    campaign = await make_campaign(db, status="draft", name="")
    report = await svc.validate_campaign(db, campaign)
    assert not report["ok"]
    assert any("name" in e.lower() for e in report["errors"])
    assert any("sms set" in e.lower() for e in report["errors"])


@pytest.mark.asyncio
async def test_percentage_split_must_total_100(db):
    contacts = await make_contacts(db, 5)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db, status="draft")
    ads_set = await make_set(db, campaign, lst, split_mode="percentage")
    await make_creatives(db, ads_set, ["A", "B"], [70, 20])
    report = await svc.validate_campaign(db, campaign)
    assert not report["ok"]
    assert any("100%" in e for e in report["errors"])


@pytest.mark.asyncio
async def test_test_mode_sends_no_real_messages(db):
    contacts = await make_contacts(db, 3)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db, test_mode=True)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)
    result = await svc.dispatch_campaign(db, campaign, limit=10)
    assert result["sent"] == 3
    # No Message rows exist: nothing left the server, no credits consumed.
    assert (await db.execute(select(Message))).scalars().first() is None


# ------------------------------------------------------------- live edits


@pytest.mark.asyncio
async def test_adding_contacts_to_a_running_campaign_only_evaluates_new_ones(db):
    contacts = await make_contacts(db, 3)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)
    await svc.dispatch_campaign(db, campaign, limit=10)

    fresh = await make_contacts(db, 2, start=5000)
    for c in fresh:
        db.add(ContactListMember(list_id=lst.id, contact_id=c.id))
    await db.flush()

    result = await svc.build_audience(db, campaign)
    assert result["added"] == 2
    assert result["skipped"]["already_sent"] == 3
    sent_again = await svc.dispatch_campaign(db, campaign, limit=10)
    assert sent_again["sent"] == 2  # the original 3 are not re-sent


@pytest.mark.asyncio
async def test_always_on_picks_up_new_contacts(db):
    contacts = await make_contacts(db, 2)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db, always_on=True)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)

    newcomer = (await make_contacts(db, 1, start=7000))[0]
    db.add(ContactListMember(list_id=lst.id, contact_id=newcomer.id))
    await db.flush()

    added = await svc.refresh_always_on(db)
    assert added == 1


@pytest.mark.asyncio
async def test_duplicate_campaign_copies_structure_not_audience(db):
    contacts = await make_contacts(db, 3)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A", "B"])
    await svc.build_audience(db, campaign)

    clone = await svc.duplicate_campaign(db, campaign)
    assert clone.status == "draft"
    clone_sets = list(
        (await db.execute(select(AdsSet).where(AdsSet.campaign_id == clone.id))).scalars().all()
    )
    assert len(clone_sets) == 1
    assert clone_sets[0].list_ids is None  # audience deliberately not copied
    clone_creatives = list(
        (
            await db.execute(select(AdsCreative).where(AdsCreative.campaign_id == clone.id))
        ).scalars().all()
    )
    assert len(clone_creatives) == 2
    assert (
        await db.execute(select(AdsAssignment).where(AdsAssignment.campaign_id == clone.id))
    ).scalars().first() is None


@pytest.mark.asyncio
async def test_frequency_limit_defers_contact(db):
    contacts = await make_contacts(db, 1)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db, max_per_contact_per_day=1)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)

    from app.models.conversation import Conversation

    conv = Conversation(contact_id=contacts[0].id, status="active")
    db.add(conv)
    await db.flush()
    db.add(
        Message(
            conversation_id=conv.id, contact_id=contacts[0].id, direction="outgoing",
            body="Earlier today", status="sent", idempotency_key="out-earlier",
        )
    )
    await db.flush()

    result = await svc.dispatch_campaign(db, campaign, limit=10)
    assert result["sent"] == 0
    assert result["reasons"]["frequency_limit"] == 1
