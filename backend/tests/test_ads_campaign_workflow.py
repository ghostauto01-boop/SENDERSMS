"""End-to-end Ads campaign preflight, lifecycle and persistent-job coverage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.ads import (
    AdsActivityLog,
    AdsAssignment,
    AdsCampaign,
    AdsCampaignJob,
    AdsCampaignStateTransition,
    AdsCreative,
    AdsEvent,
    AdsSet,
)
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.services import ads_service


@pytest_asyncio.fixture
async def workflow_db(monkeypatch):
    import app.models  # noqa: F401 - register every mapped table before create_all

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.services.campaign_jobs.async_session_factory", factory)
    async with factory() as db:
        yield db, factory
    await engine.dispose()


async def _valid_draft(db, *, name="Workflow campaign", start_date=None):
    contact = Contact(
        phone_number="+2348031234567",
        first_name="Ada",
        email="ada@example.org",
    )
    listing = ContactList(name="Workflow audience")
    db.add_all([contact, listing])
    await db.flush()
    db.add(ContactListMember(list_id=listing.id, contact_id=contact.id))

    campaign = AdsCampaign(
        name=name,
        objective="replies",
        status="draft",
        channel="sms",
        test_mode=True,
        daily_limit=None,
        start_date=start_date,
    )
    db.add(campaign)
    await db.flush()

    ads_set = AdsSet(
        campaign_id=campaign.id,
        name="Primary audience",
        status="active",
        list_ids=str(listing.id),
    )
    db.add(ads_set)
    await db.flush()

    creative = AdsCreative(
        campaign_id=campaign.id,
        set_id=ads_set.id,
        name="Welcome",
        body="Hello {{first_name}}",
        status="active",
        allocation=100,
    )
    db.add(creative)
    await db.flush()
    await ads_service.ensure_version(db, creative)
    return campaign, contact, ads_set, creative


@pytest.mark.asyncio
async def test_launch_blocks_invalid_campaign_and_keeps_actionable_diagnosis(workflow_db):
    db, _factory = workflow_db
    campaign = AdsCampaign(name="", status="draft", channel="sms", test_mode=True)
    db.add(campaign)
    await db.flush()

    result = await ads_service.launch(db, campaign, actor="qa")

    assert result["ok"] is False
    assert any("name is required" in error.lower() for error in result["errors"])
    assert campaign.status == "draft"
    assert campaign.last_error
    assert (await db.execute(select(AdsCampaignJob))).scalars().all() == []
    activity = (await db.execute(select(AdsActivityLog))).scalars().all()
    assert any(row.action == "campaign_validation_failed" for row in activity)


@pytest.mark.asyncio
async def test_launch_creates_audiences_transition_audit_and_durable_job(workflow_db):
    db, factory = workflow_db
    campaign, contact, _ads_set, _creative = await _valid_draft(db)

    result = await ads_service.launch(db, campaign, actor="qa")
    await db.commit()

    assert result["ok"] is True
    assert result["status"] == "active"
    assert campaign.state_changed_at is not None
    assignments = (await db.execute(
        select(AdsAssignment).where(AdsAssignment.campaign_id == campaign.id)
    )).scalars().all()
    assert len(assignments) == 1
    assert assignments[0].contact_id == contact.id

    transition = (await db.execute(
        select(AdsCampaignStateTransition).where(
            AdsCampaignStateTransition.campaign_id == campaign.id
        )
    )).scalar_one()
    assert (transition.from_state, transition.to_state, transition.actor) == ("draft", "active", "qa")
    assert "preflight passed" in transition.reason

    job = (await db.execute(
        select(AdsCampaignJob).where(AdsCampaignJob.campaign_id == campaign.id)
    )).scalar_one()
    assert job.status == "queued"
    assert job.scheduled_for is not None
    assert job.progress_total == 1

    # Re-launching is idempotent: it wakes the same persistent queue row.
    repeated = await ads_service.launch(db, campaign, actor="qa")
    await db.commit()
    assert repeated["idempotent"] is True
    assert repeated["job_id"] == job.id
    assert (await db.execute(select(AdsCampaignJob))).scalars().all() == [job]

    from app.services.campaign_jobs import process_campaign_job

    processed = await process_campaign_job(job.id, send_inline=True)
    assert processed["ok"] is True
    assert processed["sent"] == 1
    assert processed["job_status"] == "completed"
    await db.refresh(campaign)
    await db.refresh(assignments[0])
    assert assignments[0].send_status == "sent"
    assert campaign.sent_count == 1
    events = list((await db.execute(
        select(AdsEvent).where(AdsEvent.campaign_id == campaign.id)
    )).scalars().all())
    assert {event.event_type for event in events} == {"MESSAGE_QUEUED", "MESSAGE_SENT"}

    from app.api.v1.ads import campaign_send_logs

    send_logs = await campaign_send_logs(campaign.id, 1, 50, None, db, None)
    assert send_logs["summary"]["sent"] == 1
    assert send_logs["summary"]["total"] == 1
    assert send_logs["summary"]["queued"] == 0


@pytest.mark.asyncio
async def test_scheduled_launch_waits_then_worker_sends_without_provider_io(workflow_db):
    db, factory = workflow_db
    starts = datetime.now(timezone.utc) + timedelta(hours=2)
    campaign, _contact, _ads_set, _creative = await _valid_draft(db, start_date=starts)

    result = await ads_service.launch(db, campaign, actor="qa")
    await db.commit()

    assert result["status"] == "scheduled"
    job_id = result["job_id"]
    job = (await db.execute(
        select(AdsCampaignJob).where(AdsCampaignJob.id == job_id)
    )).scalar_one()
    assert job.scheduled_for is not None
    scheduled = job.scheduled_for
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=timezone.utc)
    assert scheduled > datetime.now(timezone.utc)

    from app.services.campaign_jobs import process_campaign_job

    early = await process_campaign_job(job_id, send_inline=True)
    assert early["claimed"] is False

    # A scheduled campaign cannot be dispatched early.
    async with factory() as worker_db:
        job = (await worker_db.execute(
            select(AdsCampaignJob).where(AdsCampaignJob.id == job_id)
        )).scalar_one()
        campaign = (await worker_db.execute(
            select(AdsCampaign).where(AdsCampaign.id == job.campaign_id)
        )).scalar_one()
        result = await ads_service.dispatch_campaign(worker_db, campaign, limit=25)
        assert result["sent"] == 0
        assert result["state"] == "scheduled"


@pytest.mark.asyncio
async def test_transition_rejects_illegal_move_and_records_reason(workflow_db):
    db, _factory = workflow_db
    campaign, *_ = await _valid_draft(db)
    campaign.status = "completed"

    with pytest.raises(ValueError, match="COMPLETED to ACTIVE"):
        ads_service.transition_campaign(db, campaign, "active", reason="must not reopen")

    ads_service.transition_campaign(db, campaign, "archived", reason="Campaign retired", actor="qa")
    assert campaign.status == "archived"
    assert campaign.state_changed_at is not None
    transition = (await db.execute(
        select(AdsCampaignStateTransition).where(
            AdsCampaignStateTransition.campaign_id == campaign.id
        )
    )).scalar_one()
    assert (transition.from_state, transition.to_state, transition.reason, transition.actor) == (
        "completed", "archived", "Campaign retired", "qa"
    )
