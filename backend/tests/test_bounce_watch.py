"""The bounce watchdog: it reports a bad bounce rate, it never stops a campaign.

This replaces the *bounce circuit breaker*. The breaker paused an email campaign
above 2% bounces and refused to resume it without ``acknowledge_breaker=true``,
which is exactly the behaviour that has to be gone: a campaign must not stop
itself halfway through its list.

So these tests assert two things together, every time: the operator is told,
**and the campaign keeps running**. Anything that pauses, blocks or demands an
acknowledgement is a regression.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.ads import AdsCampaign
from app.models.campaign import Campaign
from app.models.notification import NotificationEvent
from app.services import bounce_watch, email_service
from tests.campaign_factory import make_account, make_campaign, seed_email_messages

NOW = datetime(2026, 9, 2, 11, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr(bounce_watch, "_utcnow", lambda: NOW)


async def _running_campaign(db, **kw):
    return await make_campaign(db, status="running", channel="email", n=2, **kw)


async def _ads_campaign(db, status="active"):
    campaign = AdsCampaign(name="Ads cold", status=status, channel="email")
    db.add(campaign)
    await db.flush()
    return campaign


async def _alerts(db, campaign_id=None, ads_campaign_id=None):
    """Every bounce alert stored for one campaign."""
    rows = (
        await db.execute(
            select(NotificationEvent)
            .where(NotificationEvent.event_type == bounce_watch.ALERT_EVENT)
            .order_by(NotificationEvent.id)
        )
    ).scalars().all()
    if campaign_id is not None:
        rows = [r for r in rows if r.reference_type == "campaign" and r.reference_id == campaign_id]
    if ads_campaign_id is not None:
        rows = [r for r in rows if r.reference_type == "ads" and r.reference_id == ads_campaign_id]
    return rows


# --------------------------------------------------------------------------
# the numbers
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_synthetic_five_percent_bounce_run_alerts_without_stopping_the_campaign(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=5,
                              sent_at=NOW - timedelta(hours=3))

    outcome = await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id)

    assert outcome and outcome["alerted"] is True
    await api_db.refresh(campaign)
    assert campaign.status == "running", "a high bounce rate must never pause a campaign"
    assert campaign.paused_reason is None
    reason = outcome["reason"]
    assert "5.0%" in reason and "2.0%" in reason and "5 of 100" in reason

    (alert,) = await _alerts(api_db, campaign_id=campaign.id)
    assert "High bounce rate" in alert.title and campaign.name in alert.title
    assert "5.0%" in alert.body and "still sending" in alert.body
    assert alert.url == "/campaigns"


@pytest.mark.asyncio
async def test_the_same_run_alerts_on_an_ads_campaign_and_leaves_it_active(api_db):
    campaign = await _ads_campaign(api_db)
    await seed_email_messages(api_db, ads_campaign_id=campaign.id, sent=100, bounced=5,
                              sent_at=NOW - timedelta(hours=3))

    outcome = await bounce_watch.check_and_alert(api_db, ads_campaign_id=campaign.id)

    assert outcome and outcome["alerted"] is True
    await api_db.refresh(campaign)
    assert campaign.status == "active"
    assert campaign.paused_reason is None
    (alert,) = await _alerts(api_db, ads_campaign_id=campaign.id)
    assert alert.reference_type == "ads" and alert.reference_id == campaign.id
    assert alert.url == "/sms-manager"


@pytest.mark.asyncio
async def test_one_percent_does_not_alert(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=1,
                              sent_at=NOW - timedelta(hours=3))

    outcome = await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id)

    assert not (outcome or {}).get("alerted")
    assert await _alerts(api_db, campaign_id=campaign.id) == []


@pytest.mark.asyncio
async def test_exactly_the_threshold_does_not_alert(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=2,
                              sent_at=NOW - timedelta(hours=3))

    assert not (await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id) or {}
                ).get("alerted"), "the limit is 'above 2%'"


@pytest.mark.asyncio
async def test_a_tiny_sample_cannot_alert(api_db):
    """1 bounce in 5 sends is 20%, and also meaningless: wait for a real sample."""
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=5, bounced=1,
                              sent_at=NOW - timedelta(hours=3))

    assert not (await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id) or {}
                ).get("alerted")


@pytest.mark.asyncio
async def test_old_bounces_roll_out_of_the_window(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=20,
                              sent_at=NOW - timedelta(days=30))
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=0,
                              sent_at=NOW - timedelta(hours=2))

    health = await bounce_watch.campaign_health(api_db, campaign_id=campaign.id)

    assert health["sent"] == 100 and health["bounced"] == 0
    assert not (await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id) or {}
                ).get("alerted")


@pytest.mark.asyncio
async def test_provider_rejections_are_reported(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=50, rejected=50,
                              sent_at=NOW - timedelta(hours=2))

    outcome = await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id)

    assert outcome and outcome["alerted"] is True
    assert "50.0%" in outcome["reason"]
    await api_db.refresh(campaign)
    assert campaign.status == "running"


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

    assert not (await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id) or {}
                ).get("alerted"), "a refusal that never reached Brevo is not a bounce"


@pytest.mark.asyncio
async def test_spam_complaints_alert_at_a_tenth_of_a_percent(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=200, complaints=1,
                              sent_at=NOW - timedelta(hours=2))

    outcome = await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id)

    assert outcome and outcome["alerted"] is True
    assert "complaint" in outcome["reason"].lower() and "0.10%" in outcome["reason"]
    await api_db.refresh(campaign)
    assert campaign.status == "running"


@pytest.mark.asyncio
async def test_no_complaints_no_alert(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=200,
                              sent_at=NOW - timedelta(hours=2))
    assert not (await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id) or {}
                ).get("alerted")


@pytest.mark.asyncio
async def test_sms_campaigns_are_not_judged_by_email_bounces(api_db):
    campaign = await make_campaign(api_db, status="running", channel="sms")
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=50,
                              sent_at=NOW - timedelta(hours=2))
    assert (await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id)) is None


@pytest.mark.asyncio
async def test_a_campaign_that_is_not_sending_is_not_judged(api_db):
    campaign = await make_campaign(api_db, status="completed", channel="email")
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=50,
                              sent_at=NOW - timedelta(hours=2))
    assert (await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id)) is None
    await api_db.refresh(campaign)
    assert campaign.status == "completed"


@pytest.mark.asyncio
async def test_the_watch_can_be_switched_off(api_db):
    from app.models.system import SystemSetting

    api_db.add(SystemSetting(key="bounce_alert_enabled", value="false",
                             category="sending_rules"))
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=50,
                              sent_at=NOW - timedelta(hours=2))
    assert (await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id)) is None


@pytest.mark.asyncio
async def test_the_old_breaker_switch_still_switches_it_off(api_db):
    """An install that saved ``breaker_enabled=false`` keeps its choice."""
    from app.models.system import SystemSetting

    api_db.add(SystemSetting(key="breaker_enabled", value="false", category="sending_rules"))
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=50,
                              sent_at=NOW - timedelta(hours=2))
    assert (await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id)) is None


# --------------------------------------------------------------------------
# it does not nag
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_alert_per_cooldown_however_many_bounces_land(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=5,
                              sent_at=NOW - timedelta(hours=3))

    first = await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=5,
                              sent_at=NOW - timedelta(hours=1))
    second = await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id)

    assert first["alerted"] is True
    assert second["alerted"] is False and second.get("suppressed") is True
    assert len(await _alerts(api_db, campaign_id=campaign.id)) == 1, (
        "a bad list bounces on every send; that must not be one notification each"
    )


@pytest.mark.asyncio
async def test_it_alerts_again_once_the_cooldown_has_passed(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=5,
                              sent_at=NOW - timedelta(hours=3))
    await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id)

    (alert,) = await _alerts(api_db, campaign_id=campaign.id)
    alert.created_at = NOW - timedelta(hours=7)  # cooldown is 6h
    await api_db.flush()

    again = await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id)

    assert again["alerted"] is True
    assert len(await _alerts(api_db, campaign_id=campaign.id)) == 2


# --------------------------------------------------------------------------
# the sweep
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_sweep_alerts_on_every_offender_and_pauses_nobody(api_db):
    bad = await _running_campaign(api_db, name="Bad list")
    good = await _running_campaign(api_db, name="Good list")
    bad_ads = await _ads_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=bad.id, sent=100, bounced=10,
                              sent_at=NOW - timedelta(hours=2))
    await seed_email_messages(api_db, campaign_id=good.id, sent=100, bounced=0,
                              sent_at=NOW - timedelta(hours=2))
    await seed_email_messages(api_db, ads_campaign_id=bad_ads.id, sent=100, bounced=10,
                              sent_at=NOW - timedelta(hours=2))

    alerts = await bounce_watch.check_all(api_db)

    assert {(a["kind"], a["id"]) for a in alerts} == {("campaign", bad.id), ("ads", bad_ads.id)}
    for row in (bad, good, bad_ads):
        await api_db.refresh(row)
    assert (bad.status, good.status, bad_ads.status) == ("running", "running", "active")
    assert bad.paused_reason is None and bad_ads.paused_reason is None


# --------------------------------------------------------------------------
# it alerts from the bounce webhook, with nobody calling it
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bounce_webhooks_alert_by_themselves_without_pausing(api_db):
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
    assert await _alerts(api_db, campaign_id=campaign.id) == []

    await email_service.apply_event(api_db, "bounce", {
        "message-id": sent_rows[1].provider_message_id, "email": "x2@y.io",
        "event-id": "evt-2", "reason": "no such user", "type": "hard_bounce"})
    await api_db.refresh(campaign)

    assert campaign.status == "running", "the webhook must not stop the run"
    assert len(await _alerts(api_db, campaign_id=campaign.id)) == 1


# --------------------------------------------------------------------------
# nothing stands between an operator and their own campaign
# --------------------------------------------------------------------------


async def _alerting_classic(api_db):
    campaign = await _running_campaign(api_db)
    await seed_email_messages(api_db, campaign_id=campaign.id, sent=100, bounced=5,
                              sent_at=NOW - timedelta(hours=3))
    await bounce_watch.check_and_alert(api_db, campaign_id=campaign.id)
    return campaign


@pytest.mark.asyncio
async def test_a_manual_pause_resumes_without_any_ceremony(api_client, api_db, stub_queue):
    campaign = await _alerting_classic(api_db)
    assert (await api_client.post(f"/api/v1/campaigns/{campaign.id}/pause")).status_code == 200

    resumed = await api_client.post(f"/api/v1/campaigns/{campaign.id}/resume")

    assert resumed.status_code == 200, resumed.text
    await api_db.refresh(campaign)
    assert campaign.status == "running"


@pytest.mark.asyncio
async def test_start_is_never_refused_because_of_a_bounce_rate(api_client, api_db, stub_queue):
    campaign = await _alerting_classic(api_db)
    assert (await api_client.post(f"/api/v1/campaigns/{campaign.id}/pause")).status_code == 200

    started = await api_client.post(f"/api/v1/campaigns/{campaign.id}/start")

    assert started.status_code == 200, started.text
    await api_db.refresh(campaign)
    assert campaign.status == "running"


@pytest.mark.asyncio
async def test_ads_resume_and_launch_are_never_refused_either(api_client, api_db):
    campaign = await _ads_campaign(api_db)
    await seed_email_messages(api_db, ads_campaign_id=campaign.id, sent=100, bounced=5,
                              sent_at=NOW - timedelta(hours=3))
    await bounce_watch.check_and_alert(api_db, ads_campaign_id=campaign.id)
    from app.services import ads_service

    ads_service.transition_campaign(api_db, campaign, "paused", reason="Paused manually",
                                    actor="tester")
    campaign.paused_reason = "Paused manually"
    await api_db.flush()

    resumed = await api_client.post(f"/api/v1/ads/campaigns/{campaign.id}/resume")
    assert resumed.status_code == 200, resumed.text
    await api_db.refresh(campaign)
    assert campaign.status == "active"

    launched = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/launch")
    assert launched.status_code != 409, launched.text


@pytest.mark.asyncio
async def test_no_acknowledge_breaker_parameter_exists_any_more(api_client, api_db, stub_queue):
    """The escape hatch is gone, not merely unused."""
    campaign = await _alerting_classic(api_db)
    await api_client.post(f"/api/v1/campaigns/{campaign.id}/pause")

    resumed = await api_client.post(f"/api/v1/campaigns/{campaign.id}/resume",
                                    params={"acknowledge_breaker": "true"})

    assert resumed.status_code == 200, resumed.text


@pytest.mark.asyncio
async def test_stopping_a_campaign_still_cancels_its_queued_mail(api_client, api_db):
    from app.models.conversation import Message

    campaign = await _running_campaign(api_db)
    account = await make_account(api_db, default=True)
    rows = await seed_email_messages(api_db, campaign_id=campaign.id, account_id=account.id,
                                     in_flight=4)

    assert (await api_client.post(f"/api/v1/campaigns/{campaign.id}/stop")).status_code == 200

    for row in rows:
        await api_db.refresh(row)
        assert row.status == "cancelled"


def test_bounce_watch_sweep_is_scheduled_as_a_webhook_backstop():
    from app.tasks.celery_app import celery_app

    celery_app.loader.import_default_modules()
    entry = celery_app.conf.beat_schedule["watch-bounce-rates"]
    assert entry["task"] in celery_app.tasks
    assert entry["schedule"] <= timedelta(minutes=10)
