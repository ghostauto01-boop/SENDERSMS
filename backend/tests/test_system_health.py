"""System worker/queue and Brevo sender health API coverage."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.ads import AdsCampaign, AdsCampaignJob


@pytest.fixture
def healthy_infrastructure(monkeypatch):
    from redis.asyncio import Redis
    from app.tasks.celery_app import celery_app

    class FakeRedis:
        async def ping(self):
            return True

        async def aclose(self):
            return None

    class FakeInspector:
        def ping(self):
            return {"worker-test@arena": {"ok": "pong"}}

    monkeypatch.setattr(Redis, "from_url", lambda *_args, **_kwargs: FakeRedis())
    monkeypatch.setattr(
        celery_app.control, "inspect", lambda **_kwargs: FakeInspector()
    )


@pytest.mark.asyncio
async def test_system_health_reports_database_redis_worker_and_persistent_queue(
    api_client, api_db, healthy_infrastructure
):
    response = await api_client.get("/api/v1/system/health")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["degraded"] is False
    assert body["components"]["database"]["ok"] is True
    assert body["components"]["redis"]["ok"] is True
    assert body["components"]["celery_workers"]["workers"] == ["worker-test@arena"]
    assert body["components"]["campaign_jobs"]["by_status"] == {}


@pytest.mark.asyncio
async def test_system_health_surfaces_due_stale_and_failed_jobs(api_client, api_db):
    campaign = AdsCampaign(name="Health", status="active", channel="sms", test_mode=True)
    api_db.add(campaign)
    await api_db.flush()
    now = datetime.now(timezone.utc)
    api_db.add_all([
        AdsCampaignJob(
            campaign_id=campaign.id, status="queued", scheduled_for=now - timedelta(minutes=1),
            updated_at=now,
        ),
        AdsCampaignJob(
            campaign_id=campaign.id, status="running", scheduled_for=now - timedelta(minutes=20),
            updated_at=now - timedelta(minutes=20),
        ),
        AdsCampaignJob(
            campaign_id=campaign.id, status="failed", attempts=5, max_retries=5,
            updated_at=now,
        ),
    ])
    await api_db.commit()

    response = await api_client.get("/api/v1/system/health")

    assert response.status_code == 200, response.text
    body = response.json()
    jobs = body["components"]["campaign_jobs"]
    assert body["ok"] is False
    assert jobs["ok"] is False
    assert jobs["due"] == 1
    assert jobs["stale_running"] == 1
    assert jobs["failed"] == 1
    assert "attention" in jobs["message"]


@pytest.mark.asyncio
async def test_brevo_health_shows_account_status_and_explicit_unlimited_limit(
    api_client, api_db
):
    from tests.campaign_factory import make_account

    account = await make_account(api_db, default=True, daily_limit=None)
    account.connection_status = "connected"
    account.last_tested_at = datetime.now(timezone.utc)
    account.last_error = None
    await api_db.commit()

    response = await api_client.get("/api/v1/email/health")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    sender = body["accounts"][0]
    assert sender["id"] == account.id
    assert sender["status"] == "connected"
    assert sender["daily_limit"] is None
    assert sender["daily_limit_unlimited"] is True
    assert "api_key" not in sender
