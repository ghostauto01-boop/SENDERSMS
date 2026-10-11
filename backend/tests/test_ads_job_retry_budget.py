"""Regression: a campaign job's retry budget counts FAILURES, not routine wake-ups.

A campaign that waits for its sending window, a daily cap or the next drip batch
is claimed again and again. Before the fix every claim consumed one of the five
retries, so after five ordinary wake-ups a single transient error marked the job
``failed`` for good, and ``create_missing_jobs`` never revived it. The campaign
stayed ``active`` with pending contacts and nothing was ever sent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.ads import AdsAssignment, AdsCampaign, AdsCampaignJob, AdsCreative, AdsSet
from app.models.contact import Contact
from app.services import ads_service
from app.services import campaign_jobs


@pytest_asyncio.fixture
async def job_db(monkeypatch):
    import app.models  # noqa: F401 - register every mapped table before create_all

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.services.campaign_jobs.async_session_factory", factory)
    # process_due_jobs imports the factory from app.database at call time.
    monkeypatch.setattr("app.database.async_session_factory", factory)

    async def _no_delivery_sync(db, campaign_id=None, **_):
        return 0

    monkeypatch.setattr(ads_service, "sync_delivery", _no_delivery_sync)
    async with factory() as db:
        contact = Contact(phone_number="+2348031234567", first_name="Ada", email="ada@example.org")
        db.add(contact)
        await db.flush()
        campaign = AdsCampaign(
            name="Waiting campaign", objective="replies", status="active", channel="email",
            test_mode=True,
        )
        db.add(campaign)
        await db.flush()
        ads_set = AdsSet(campaign_id=campaign.id, name="Set", status="active")
        db.add(ads_set)
        await db.flush()
        creative = AdsCreative(
            set_id=ads_set.id, campaign_id=campaign.id, name="A", body="Hi", status="active",
        )
        db.add(creative)
        await db.flush()
        db.add(AdsAssignment(
            campaign_id=campaign.id, set_id=ads_set.id, creative_id=creative.id,
            contact_id=contact.id, send_status="pending",
        ))
        job = AdsCampaignJob(
            campaign_id=campaign.id, job_type="send_campaign_batch", status="queued",
            max_retries=5, scheduled_for=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        db.add(job)
        await db.commit()
        job_id = job.id
    yield factory, job_id
    await engine.dispose()


async def _make_due(factory, job_id):
    async with factory() as db:
        job = await db.get(AdsCampaignJob, job_id)
        job.scheduled_for = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()


async def _job(factory, job_id) -> AdsCampaignJob:
    async with factory() as db:
        job = await db.get(AdsCampaignJob, job_id)
        # Detached copy of the columns the assertions need.
        return type("J", (), {
            "status": job.status, "attempts": job.attempts, "error": job.error,
            "scheduled_for": job.scheduled_for,
        })()


@pytest.mark.asyncio
async def test_waiting_wakeups_do_not_consume_the_retry_budget(job_db, monkeypatch):
    factory, job_id = job_db

    async def waiting(db, campaign, **_):
        return {"sent": 0, "skipped": 0, "state": "waiting_for_schedule", "reasons": {}}

    monkeypatch.setattr(ads_service, "dispatch_campaign", waiting)
    for _ in range(12):  # well past max_retries=5
        await _make_due(factory, job_id)
        outcome = await campaign_jobs.process_campaign_job(job_id)
        assert outcome["ok"] is True

    job = await _job(factory, job_id)
    assert job.status == "queued"
    assert job.attempts == 0


@pytest.mark.asyncio
async def test_transient_error_after_many_wakeups_is_retried_not_failed(job_db, monkeypatch):
    factory, job_id = job_db

    async def waiting(db, campaign, **_):
        return {"sent": 0, "skipped": 0, "state": "waiting_for_schedule", "reasons": {}}

    monkeypatch.setattr(ads_service, "dispatch_campaign", waiting)
    for _ in range(8):
        await _make_due(factory, job_id)
        await campaign_jobs.process_campaign_job(job_id)

    async def blip(db, campaign, **_):
        raise RuntimeError("transient database blip")

    monkeypatch.setattr(ads_service, "dispatch_campaign", blip)
    await _make_due(factory, job_id)
    outcome = await campaign_jobs.process_campaign_job(job_id)

    assert outcome["ok"] is False
    job = await _job(factory, job_id)
    assert job.status == "queued", job.status
    assert job.attempts == 1
    assert job.scheduled_for is not None  # a backoff retry time was set


@pytest.mark.asyncio
async def test_five_consecutive_failures_still_fail_the_job(job_db, monkeypatch):
    factory, job_id = job_db

    async def blip(db, campaign, **_):
        raise RuntimeError("provider is down")

    monkeypatch.setattr(ads_service, "dispatch_campaign", blip)
    for _ in range(5):
        await _make_due(factory, job_id)
        await campaign_jobs.process_campaign_job(job_id)

    job = await _job(factory, job_id)
    assert job.status == "failed"
    assert job.attempts == 5


@pytest.mark.asyncio
async def test_a_successful_slice_clears_the_failure_count(job_db, monkeypatch):
    factory, job_id = job_db

    async def blip(db, campaign, **_):
        raise RuntimeError("transient")

    async def waiting(db, campaign, **_):
        return {"sent": 0, "skipped": 0, "state": "waiting_for_schedule", "reasons": {}}

    monkeypatch.setattr(ads_service, "dispatch_campaign", blip)
    for _ in range(3):
        await _make_due(factory, job_id)
        await campaign_jobs.process_campaign_job(job_id)
    assert (await _job(factory, job_id)).attempts == 3

    monkeypatch.setattr(ads_service, "dispatch_campaign", waiting)
    await _make_due(factory, job_id)
    await campaign_jobs.process_campaign_job(job_id)

    job = await _job(factory, job_id)
    assert job.status == "queued"
    assert job.attempts == 0


@pytest.mark.asyncio
async def test_poller_picks_up_a_waiting_campaign_once_it_is_due(job_db, monkeypatch):
    """process_due_jobs (what the inline poller calls) keeps finding the job."""
    factory, job_id = job_db

    async def waiting(db, campaign, **_):
        return {"sent": 0, "skipped": 0, "state": "waiting_for_schedule", "reasons": {}}

    monkeypatch.setattr(ads_service, "dispatch_campaign", waiting)
    seen = []
    for _ in range(7):
        await _make_due(factory, job_id)
        summary = await campaign_jobs.process_due_jobs(send_inline=True)
        seen.append(summary["processed"])

    assert seen == [1] * 7
    assert (await _job(factory, job_id)).status == "queued"
