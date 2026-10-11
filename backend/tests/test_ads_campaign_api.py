"""HTTP contracts for Ads preflight, lifecycle scheduling and job retry."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.ads import AdsCampaign, AdsCampaignJob, AdsCampaignStateTransition


@pytest.mark.asyncio
async def test_invalid_launch_keeps_string_detail_shape_and_exposes_no_traceback(
    api_client, api_db
):
    campaign = AdsCampaign(name="", status="draft", channel="sms", test_mode=True)
    api_db.add(campaign)
    await api_db.commit()

    preflight = await api_client.post(f"/api/v1/ads/campaigns/{campaign.id}/validate")
    launch = await api_client.post(f"/api/v1/ads/campaigns/{campaign.id}/launch")

    assert preflight.status_code == 200
    assert preflight.json()["valid"] is False
    assert preflight.json()["checks"]
    assert launch.status_code == 400
    assert isinstance(launch.json()["detail"], str)
    assert "name is required" in launch.json()["detail"].lower()
    assert "traceback" not in launch.text.lower()
    await api_db.refresh(campaign)
    assert campaign.status == "draft"
    assert campaign.last_error


@pytest.mark.asyncio
async def test_resuming_a_paused_future_campaign_restores_scheduled_state(
    api_client, api_db, monkeypatch
):
    from app.services import campaign_jobs

    async def no_background_send(*_args, **_kwargs):
        return {"ok": True, "claimed": False}

    monkeypatch.setattr(campaign_jobs, "process_campaign_job", no_background_send)
    start = datetime.now(timezone.utc) + timedelta(hours=2)
    campaign = AdsCampaign(
        name="Future restart", status="paused", channel="sms", test_mode=True,
        start_date=start, paused_reason="Paused manually",
    )
    api_db.add(campaign)
    await api_db.commit()

    response = await api_client.post(f"/api/v1/ads/campaigns/{campaign.id}/resume")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "scheduled"
    await api_db.refresh(campaign)
    assert campaign.status == "scheduled"
    transition = (await api_db.execute(
        select(AdsCampaignStateTransition).where(
            AdsCampaignStateTransition.campaign_id == campaign.id
        )
    )).scalar_one()
    assert (transition.from_state, transition.to_state) == ("paused", "scheduled")
    job = (await api_db.execute(
        select(AdsCampaignJob).where(AdsCampaignJob.campaign_id == campaign.id)
    )).scalar_one()
    scheduled_for = job.scheduled_for
    if scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
    assert scheduled_for > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_failed_persistent_job_can_be_retried_without_changing_response_contract(
    api_client, api_db, monkeypatch
):
    from app.services import campaign_jobs

    async def no_background_send(*_args, **_kwargs):
        return {"ok": True, "claimed": False}

    monkeypatch.setattr(campaign_jobs, "process_campaign_job", no_background_send)
    campaign = AdsCampaign(
        name="Retry worker", status="active", channel="sms", test_mode=True
    )
    api_db.add(campaign)
    await api_db.flush()
    old_job = AdsCampaignJob(
        campaign_id=campaign.id, status="failed", attempts=5, max_retries=5,
        error="Worker exhausted retries.", finished_at=datetime.now(timezone.utc),
    )
    api_db.add(old_job)
    await api_db.commit()

    response = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/jobs/retry"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["job"]["status"] == "queued"
    assert body["job"]["attempts"] == 0
    assert body["job"]["error"] is None
    await api_db.refresh(old_job)
    assert old_job.status == "cancelled"
    status = await api_client.get(f"/api/v1/ads/campaigns/{campaign.id}/jobs")
    assert status.status_code == 200
    assert len(status.json()["jobs"]) == 2
