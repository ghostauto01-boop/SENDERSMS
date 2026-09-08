"""Saved audiences, set duplication, audience removal, winners & Andromeda.

Covers the audience-control release: empty targeting matches NOTHING (the
old "empty means all contacts" bug), saved audiences attach to campaigns,
sets duplicate cleanly, unsent contacts can be removed, winners are detected,
click/open rates work, and the Andromeda optimizer only acts when opted in.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.ads import (
    AdsAssignment,
    AdsAudience,
    AdsCampaign,
    AdsCreative,
    AdsSet,
)
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
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


async def make_contacts(db, n, start=2000, **kw):
    out = []
    for i in range(n):
        c = Contact(phone_number=f"+23470{start + i:08d}"[:16], country="Nigeria", **kw)
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
            body=f"Message {name}",
            status="active",
            allocation=(allocations[i] if allocations else 0.0),
        )
        db.add(c)
        await db.flush()
        await svc.ensure_version(db, c)
        out.append(c)
    return out


# ------------------------------------------------- empty targeting = nobody


@pytest.mark.asyncio
async def test_empty_set_matches_zero_contacts(db):
    """THE bug fix: a set with no lists/contacts/audience/filters must match
    zero contacts -- never the whole database."""
    await make_contacts(db, 5)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign)  # no list, no filters at all
    await make_creatives(db, ads_set, ["A"])

    assert await svc.resolve_audience(db, ads_set) == []
    result = await svc.build_audience(db, campaign)
    assert result["added"] == 0
    assert result["audience"] == 0


@pytest.mark.asyncio
async def test_empty_list_selection_matches_zero(db):
    await make_contacts(db, 5)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst=None)
    ads_set.list_ids = ""  # explicitly cleared
    await db.flush()
    assert await svc.resolve_audience(db, ads_set) == []


@pytest.mark.asyncio
async def test_city_only_targeting_still_works(db):
    """Filter-only targeting IS targeting: a city on its own matches."""
    await make_contacts(db, 3, city="Lagos")
    await make_contacts(db, 2, city="Abuja", start=3000)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, city="Lagos")
    matched = await svc.resolve_audience(db, ads_set)
    assert len(matched) == 3


@pytest.mark.asyncio
async def test_explicit_contact_ids_are_targetable(db):
    contacts = await make_contacts(db, 5)
    await make_list(db, contacts)  # list exists but is NOT selected
    campaign = await make_campaign(db)
    ads_set = await make_set(
        db, campaign, contact_ids=f"{contacts[0].id},{contacts[2].id}"
    )
    matched = await svc.resolve_audience(db, ads_set)
    assert {c.id for c in matched} == {contacts[0].id, contacts[2].id}


# ------------------------------------------------------- saved audiences


@pytest.mark.asyncio
async def test_saved_audience_resolve_and_preview(db):
    contacts = await make_contacts(db, 6)
    lst = await make_list(db, contacts[:4])
    audience = AdsAudience(
        name="VIP", list_ids=str(lst.id), contact_ids=str(contacts[5].id)
    )
    db.add(audience)
    await db.flush()

    matched = await svc.resolve_audience_contacts(db, audience)
    assert len(matched) == 5  # 4 from list + 1 explicit

    preview = await svc.preview_saved_audience(db, audience)
    assert preview["matched"] == 5
    assert preview["lists"][0]["members"] == 4


@pytest.mark.asyncio
async def test_saved_audience_empty_matches_zero(db):
    await make_contacts(db, 5)
    audience = AdsAudience(name="Blank")
    db.add(audience)
    await db.flush()
    assert await svc.resolve_audience_contacts(db, audience) == []


@pytest.mark.asyncio
async def test_attach_audience_creates_set(db):
    contacts = await make_contacts(db, 4)
    lst = await make_list(db, contacts)
    audience = AdsAudience(name="Buyers", list_ids=str(lst.id))
    db.add(audience)
    await db.flush()
    campaign = await make_campaign(db)

    ads_set = await svc.create_set_from_audience(db, campaign, audience)
    assert ads_set.audience_id == audience.id
    matched = await svc.resolve_audience(db, ads_set)
    assert len(matched) == 4


@pytest.mark.asyncio
async def test_set_merges_linked_audience(db):
    contacts = await make_contacts(db, 4)
    lst_a = await make_list(db, contacts[:2], name="A")
    lst_b = await make_list(db, contacts[2:], name="B")
    audience = AdsAudience(name="A-list", list_ids=str(lst_a.id))
    db.add(audience)
    await db.flush()
    campaign = await make_campaign(db)
    # Set has its own list B plus the linked audience's list A -> union.
    ads_set = await make_set(db, campaign, lst_b, audience_id=audience.id)
    matched = await svc.resolve_audience(db, ads_set)
    assert len(matched) == 4


# ---------------------------------------------------------- duplicate set


@pytest.mark.asyncio
async def test_duplicate_set_copies_targeting_and_creatives_not_audience(db):
    contacts = await make_contacts(db, 6)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A", "B"])
    await svc.build_audience(db, campaign)
    before = (await db.execute(select(AdsAssignment))).scalars().all()
    assert len(before) == 6

    clone = await svc.duplicate_set(db, ads_set)
    assert clone.id != ads_set.id
    assert clone.list_ids == ads_set.list_ids
    assert "(Copy)" in clone.name
    clones = list(
        (await db.execute(select(AdsCreative).where(AdsCreative.set_id == clone.id)))
        .scalars()
        .all()
    )
    assert len(clones) == 2
    # No assignments were copied.
    after = (await db.execute(select(AdsAssignment))).scalars().all()
    assert len(after) == 6


# ------------------------------------------------------- remove from audience


@pytest.mark.asyncio
async def test_remove_pending_assignment(db):
    contacts = await make_contacts(db, 3)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)

    assignment = (await db.execute(select(AdsAssignment))).scalars().first()
    await svc.remove_assignment(db, assignment.id, campaign_id=campaign.id)
    remaining = (await db.execute(select(AdsAssignment))).scalars().all()
    assert len(remaining) == 2


@pytest.mark.asyncio
async def test_remove_sent_assignment_is_refused(db):
    contacts = await make_contacts(db, 2)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)
    await svc.dispatch_campaign(db, campaign, limit=10)

    assignment = (await db.execute(select(AdsAssignment))).scalars().first()
    with pytest.raises(ValueError, match="already_sent"):
        await svc.remove_assignment(db, assignment.id, campaign_id=campaign.id)


@pytest.mark.asyncio
async def test_bulk_remove_skips_sent(db):
    contacts = await make_contacts(db, 3)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)
    rows = list((await db.execute(select(AdsAssignment))).scalars().all())
    rows[0].send_status = "sent"
    from datetime import datetime, timezone

    rows[0].sent_at = datetime.now(timezone.utc)
    await db.flush()

    result = await svc.bulk_remove_assignments(db, campaign.id, [r.id for r in rows])
    assert result == {"removed": 2, "skipped_sent": 1, "missing": 0}


@pytest.mark.asyncio
async def test_bulk_remove_all_deletes_only_unsent_rows(db):
    contacts = await make_contacts(db, 3)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)

    rows = list((await db.execute(select(AdsAssignment))).scalars().all())
    assert len(rows) == 3
    # One row already went out — it must survive the remove-all.
    rows[0].send_status = "delivered"
    from datetime import datetime, timezone

    rows[0].sent_at = datetime.now(timezone.utc)
    await db.flush()

    result = await svc.bulk_remove_assignments(
        db, campaign.id, ids=[], actor="user", remove_all=True
    )
    # Only the two unsent rows were removable; the sent one was never targeted.
    assert result == {"removed": 2, "skipped_sent": 0, "missing": 0}

    remaining = (await db.execute(select(AdsAssignment))).scalars().all()
    assert [r.id for r in remaining] == [rows[0].id]


@pytest.mark.asyncio
async def test_bulk_remove_all_empty_audience_is_a_noop(db):
    campaign = await make_campaign(db)
    result = await svc.bulk_remove_assignments(
        db, campaign.id, ids=[], actor="user", remove_all=True
    )
    assert result == {"removed": 0, "skipped_sent": 0, "missing": 0}


# ------------------------------------------------------------- winners


def test_pick_winners_needs_enough_sends():
    creatives = [
        {"id": 1, "set_id": 1, "sent": 50, "score": 10.0},
        {"id": 2, "set_id": 1, "sent": 50, "score": 30.0},
        {"id": 3, "set_id": 2, "sent": 2, "score": 99.0},
    ]
    assert svc.pick_winners(creatives, min_sends=10) == {1: 2, 2: None}


@pytest.mark.asyncio
async def test_campaign_analytics_flags_winner(db):
    contacts = await make_contacts(db, 30)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    creatives = await make_creatives(db, ads_set, ["Loser", "Winner"])
    await svc.build_audience(db, campaign)
    await svc.dispatch_campaign(db, campaign, limit=100)

    # Every contact on "Winner" replies positively.
    rows = list((await db.execute(select(AdsAssignment))).scalars().all())
    for r in rows:
        if r.creative_id == creatives[1].id:
            r.reply_status = "positive"
    await db.flush()

    report = await svc.campaign_analytics(db, campaign)
    by_id = {c["id"]: c for c in report["creatives"]}
    assert by_id[creatives[1].id]["is_winner"] is True
    assert by_id[creatives[0].id]["is_winner"] is False
    assert report["sets"][0]["winner_id"] == creatives[1].id


# ------------------------------------------------- clicks / opens


@pytest.mark.asyncio
async def test_click_and_open_rates(db):
    contacts = await make_contacts(db, 4)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db)
    ads_set = await make_set(db, campaign, lst)
    creatives = await make_creatives(db, ads_set, ["A"])
    await svc.build_audience(db, campaign)
    await svc.dispatch_campaign(db, campaign, limit=10)

    await svc.record_link_click(
        db, campaign_id=campaign.id, creative_id=creatives[0].id, contact_id=contacts[0].id
    )
    await svc.record_link_click(
        db, campaign_id=campaign.id, creative_id=creatives[0].id, contact_id=contacts[1].id
    )
    rows = list((await db.execute(select(AdsAssignment))).scalars().all())
    rows[2].reply_status = "positive"  # reply without click still counts as opened
    await db.flush()

    stats = await svc._counts_for(
        db, [AdsAssignment.campaign_id == campaign.id], campaign_id=campaign.id
    )
    assert stats["clicks"] == 2
    assert stats["click_rate"] == 50.0
    assert stats["opens"] == 3
    assert stats["open_rate"] == 75.0


# ------------------------------------------------------------- andromeda


async def _campaign_with_winner(db, **campaign_kw):
    """Two creatives, 40 contacts each assigned; A sent 40 with no replies,
    B sent 40 with 20 positive replies; 20 more pending on A, 0 on B... built
    so the optimizer has a decisive winner and moveable pending."""
    contacts = await make_contacts(db, 100)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db, **campaign_kw)
    ads_set = await make_set(db, campaign, lst)
    creatives = await make_creatives(db, ads_set, ["A-loser", "B-winner"])
    await svc.build_audience(db, campaign)
    rows = list((await db.execute(select(AdsAssignment))).scalars().all())
    # Split deterministically: first 50 -> A, next 50 -> B.
    for i, r in enumerate(rows):
        r.creative_id = creatives[0].id if i < 50 else creatives[1].id
    await db.flush()
    # A: 30 sent, no replies, 20 pending. B: 30 sent, 20 positive, 20 pending.
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    for i, r in enumerate(rows):
        if i < 30 or 50 <= i < 80:
            r.send_status = "sent"
            r.sent_at = now
            r.delivery_status = "delivered"
            if 50 <= i < 70:
                r.reply_status = "positive"
    await db.flush()
    return campaign, ads_set, creatives


@pytest.mark.asyncio
async def test_optimizer_is_off_by_default(db):
    campaign, _, _ = await _campaign_with_winner(db)
    assert campaign.auto_optimize is False
    result = await svc.run_optimization(db, campaign)
    assert result == {"ok": False, "error": "disabled", "moved": 0, "paused": []}


@pytest.mark.asyncio
async def test_optimizer_dry_run_changes_nothing(db):
    campaign, _, _ = await _campaign_with_winner(db)
    before = [(r.id, r.creative_id) for r in (await db.execute(select(AdsAssignment))).scalars()]
    result = await svc.run_optimization(db, campaign, dry_run=True)
    assert result["ok"] is True
    assert result["plan"]["total_moveable"] == 20  # A's 20 pending would move to B
    after = [(r.id, r.creative_id) for r in (await db.execute(select(AdsAssignment))).scalars()]
    assert before == after


@pytest.mark.asyncio
async def test_optimizer_moves_pending_to_winner_when_enabled(db):
    campaign, _, creatives = await _campaign_with_winner(
        db, auto_optimize=True, optimize_min_sends=20, optimize_min_gap_pct=10
    )
    result = await svc.run_optimization(db, campaign, actor="tester")
    assert result["ok"] is True
    assert result["moved"] == 20
    assert result["paused"] == []  # default action is shift-only
    pending = list(
        (
            await db.execute(
                select(AdsAssignment).where(AdsAssignment.send_status == "pending")
            )
        ).scalars()
    )
    assert pending and all(r.creative_id == creatives[1].id for r in pending)


@pytest.mark.asyncio
async def test_optimizer_shift_and_pause_pauses_losers(db):
    campaign, ads_set, creatives = await _campaign_with_winner(
        db, auto_optimize=True, optimize_min_sends=20, optimize_min_gap_pct=10,
        optimize_action="shift_and_pause",
    )
    result = await svc.run_optimization(db, campaign)
    assert result["ok"] is True
    assert len(result["paused"]) == 1
    loser = (
        await db.execute(select(AdsCreative).where(AdsCreative.id == creatives[0].id))
    ).scalar_one()
    assert loser.status == "paused"
    await db.refresh(ads_set)
    assert ads_set.split_mode == "percentage"


@pytest.mark.asyncio
async def test_optimizer_respects_per_set_toggle(db):
    campaign, ads_set, _ = await _campaign_with_winner(
        db, auto_optimize=True, optimize_min_sends=20, optimize_min_gap_pct=10
    )
    ads_set.auto_optimize = False
    await db.flush()
    result = await svc.run_optimization(db, campaign)
    assert result["ok"] is True
    assert result["moved"] == 0


@pytest.mark.asyncio
async def test_optimizer_needs_minimum_sends(db):
    campaign, _, _ = await _campaign_with_winner(
        db, auto_optimize=True, optimize_min_sends=500
    )
    result = await svc.run_optimization(db, campaign)
    assert result["ok"] is True
    assert result["moved"] == 0
    assert "sends yet" in result["plan"]["per_set"][0]["reason"]


@pytest.mark.asyncio
async def test_pause_losing_creatives_manual(db):
    campaign, ads_set, creatives = await _campaign_with_winner(db)
    result = await svc.pause_losing_creatives(db, ads_set.id, min_sends=10)
    assert result["winner_id"] == creatives[1].id
    assert result["moved"] == 20
    assert len(result["paused"]) == 1
    assert campaign.auto_optimize is False  # manual action needs no opt-in


@pytest.mark.asyncio
async def test_duplicate_campaign_resets_auto_optimize(db):
    contacts = await make_contacts(db, 4)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db, auto_optimize=True)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    clone = await svc.duplicate_campaign(db, campaign)
    assert clone.auto_optimize is False
    assert clone.optimize_metric == campaign.optimize_metric
