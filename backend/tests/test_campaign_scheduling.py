"""Tests for scheduled campaign launches.

The core guarantee: a campaign with a future scheduled_start_at launches by
itself once that time passes, exactly once, and not a moment before.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.campaign import Campaign, CampaignContact
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.schemas.campaign import CampaignScheduleRequest


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def launcher(db, monkeypatch):
    """launch_due_campaigns_async bound to the test session and a stub broker.

    The real function opens its own session from async_session_factory; here we
    point that at the in-memory test database so both see the same rows.
    """
    import app.tasks.campaign_tasks as ct
    from contextlib import asynccontextmanager

    enqueued: list[int] = []

    @asynccontextmanager
    async def _factory():
        yield db

    monkeypatch.setattr(ct, "async_session_factory", _factory)
    monkeypatch.setattr(ct, "try_enqueue", lambda task, *a, **k: enqueued.append(a[0]) or True)

    async def _run():
        return await ct.launch_due_campaigns_async()

    _run.enqueued = enqueued
    return _run


async def _campaign_with_contacts(db, scheduled_start_at, status="scheduled", n=2):
    lst = ContactList(name="Test list")
    db.add(lst)
    await db.flush()
    for i in range(n):
        c = Contact(phone_number=f"+23480312345{10 + i}", first_name=f"C{i}")
        db.add(c)
        await db.flush()
        db.add(ContactListMember(list_id=lst.id, contact_id=c.id))
    campaign = Campaign(
        name="Scheduled campaign",
        status=status,
        list_id=lst.id,
        message_body="Hello {{first_name}}",
        scheduled_start_at=scheduled_start_at,
    )
    db.add(campaign)
    await db.flush()
    return campaign


# --- the launcher --------------------------------------------------------


@pytest.mark.asyncio
async def test_future_campaign_is_not_launched_early(db, launcher):
    c = await _campaign_with_contacts(db, datetime.now(timezone.utc) + timedelta(hours=2))
    assert await launcher() == []
    await db.refresh(c)
    assert c.status == "scheduled"
    assert c.scheduled_start_at is not None


@pytest.mark.asyncio
async def test_due_campaign_launches(db, launcher):
    c = await _campaign_with_contacts(db, datetime.now(timezone.utc) - timedelta(minutes=1))
    launched = await launcher()
    assert launched == [c.id]
    await db.refresh(c)
    assert c.status == "running"
    assert c.started_at is not None
    # Trigger consumed, so a later pause/resume cannot re-fire it.
    assert c.scheduled_start_at is None
    # Contacts were populated, and the send task was queued.
    rows = (await db.execute(select(CampaignContact).where(CampaignContact.campaign_id == c.id))).scalars().all()
    assert len(rows) == 2
    assert launcher.enqueued == [c.id]


@pytest.mark.asyncio
async def test_launcher_is_idempotent(db, launcher):
    """Beat and the inline poller both run this; it must not double-send."""
    c = await _campaign_with_contacts(db, datetime.now(timezone.utc) - timedelta(minutes=1))
    first = await launcher()
    second = await launcher()
    assert first == [c.id]
    assert second == []
    rows = (await db.execute(select(CampaignContact).where(CampaignContact.campaign_id == c.id))).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_campaign_without_a_schedule_is_untouched(db, launcher):
    """A validated campaign awaiting a manual Start must stay put."""
    c = await _campaign_with_contacts(db, None)
    assert await launcher() == []
    await db.refresh(c)
    assert c.status == "scheduled"


@pytest.mark.asyncio
async def test_draft_campaign_is_never_auto_launched(db, launcher):
    c = await _campaign_with_contacts(
        db, datetime.now(timezone.utc) - timedelta(minutes=1), status="draft"
    )
    assert await launcher() == []
    await db.refresh(c)
    assert c.status == "draft"


@pytest.mark.asyncio
async def test_unstartable_campaign_falls_back_to_draft(db, launcher, monkeypatch):
    """An invalid campaign must surface as draft, not retry every minute."""
    from app.services.campaign_service import CampaignService

    c = Campaign(
        name="Broken",
        status="scheduled",
        message_body="Hi",
        scheduled_start_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db.add(c)
    await db.flush()

    async def _boom(self, campaign_id):
        raise ValueError("Campaign has no contacts")

    monkeypatch.setattr(CampaignService, "start_campaign", _boom)
    assert await launcher() == []
    await db.refresh(c)
    assert c.status == "draft"
    assert c.scheduled_start_at is None


@pytest.mark.asyncio
async def test_two_concurrent_launchers_start_it_only_once(tmp_path, monkeypatch):
    """The real double-send risk: beat and the inline poller firing together.

    Uses a file-backed database so two independent sessions genuinely contend
    for the same row, which an in-memory database cannot reproduce.
    """
    import asyncio
    from contextlib import asynccontextmanager

    import app.tasks.campaign_tasks as ct

    url = f"sqlite+aiosqlite:///{tmp_path}/sched.db"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as setup:
        campaign = await _campaign_with_contacts(
            setup, datetime.now(timezone.utc) - timedelta(minutes=1)
        )
        await setup.commit()
        campaign_id = campaign.id

    enqueued: list[int] = []
    monkeypatch.setattr(ct, "async_session_factory", factory)
    monkeypatch.setattr(ct, "try_enqueue", lambda task, *a, **k: enqueued.append(a[0]) or True)

    results = await asyncio.gather(
        ct.launch_due_campaigns_async(),
        ct.launch_due_campaigns_async(),
        return_exceptions=True,
    )
    launched = [cid for r in results if isinstance(r, list) for cid in r]

    # Exactly one launcher may claim it, and the contacts must be queued once.
    assert launched == [campaign_id], f"expected one launch, got {results}"
    assert enqueued == [campaign_id]
    async with factory() as check:
        rows = (
            await check.execute(
                select(CampaignContact).where(CampaignContact.campaign_id == campaign_id)
            )
        ).scalars().all()
        assert len(rows) == 2
    await engine.dispose()


# --- schedule request validation ----------------------------------------


def test_past_launch_time_is_rejected():
    with pytest.raises(ValueError):
        CampaignScheduleRequest(
            scheduled_start_at=datetime.now(timezone.utc) - timedelta(hours=1)
        )


def test_future_launch_time_is_accepted():
    when = datetime.now(timezone.utc) + timedelta(days=1)
    assert CampaignScheduleRequest(scheduled_start_at=when).scheduled_start_at == when


def test_naive_datetime_is_treated_as_utc():
    """A datetime with no offset must not be read as server-local time."""
    naive = (datetime.now(timezone.utc) + timedelta(days=1)).replace(tzinfo=None)
    parsed = CampaignScheduleRequest(scheduled_start_at=naive).scheduled_start_at
    assert parsed.tzinfo is not None


def test_null_clears_the_schedule():
    assert CampaignScheduleRequest(scheduled_start_at=None).scheduled_start_at is None


# --- timestamps are serialised with an explicit UTC offset -----------------


def test_response_stamps_utc_on_naive_timestamps():
    """A naive datetime must not reach the browser without an offset.

    SQLite (and any column built without a timezone) returns naive datetimes.
    Serialised bare, the browser's `new Date(...)` reads them as *local* time,
    so a campaign set for 15:00 UTC is drawn at 15:00 in the user's own zone.
    """
    from app.schemas.campaign import CampaignOut

    naive = datetime(2030, 1, 2, 15, 0, 0)
    resp = CampaignOut(
        id=1, name="c", description=None, status="scheduled",
        list_id=None, template_id=None, message_body="hi", sequence_id=None,
        gateway_setting_id=None, sequence_version_id=None, daily_limit=0, hourly_limit=0,
        per_minute_limit=0, min_delay=0, max_delay=0,
        send_start_hour=None, send_end_hour=None, allow_weekends=True,
        total_contacts=0, messages_sent=0, messages_delivered=0,
        messages_failed=0, replies=0, interested=0,
        scheduled_at=naive, scheduled_start_at=naive,
        started_at=None, completed_at=None,
        created_at=naive, updated_at=naive,
    )
    assert resp.scheduled_start_at.tzinfo is not None
    assert resp.scheduled_start_at.utcoffset() == timedelta(0)
    assert "+00:00" in resp.scheduled_start_at.isoformat()


def test_response_leaves_aware_timestamps_alone():
    from app.schemas.campaign import CampaignOut

    aware = datetime(2030, 1, 2, 15, 0, 0, tzinfo=timezone.utc)
    resp = CampaignOut(
        id=1, name="c", description=None, status="scheduled",
        list_id=None, template_id=None, message_body="hi", sequence_id=None,
        gateway_setting_id=None, sequence_version_id=None, daily_limit=0, hourly_limit=0,
        per_minute_limit=0, min_delay=0, max_delay=0,
        send_start_hour=None, send_end_hour=None, allow_weekends=True,
        total_contacts=0, messages_sent=0, messages_delivered=0,
        messages_failed=0, replies=0, interested=0,
        scheduled_at=None, scheduled_start_at=aware,
        started_at=None, completed_at=None,
        created_at=aware, updated_at=aware,
    )
    assert resp.scheduled_start_at == aware
