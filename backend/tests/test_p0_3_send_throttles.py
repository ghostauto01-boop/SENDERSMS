"""Campaign-level sending controls; no application-wide throttle remains."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.models.system import SystemSetting
from app.services import campaign_sending
from app.services.campaign_sending import CampaignSendingGate
from tests.campaign_factory import make_campaign, seed_email_messages

LAGOS = ZoneInfo("Africa/Lagos")
WED_NOON = datetime(2026, 9, 2, 12, 0, tzinfo=LAGOS).astimezone(timezone.utc)
WED_NIGHT = datetime(2026, 9, 2, 21, 0, tzinfo=LAGOS).astimezone(timezone.utc)
SAT_NOON = datetime(2026, 9, 5, 12, 0, tzinfo=LAGOS).astimezone(timezone.utc)


def _pin_campaign_clock(monkeypatch, now: datetime) -> None:
    monkeypatch.setattr(campaign_sending, "_utcnow", lambda: now)


@pytest.mark.asyncio
async def test_unconfigured_campaign_is_unrestricted_even_with_legacy_global_rules(
    api_db, monkeypatch
):
    api_db.add_all([
        SystemSetting(key="enable_daily_limit", value="true", category="sending_rules"),
        SystemSetting(key="daily_maximum", value="1", category="sending_rules"),
        SystemSetting(key="sending_start_time", value="09:00", category="sending_rules"),
        SystemSetting(key="sending_end_time", value="17:00", category="sending_rules"),
        SystemSetting(key="allow_weekends", value="false", category="sending_rules"),
    ])
    campaign = await make_campaign(api_db, channel="sms", daily_limit=None)
    _pin_campaign_clock(monkeypatch, SAT_NOON)

    gate = CampaignSendingGate(api_db, campaign)
    check = await gate.check()

    assert check["allowed"] is True
    assert check["reason"] is None
    assert await gate.room() is None


@pytest.mark.asyncio
async def test_campaign_window_is_optional_and_scoped_to_that_campaign(api_db, monkeypatch):
    unrestricted = await make_campaign(api_db, channel="sms")
    restricted = await make_campaign(
        api_db,
        channel="sms",
        send_start_hour=9,
        send_end_hour=17,
        allow_weekends=False,
    )
    _pin_campaign_clock(monkeypatch, WED_NIGHT)

    assert (await CampaignSendingGate(api_db, unrestricted).check())["allowed"] is True
    closed = await CampaignSendingGate(api_db, restricted).check()
    assert closed["allowed"] is False
    assert "sending window" in closed["reason"].lower()
    assert await CampaignSendingGate(api_db, restricted).room() == 0

    _pin_campaign_clock(monkeypatch, WED_NOON)
    assert (await CampaignSendingGate(api_db, restricted).check())["allowed"] is True


@pytest.mark.asyncio
async def test_campaign_cap_counts_only_that_campaigns_sent_and_queued_messages(
    api_db, monkeypatch
):
    campaign = await make_campaign(api_db, channel="sms", daily_limit=2)
    other = await make_campaign(api_db, channel="sms", daily_limit=2)
    now = WED_NOON
    _pin_campaign_clock(monkeypatch, now)
    await seed_email_messages(
        api_db,
        campaign_id=campaign.id,
        sent=1,
        in_flight=1,
        sent_at=now,
    )
    # Traffic from another campaign must not consume this campaign's allowance.
    await seed_email_messages(api_db, campaign_id=other.id, sent=100, sent_at=now)

    gate = CampaignSendingGate(api_db, campaign)
    check = await gate.check()

    assert check["allowed"] is False
    assert "daily limit" in check["reason"].lower()
    assert await gate.room() == 0


@pytest.mark.asyncio
async def test_campaign_delay_is_applied_only_when_the_campaign_configures_it(
    api_db, monkeypatch
):
    campaign = await make_campaign(api_db, channel="sms", min_delay=60, max_delay=60)
    now = WED_NOON
    _pin_campaign_clock(monkeypatch, now)
    await seed_email_messages(
        api_db,
        campaign_id=campaign.id,
        sent=1,
        sent_at=now - timedelta(seconds=10),
    )

    check = await CampaignSendingGate(api_db, campaign).check()

    assert check["allowed"] is False
    assert "delay" in check["reason"].lower()
    assert check["wait_seconds"] >= 5


@pytest.mark.asyncio
async def test_campaign_api_creates_and_updates_scoped_controls(api_client):
    created = await api_client.post(
        "/api/v1/campaigns/",
        json={
            "name": "Paced campaign",
            "channel": "sms",
            "daily_limit": 300,
            "hourly_limit": 40,
            "per_minute_limit": 5,
            "min_delay": 10,
            "max_delay": 25,
            "send_start_hour": 8,
            "send_end_hour": 19,
            "allow_weekends": False,
        },
    )
    assert created.status_code == 201, created.text
    campaign = created.json()
    assert campaign["daily_limit"] == 300
    assert campaign["hourly_limit"] == 40
    assert campaign["per_minute_limit"] == 5
    assert campaign["min_delay"] == 10 and campaign["max_delay"] == 25
    assert campaign["send_start_hour"] == 8 and campaign["send_end_hour"] == 19
    assert campaign["allow_weekends"] is False

    updated = await api_client.patch(
        f"/api/v1/campaigns/{campaign['id']}",
        json={"daily_limit": 500, "send_start_hour": None, "allow_weekends": True},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["daily_limit"] == 500
    assert body["send_start_hour"] is None
    assert body["allow_weekends"] is True


@pytest.mark.asyncio
async def test_campaign_start_reports_its_own_closed_window(
    api_client, api_db, monkeypatch, stub_queue, sms_gateway_configured
):
    campaign = await make_campaign(
        api_db,
        channel="sms",
        send_start_hour=9,
        send_end_hour=17,
        allow_weekends=True,
    )
    _pin_campaign_clock(monkeypatch, WED_NIGHT)

    response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/start")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "running"
    assert "campaign-level controls" in body["message"]
    assert "sending window" in body["waiting"]["reason"].lower()
    assert body["waiting"]["next_window"]


@pytest.mark.asyncio
async def test_retired_app_settings_sending_rules_endpoints_are_gone(api_client):
    response = await api_client.get("/api/v1/settings/sending-rules")
    assert response.status_code == 404
    status = await api_client.get("/api/v1/settings/sending-rules/status")
    assert status.status_code == 404
