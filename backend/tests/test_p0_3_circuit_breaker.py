"""P0-3 — the bounce circuit breaker.

A campaign that is hurting the sender's reputation has to stop on its own: the
QA sweep found a live campaign with a 45% failure rate and 525 contacts still
queued, and nothing in the product would ever have paused it.

The breaker watches a *rolling* window per campaign (both campaign systems),
pauses at a bounce rate above 2% or a spam-complaint rate above ~0.10%, writes
the reason on the campaign, and refuses an accidental resume.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.ads import AdsCampaign
from app.models.campaign import Campaign
from app.services import circuit_breaker, email_service
from tests.campaign_factory import make_account, make_campaign, seed_email_messages

NOW = datetime(2026, 9, 2, 11, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr(circuit_breaker, "_utcnow", lambda: NOW)


async def _running_campaign(db, **kw):
    return await make_campaign(db, status="running", channel="email", n=2, **kw)


async def _ads_campaign(db, status="active"):
    campaign = AdsCampaign(name="Ads cold", status=status, channel="email")
    db.add(campaign)
    await db.flush()
    return campaign


# --------------------------------------------------------------------------
# the numbers
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_synthetic_five_percent_bounce_run_pauses_the_campaign(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=5,
                              sent_at=NOW - timedelta(hours=3))

    outcome = await circuit_breaker.check_and_trip(api_db, campaign_id=campaign.id)

    assert outcome and outcome["tripped"] is True
    await api_db.refresh(campaign)
    assert campaign.status == "paused"
    assert campaign.paused_from is None, "it was paused mid-send, not while scheduled"
    reason = campaign.paused_reason
    assert reason.startswith("Circuit breaker")
    assert "5.0%" in reason and "2.0%" in reason and "5 of 100" in reason
    assert campaign.paused_at is not None


@pytest.mark.asyncio
async def test_the_same_run_pauses_an_ads_campaign_and_records_why(api_db):
    campaign = await _ads_campaign(api_db)
    await seed_email_messages(api_db, ads_campaign_id=campaign.id, sent=100, bounced=5,
                              sent_at=NOW - timedelta(hours=3))

    outcome = await circuit_breaker.check_and_trip(api_db, ads_campaign_id=campaign.id)

    assert outcome and outcome["tripped"] is True
    await api_db.refresh(campaign)
    assert campaign.status == "paused"
    assert campaign.paused_reason.startswith("Circuit breaker")
    assert "5.0%" in campaign.paused_reason


@pytest.mark.asyncio
async def test_one_percent_does_not_trip(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=1,
                              sent_at=NOW - timedelta(hours=3))

    outcome = await circuit_breaker.check_and_trip(api_db, campaign_id=campaign.id)

    assert not (outcome or {}).get("tripped")
    await api_db.refresh(campaign)
    assert campaign.status == "running"


@pytest.mark.asyncio
async def test_exactly_the_threshold_does_not_trip(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=2,
                              sent_at=NOW - timedelta(hours=3))
    assert not (await circuit_breaker.check_and_trip(api_db, campaign_id=campaign.id) or {}
                ).get("tripped"), "the limit is 'above 2%'"


@pytest.mark.asyncio
async def test_a_tiny_sample_cannot_trip_it(api_db):
    """1 bounce in 5 sends is 20%, and also meaningless: wait for a real sample."""
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=5, bounced=1,
                              sent_at=NOW - timedelta(hours=3))

    assert not (await circuit_breaker.check_and_trip(api_db, campaign_id=campaign.id) or {}
                ).get("tripped")


@pytest.mark.asyncio
async def test_old_bounces_roll_out_of_the_window(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=20,
                              sent_at=NOW - timedelta(days=30))
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=0,
                              sent_at=NOW - timedelta(hours=2))

    health = await circuit_breaker.campaign_health(api_db, campaign_id=campaign.id)

    assert health["sent"] == 100 and health["bounced"] == 0
    assert not (await circuit_breaker.check_and_trip(api_db, campaign_id=campaign.id) or {}
                ).get("tripped")


@pytest.mark.asyncio
async def test_provider_rejections_count_as_failures(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=50, rejected=50,
                              sent_at=NOW - timedelta(hours=2))

    outcome = await circuit_breaker.check_and_trip(api_db, campaign_id=campaign.id)

    assert outcome and outcome["tripped"] is True
    await api_db.refresh(campaign)
    assert "50.0%" in campaign.paused_reason


@pytest.mark.asyncio
async def test_messages_we_filtered_before_sending_are_not_provider_failures(api_db):
    from app.models.conversation import Message

    campaign = await _running_campaign(api_db)
    rows = await seed_email_messages(api_db, campaign_id=campaign.id, sent=100,
                                     rejected=40, sent_at=NOW - timedelta(hours=2))
    for row in rows:
        if row.status == "failed" and row.sent_at is None:
            row.last_error = "Filtered before send: unverified"
    await api_db.flush()

    assert not (await circuit_breaker.check_and_trip(api_db, campaign_id=campaign.id) or {}
                ).get("tripped"), "a refusal that never reached Brevo is not a bounce"


@pytest.mark.asyncio
async def test_spam_complaints_trip_at_a_tenth_of_a_percent(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=200, complaints=1,
                              sent_at=NOW - timedelta(hours=2))

    outcome = await circuit_breaker.check_and_trip(api_db, campaign_id=campaign.id)

    assert outcome and outcome["tripped"] is True
    await api_db.refresh(campaign)
    assert "complaint" in campaign.paused_reason.lower() and "0.10%" in campaign.paused_reason


@pytest.mark.asyncio
async def test_no_complaints_no_trip(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=200,
                              sent_at=NOW - timedelta(hours=2))
    assert not (await circuit_breaker.check_and_trip(api_db, campaign_id=campaign.id) or {}
                ).get("tripped")


@pytest.mark.asyncio
async def test_sms_campaigns_are_not_judged_by_email_bounces(api_db):
    campaign = await make_campaign(api_db, status="running", channel="sms")
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=50,
                              sent_at=NOW - timedelta(hours=2))
    assert (await circuit_breaker.check_and_trip(api_db, campaign_id=campaign.id)) is None


@pytest.mark.asyncio
async def test_only_a_sending_campaign_is_paused(api_db):
    campaign = await make_campaign(api_db, status="completed", channel="email")
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=50,
                              sent_at=NOW - timedelta(hours=2))
    assert (await circuit_breaker.check_and_trip(api_db, campaign_id=campaign.id)) is None
    await api_db.refresh(campaign)
    assert campaign.status == "completed"


@pytest.mark.asyncio
async def test_the_breaker_can_be_switched_off(api_db):
    from app.models.system import SystemSetting

    api_db.add(SystemSetting(key="breaker_enabled", value="false", category="sending_rules"))
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=50,
                              sent_at=NOW - timedelta(hours=2))
    assert (await circuit_breaker.check_and_trip(api_db, campaign_id=campaign.id)) is None


@pytest.mark.asyncio
async def test_the_sweep_pauses_every_offender_and_nobody_else(api_db):
    bad = await _running_campaign(api_db, name="Bad list")
    good = await _running_campaign(api_db, name="Good list")
    bad_ads = await _ads_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=bad.id, sent=100, bounced=10,
                              sent_at=NOW - timedelta(hours=2))
    await seed_email_messages(api_db, campaign_id=good.id, sent=100, bounced=0,
                              sent_at=NOW - timedelta(hours=2))
    await seed_email_messages(api_db, ads_campaign_id=bad_ads.id, sent=100, bounced=10,
                              sent_at=NOW - timedelta(hours=2))

    tripped = await circuit_breaker.check_all(api_db)

    assert {(t["kind"], t["id"]) for t in tripped} == {("campaign", bad.id), ("ads", bad_ads.id)}
    for row in (bad, good, bad_ads):
        await api_db.refresh(row)
    assert (bad.status, good.status, bad_ads.status) == ("paused", "running", "paused")


# --------------------------------------------------------------------------
# it trips from the bounce webhook, with nobody calling it
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bounce_webhooks_trip_the_breaker_by_themselves(api_db):
    campaign = await _running_campaign(api_db)
    rows = await seed_email_messages(api_db, campaign_id=campaign.id, sent=60,
                                     sent_at=NOW - timedelta(hours=2))
    sent_rows = [r for r in rows if r.provider_message_id]

    # 1 bounce in 60 is 1.7%: still fine. The second takes it to 3.3%.
    await email_service.apply_event(api_db, "bounce", {
        "message-id": sent_rows[0].provider_message_id, "email": "x@y.io",
        "event-id": "evt-1", "reason": "mailbox full", "type": "hard_bounce"})
    await api_db.refresh(campaign)
    assert campaign.status == "running"

    await email_service.apply_event(api_db, "bounce", {
        "message-id": sent_rows[1].provider_message_id, "email": "x2@y.io",
        "event-id": "evt-2", "reason": "no such user", "type": "hard_bounce"})
    await api_db.refresh(campaign)

    assert campaign.status == "paused"
    assert campaign.paused_reason.startswith("Circuit breaker")


# --------------------------------------------------------------------------
# resuming after a trip must be deliberate
# --------------------------------------------------------------------------


async def _tripped_classic(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=5,
                              sent_at=NOW - timedelta(hours=3))
    await circuit_breaker.check_and_trip(api_db, campaign_id=campaign.id)
    return campaign


@pytest.mark.asyncio
async def test_resume_after_a_trip_needs_acknowledgement(api_client, api_db, stub_queue):
    campaign = await _tripped_classic(api_db)

    refused = await api_client.post(f"/api/v1/campaigns/{campaign.id}/resume")

    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["code"] == "BREAKER_TRIPPED"
    assert "5.0%" in body["message"] and "acknowledge_breaker" in body["message"]
    assert body["paused_reason"].startswith("Circuit breaker")
    await api_db.refresh(campaign)
    assert campaign.status == "paused"
    assert stub_queue == []


@pytest.mark.asyncio
async def test_an_acknowledged_resume_restarts_the_window_instead_of_retripping(
    api_client, api_db, stub_queue, monkeypatch
):
    campaign = await _tripped_classic(api_db)

    resumed = await api_client.post(
        f"/api/v1/campaigns/{campaign.id}/resume", params={"acknowledge_breaker": "true"})

    assert resumed.status_code == 200, resumed.text
    await api_db.refresh(campaign)
    assert campaign.status == "running"
    assert campaign.paused_reason is None
    # The old bounces are behind us: judging the very same history again would
    # re-pause the campaign the instant it was resumed.
    assert not (await circuit_breaker.check_and_trip(api_db, campaign_id=campaign.id) or {}
                ).get("tripped")


@pytest.mark.asyncio
async def test_a_manual_pause_resumes_without_any_ceremony(api_client, api_db, stub_queue):
    campaign = await _running_campaign(api_db)
    assert (await api_client.post(f"/api/v1/campaigns/{campaign.id}/pause")).status_code == 200

    resumed = await api_client.post(f"/api/v1/campaigns/{campaign.id}/resume")

    assert resumed.status_code == 200, resumed.text


@pytest.mark.asyncio
async def test_ads_resume_after_a_trip_needs_acknowledgement_too(api_client, api_db):
    campaign = await _ads_campaign(api_db)
    await seed_email_messages(api_db, ads_campaign_id=campaign.id, sent=100, bounced=5,
                              sent_at=NOW - timedelta(hours=3))
    await circuit_breaker.check_and_trip(api_db, ads_campaign_id=campaign.id)

    refused = await api_client.post(f"/api/v1/ads/campaigns/{campaign.id}/resume")
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "BREAKER_TRIPPED"

    ok = await api_client.post(f"/api/v1/ads/campaigns/{campaign.id}/resume",
                               params={"acknowledge_breaker": "true"})
    assert ok.status_code == 200, ok.text
    await api_db.refresh(campaign)
    assert campaign.status == "active"


@pytest.mark.asyncio
async def test_launch_cannot_be_used_to_dodge_the_breaker(api_client, api_db):
    campaign = await _ads_campaign(api_db)
    await seed_email_messages(api_db, ads_campaign_id=campaign.id, sent=100, bounced=5,
                              sent_at=NOW - timedelta(hours=3))
    await circuit_breaker.check_and_trip(api_db, ads_campaign_id=campaign.id)

    response = await api_client.post(f"/api/v1/ads/campaigns/{campaign.id}/launch")

    assert response.status_code == 409, response.text
    await api_db.refresh(campaign)
    assert campaign.status == "paused"


@pytest.mark.asyncio
async def test_the_paused_reason_is_visible_in_the_api_and_the_directory(api_client, api_db):
    campaign = await _tripped_classic(api_db)

    got = (await api_client.get(f"/api/v1/campaigns/{campaign.id}")).json()
    assert got["status"] == "paused" and got["paused_reason"].startswith("Circuit breaker")

    directory = (await api_client.get("/api/v1/overview/campaigns/%d" % campaign.id,
                                      params={"kind": "campaign"})).json()
    assert directory["paused_reason"].startswith("Circuit breaker")


@pytest.mark.asyncio
async def test_stopping_a_campaign_cancels_its_queued_mail(api_client, api_db):
    from app.models.conversation import Message

    campaign = await _running_campaign(api_db)
    account = await make_account(api_db, default=True)
    rows = await seed_email_messages(api_db, campaign_id=campaign.id, account_id=account.id,
                                     in_flight=4)

    assert (await api_client.post(f"/api/v1/campaigns/{campaign.id}/stop")).status_code == 200

    for row in rows:
        await api_db.refresh(row)
        assert row.status == "cancelled"
