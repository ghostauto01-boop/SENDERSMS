"""P0-3 — outbound throttles are ON by default and cover email.

The QA sweep found every outbound throttle switched off: no daily cap, no sending
window, weekends allowed. Worse, the sending gate was never consulted for email at
all -- ``_send_one`` skipped it with the comment that email has "no SIM to
protect" -- so an email campaign could send its whole audience in minutes from a
single new mailbox. A mailbox has a reputation to protect, which is the same
thing a SIM has.

These tests pin: the defaults, the per-mailbox daily cap, that every producer of
email (classic campaigns, Ads Manager) stops at the cap and the window, that a
message beyond the cap is deferred rather than failed, that ``validate`` tells the
operator how long the audience will take, and that paused campaigns send nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.models.campaign import Campaign, CampaignContact
from app.models.conversation import Message
from app.models.system import SystemSetting
from app.services import sending_limits
from app.services.sending_limits import SendingGate, get_sending_rules
from tests.campaign_factory import (
    make_account, make_accounts, make_campaign, make_list, seed_email_messages,
)

pytestmark = pytest.mark.real_sending_defaults

LAGOS = ZoneInfo("Africa/Lagos")
WED_NOON = datetime(2026, 9, 2, 12, 0, tzinfo=LAGOS)
WED_NIGHT = datetime(2026, 9, 2, 21, 0, tzinfo=LAGOS)
WED_EARLY = datetime(2026, 9, 2, 7, 30, tzinfo=LAGOS)
SAT_NOON = datetime(2026, 9, 5, 12, 0, tzinfo=LAGOS)
THU_NOON = datetime(2026, 9, 3, 12, 0, tzinfo=LAGOS)


async def _set(db, key, value):
    db.add(SystemSetting(key=key, value=str(value), category="sending_rules"))
    await db.flush()


# --------------------------------------------------------------------------
# the defaults
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defaults_are_on(api_db):
    rules = await get_sending_rules(api_db)

    assert rules["enable_daily_limit"] is True
    assert rules["sending_start_time"] == "09:00"
    assert rules["sending_end_time"] == "17:00"
    assert rules["allow_weekends"] is False
    assert 20 <= rules["email_daily_per_mailbox"] <= 50
    assert rules["breaker_enabled"] is True
    assert rules["breaker_bounce_pct"] == 2.0
    assert rules["breaker_complaint_pct"] == pytest.approx(0.10)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["email", "sms"])
async def test_gate_blocks_nights_and_weekends_by_default(api_db, channel):
    gate = SendingGate(api_db, channel=channel)

    assert (await gate.check(now=WED_NOON.astimezone(timezone.utc)))["allowed"] is True

    night = await gate.check(now=WED_NIGHT.astimezone(timezone.utc))
    assert night["allowed"] is False
    assert "outside sending hours" in night["reason"]
    assert night["next_window"], "the operator must be told when sending resumes"

    early = await gate.check(now=WED_EARLY.astimezone(timezone.utc))
    assert early["allowed"] is False

    weekend = await gate.check(now=SAT_NOON.astimezone(timezone.utc))
    assert weekend["allowed"] is False
    assert "weekend" in weekend["reason"]


@pytest.mark.asyncio
async def test_clearing_the_window_is_still_an_explicit_opt_out(api_db):
    """The window was once impossible to switch off; it must stay switch-off-able."""
    await _set(api_db, "sending_start_time", "")
    await _set(api_db, "sending_end_time", "")
    await _set(api_db, "allow_weekends", "true")

    check = await SendingGate(api_db, channel="email").check(
        now=SAT_NOON.astimezone(timezone.utc).replace(hour=2)
    )
    assert check["allowed"] is True, check["reason"]


# --------------------------------------------------------------------------
# the email cap follows the number of mailboxes
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_daily_cap_is_per_mailbox_times_mailbox_count(api_db):
    one = await make_account(api_db, default=True)
    capacity = await sending_limits.email_capacity(api_db)
    assert capacity["mailboxes"] == 1
    per_mailbox = capacity["per_mailbox"]
    assert capacity["daily_cap"] == per_mailbox

    await make_account(api_db)
    capacity = await sending_limits.email_capacity(api_db)
    assert capacity["mailboxes"] == 2
    assert capacity["daily_cap"] == 2 * per_mailbox
    assert one.id


@pytest.mark.asyncio
async def test_a_disabled_or_unusable_mailbox_adds_no_capacity(api_db):
    await make_account(api_db, default=True)
    await make_account(api_db, active=False)
    capacity = await sending_limits.email_capacity(api_db)
    assert capacity["mailboxes"] == 1


@pytest.mark.asyncio
async def test_an_accounts_own_limit_can_only_lower_the_default(api_db):
    await make_account(api_db, default=True, daily_limit=10)
    capacity = await sending_limits.email_capacity(api_db)
    assert capacity["daily_cap"] == 10

    await make_account(api_db, daily_limit=5000)
    capacity = await sending_limits.email_capacity(api_db)
    assert capacity["daily_cap"] == 10 + capacity["per_mailbox"], (
        "a generous provider quota must not raise the protective default"
    )


@pytest.mark.asyncio
async def test_the_gate_blocks_email_once_the_daily_cap_is_used(api_db, sending_clock):
    sending_clock(WED_NOON)
    await make_account(api_db, default=True)
    cap = (await sending_limits.email_capacity(api_db))["daily_cap"]
    await seed_email_messages(api_db, campaign_id=1, sent=cap,
                              sent_at=WED_NOON.astimezone(timezone.utc) - timedelta(minutes=10))

    check = await SendingGate(api_db, channel="email").check()

    assert check["allowed"] is False
    assert f"daily limit ({cap}) reached" in check["reason"]
    assert check["next_window"], "tomorrow's window start is reported"


@pytest.mark.asyncio
async def test_email_and_sms_volume_are_counted_separately(api_db, sending_clock):
    sending_clock(WED_NOON)
    await make_account(api_db, default=True)
    cap = (await sending_limits.email_capacity(api_db))["daily_cap"]
    await seed_email_messages(api_db, campaign_id=1, sent=cap,
                              sent_at=WED_NOON.astimezone(timezone.utc) - timedelta(minutes=10))

    sms = await SendingGate(api_db, channel="sms").check()

    assert sms["allowed"] is True, "email volume must not eat the SMS allowance"


@pytest.mark.asyncio
async def test_room_is_the_allowance_left_after_sent_and_in_flight(api_db, sending_clock):
    sending_clock(WED_NOON)
    await make_account(api_db, default=True)
    cap = (await sending_limits.email_capacity(api_db))["daily_cap"]
    gate = SendingGate(api_db, channel="email")
    assert await gate.room() == cap

    await seed_email_messages(api_db, campaign_id=1, sent=5, in_flight=3,
                              sent_at=WED_NOON.astimezone(timezone.utc) - timedelta(hours=1))
    assert await gate.room() == cap - 8, "queued mail will count against today when it goes"


@pytest.mark.asyncio
async def test_room_is_zero_outside_the_window(api_db, sending_clock):
    sending_clock(WED_NIGHT)
    await make_account(api_db, default=True)
    assert await SendingGate(api_db, channel="email").room() == 0


# --------------------------------------------------------------------------
# producers stop at the cap and the window
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def batch(api_db, monkeypatch):
    """The classic campaign engine bound to the test database, with a stub broker."""
    import app.tasks.campaign_tasks as ct
    from contextlib import asynccontextmanager

    published: list[int] = []

    @asynccontextmanager
    async def _factory():
        yield api_db

    monkeypatch.setattr(ct, "async_session_factory", _factory)
    monkeypatch.setattr(ct, "enqueue", lambda task, *a, **k: published.append(a[0]))

    async def _run(campaign_id, batch_size=50):
        return await ct.process_campaign_batch_async(campaign_id, batch_size=batch_size)

    _run.published = published
    return _run


async def _running_email_campaign(db, account, n):
    campaign = await make_campaign(db, status="running", channel="email", n=n,
                                   email_account_id=account.id)
    members = (await db.execute(
        select(Campaign.list_id).where(Campaign.id == campaign.id))).scalar_one()
    from app.models.contact_list import ContactListMember
    contact_ids = (await db.execute(
        select(ContactListMember.contact_id).where(ContactListMember.list_id == members)
    )).scalars().all()
    for cid in contact_ids:
        db.add(CampaignContact(campaign_id=campaign.id, contact_id=cid, status="pending"))
    campaign.total_contacts = len(contact_ids)
    await db.flush()
    return campaign


async def _message_count(db, campaign_id):
    return (await db.execute(
        select(func.count(Message.id)).where(Message.campaign_id == campaign_id)
    )).scalar_one()


@pytest.mark.asyncio
async def test_a_classic_email_campaign_stops_at_the_daily_cap(api_db, batch, sending_clock):
    sending_clock(WED_NOON)
    account = await make_account(api_db, default=True)
    cap = (await sending_limits.email_capacity(api_db))["daily_cap"]
    campaign = await _running_email_campaign(api_db, account, cap + 20)

    await batch(campaign.id, batch_size=cap + 20)
    assert await _message_count(api_db, campaign.id) == cap, "exactly one day's allowance"

    await batch(campaign.id, batch_size=cap + 20)
    assert await _message_count(api_db, campaign.id) == cap, (
        "a second pass must not create more: the first batch is still in flight"
    )
    pending = (await api_db.execute(
        select(func.count(CampaignContact.id)).where(
            CampaignContact.campaign_id == campaign.id, CampaignContact.status == "pending")
    )).scalar_one()
    assert pending == 20


@pytest.mark.asyncio
async def test_the_rest_goes_out_on_the_next_sending_day(api_db, batch, sending_clock):
    sending_clock(WED_NOON)
    account = await make_account(api_db, default=True)
    cap = (await sending_limits.email_capacity(api_db))["daily_cap"]
    campaign = await _running_email_campaign(api_db, account, cap + 20)
    await batch(campaign.id, batch_size=cap + 20)
    # Wednesday's mail is accepted by the provider.
    await api_db.execute(
        Message.__table__.update().where(Message.campaign_id == campaign.id).values(
            status="sent", sent_at=WED_NOON.astimezone(timezone.utc))
    )
    await api_db.flush()

    sending_clock(THU_NOON)
    await batch(campaign.id, batch_size=cap + 20)

    assert await _message_count(api_db, campaign.id) == cap + 20


@pytest.mark.asyncio
async def test_an_email_campaign_started_after_hours_creates_nothing_and_stays_running(
    api_db, batch, sending_clock
):
    sending_clock(WED_NIGHT)
    account = await make_account(api_db, default=True)
    campaign = await _running_email_campaign(api_db, account, 5)

    processed = await batch(campaign.id)

    assert processed == 0
    assert await _message_count(api_db, campaign.id) == 0
    await api_db.refresh(campaign)
    assert campaign.status == "running", "waiting for the window is not completion"


@pytest.mark.asyncio
async def test_a_weekend_start_waits_for_monday(api_db, batch, sending_clock):
    sending_clock(SAT_NOON)
    account = await make_account(api_db, default=True)
    campaign = await _running_email_campaign(api_db, account, 5)

    assert await batch(campaign.id) == 0
    assert await _message_count(api_db, campaign.id) == 0


@pytest.mark.asyncio
async def test_a_paused_campaign_creates_nothing(api_db, batch, sending_clock):
    sending_clock(WED_NOON)
    account = await make_account(api_db, default=True)
    campaign = await _running_email_campaign(api_db, account, 5)
    campaign.status = "paused"
    await api_db.flush()

    assert await batch(campaign.id) == 0
    assert await _message_count(api_db, campaign.id) == 0


@pytest.mark.asyncio
async def test_an_email_campaign_with_its_own_message_and_no_template_can_queue_mail(
    api_db, batch, sending_clock
):
    """Regression: this used to raise UnboundLocalError on the very first contact.

    ``template`` was only bound when the campaign lacked a subject or HTML of its
    own, yet the message read its attachments either way -- so any email campaign
    that carried its own text and had no template (and no attachments) crashed the
    batch, and never sent. Found while reproducing P0-3 (fixing it without the
    throttles would have unleashed whatever the crash was holding back).
    """
    sending_clock(WED_NOON)
    account = await make_account(api_db, default=True)
    campaign = await _running_email_campaign(api_db, account, 3)
    assert campaign.template_id is None and campaign.message_body

    processed = await batch(campaign.id)

    assert processed == 3
    assert await _message_count(api_db, campaign.id) == 3


# --- Ads Manager ----------------------------------------------------------


async def _active_ads_email_campaign(db, account, n, **kw):
    from tests.test_ads_manager import make_campaign as ads_campaign
    from tests.test_ads_manager import make_creatives, make_set
    from app.models.ads import AdsAssignment
    from app.models.contact import Contact

    campaign = await ads_campaign(db, name="Ads email", channel="email", status="active",
                                  test_mode=False, email_account_id=account.id,
                                  subject="Hello", **kw)
    ads_set = await make_set(db, campaign)
    creatives = await make_creatives(db, ads_set, ["A"])
    for i in range(n):
        contact = Contact(first_name=f"P{i}", email=f"adsperson{i}@leadco{i}.io",
                          email_verified=True, email_verified_at=datetime.now(timezone.utc),
                          email_confidence=95)
        db.add(contact)
        await db.flush()
        db.add(AdsAssignment(campaign_id=campaign.id, set_id=ads_set.id,
                             creative_id=creatives[0].id, contact_id=contact.id,
                             send_status="pending"))
    await db.flush()
    return campaign


@pytest.mark.asyncio
async def test_an_ads_email_campaign_stops_at_the_daily_cap(api_db, monkeypatch, sending_clock):
    from app.services import ads_service as svc

    monkeypatch.setattr("app.tasks.queue.try_enqueue", lambda *a, **k: True)
    sending_clock(WED_NOON)
    account = await make_account(api_db, default=True)
    cap = (await sending_limits.email_capacity(api_db))["daily_cap"]
    campaign = await _active_ads_email_campaign(api_db, account, cap + 15)

    first = await svc.dispatch_campaign(api_db, campaign, limit=cap + 15)
    second = await svc.dispatch_campaign(api_db, campaign, limit=cap + 15)

    assert first["sent"] == cap
    assert second["sent"] == 0, "the first slice is still in flight and counts against today"


@pytest.mark.asyncio
async def test_an_ads_email_campaign_waits_for_the_window(api_db, monkeypatch, sending_clock):
    from app.services import ads_service as svc

    monkeypatch.setattr("app.tasks.queue.try_enqueue", lambda *a, **k: True)
    sending_clock(SAT_NOON)
    account = await make_account(api_db, default=True)
    campaign = await _active_ads_email_campaign(api_db, account, 5)

    result = await svc.dispatch_campaign(api_db, campaign, limit=25)

    assert result["sent"] == 0


@pytest.mark.asyncio
async def test_a_paused_ads_campaign_with_pending_rows_sends_nothing(
    api_db, monkeypatch, sending_clock
):
    """P0-0's mechanism, pinned: pause really does stop the dispatcher."""
    from app.services import ads_service as svc

    monkeypatch.setattr("app.tasks.queue.try_enqueue", lambda *a, **k: True)
    sending_clock(WED_NOON)
    account = await make_account(api_db, default=True)
    campaign = await _active_ads_email_campaign(api_db, account, 10)
    campaign.status = "paused"
    await api_db.flush()

    result = await svc.dispatch_campaign(api_db, campaign, limit=25)

    assert result["sent"] == 0
    assert result["state"] == "paused"
    assert (await api_db.execute(select(func.count(Message.id)))).scalar_one() == 0


# --- the safety net at send time -------------------------------------------


@pytest_asyncio.fixture
async def sender(api_db, monkeypatch):
    """``_send_one`` bound to the test database."""
    import app.tasks.sms_tasks as st
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():
        yield api_db

    monkeypatch.setattr(st, "async_session_factory", _factory)
    return st._send_one


async def _queued_email(db, account, campaign_id=None, ads_campaign_id=None, bulk=True):
    rows = await seed_email_messages(db, campaign_id=campaign_id, ads_campaign_id=ads_campaign_id,
                                     account_id=account.id, in_flight=1, bulk=bulk)
    return rows[0]


@pytest.mark.asyncio
async def test_a_message_beyond_the_cap_is_deferred_not_failed(api_db, sender, sending_clock,
                                                                monkeypatch):
    sending_clock(WED_NOON)
    account = await make_account(api_db, default=True)
    cap = (await sending_limits.email_capacity(api_db))["daily_cap"]
    campaign = await make_campaign(api_db, status="running", channel="email")
    await seed_email_messages(api_db, campaign_id=campaign.id, account_id=account.id, sent=cap,
                              sent_at=WED_NOON.astimezone(timezone.utc) - timedelta(minutes=5))
    message = await _queued_email(api_db, account, campaign_id=campaign.id)

    called = []

    async def fake_deliver(db, m):
        called.append(m.id)
        return {"success": True}

    monkeypatch.setattr("app.services.email_service.deliver", fake_deliver)

    wait = await sender(message.id)

    assert called == [], "nothing may be handed to Brevo past the cap"
    assert isinstance(wait, int) and wait > 0, "the caller is told when to try again"
    await api_db.refresh(message)
    assert message.status == "queued"
    assert message.retry_count == 0, "a deferral must not burn the retry budget"
    assert "daily limit" in (message.last_error or "")


@pytest.mark.asyncio
async def test_a_one_to_one_reply_is_not_throttled(api_db, sender, sending_clock, monkeypatch):
    """Replying to a person who wrote to us is not outreach; it ignores the cap/window."""
    sending_clock(WED_NIGHT)
    account = await make_account(api_db, default=True)
    message = await _queued_email(api_db, account, bulk=False)

    async def fake_deliver(db, m):
        # What the real deliver() records on a Brevo 2xx.
        m.status = "sent"
        m.sent_at = datetime.now(timezone.utc)
        return {"success": True}

    monkeypatch.setattr("app.services.email_service.deliver", fake_deliver)

    assert await sender(message.id) is False
    await api_db.refresh(message)
    assert message.status in ("sent", "delivered")


@pytest.mark.asyncio
async def test_a_queued_message_of_a_paused_campaign_is_held_not_sent(
    api_db, sender, sending_clock, monkeypatch
):
    sending_clock(WED_NOON)
    account = await make_account(api_db, default=True)
    campaign = await make_campaign(api_db, status="paused", channel="email")
    message = await _queued_email(api_db, account, campaign_id=campaign.id)
    called = []

    async def fake_deliver(db, m):
        called.append(m.id)
        return {"success": True}

    monkeypatch.setattr("app.services.email_service.deliver", fake_deliver)

    result = await sender(message.id)

    assert called == []
    assert result is False
    await api_db.refresh(message)
    assert message.status == "queued"
    assert "paused" in (message.last_error or "").lower()


@pytest.mark.asyncio
async def test_a_queued_message_of_a_stopped_campaign_is_cancelled(
    api_db, sender, sending_clock, monkeypatch
):
    sending_clock(WED_NOON)
    account = await make_account(api_db, default=True)
    campaign = await make_campaign(api_db, status="stopped", channel="email")
    message = await _queued_email(api_db, account, campaign_id=campaign.id)

    assert await sender(message.id) is False
    await api_db.refresh(message)
    assert message.status == "cancelled"


# --------------------------------------------------------------------------
# validate reports how long the audience will take
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_reports_projected_duration_for_a_925_contact_email_audience(
    api_client, api_db, sending_clock
):
    sending_clock(WED_NOON)
    await make_account(api_db, default=True)
    campaign = await make_campaign(api_db, channel="email", n=925)

    response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/validate")

    assert response.status_code == 200, response.text
    body = response.json()
    projection = body["projection"]
    per_mailbox = (await sending_limits.email_capacity(api_db))["per_mailbox"]
    assert projection["sendable"] == 925
    assert projection["daily_cap"] == per_mailbox
    assert projection["mailboxes"] == 1
    expected_days = -(-925 // per_mailbox)
    assert projection["sending_days_needed"] == expected_days
    assert projection["calendar_days"] > expected_days, "weekends are off, so it takes longer"
    assert projection["projected_finish"]
    assert projection["window"].startswith("09:00")
    assert any(f"{expected_days} sending days" in w for w in body["warnings"]), body["warnings"]


@pytest.mark.asyncio
async def test_validate_projection_names_the_missing_sender(api_client, api_db, sending_clock):
    sending_clock(WED_NOON)
    campaign = await make_campaign(api_db, channel="email", n=10)

    body = (await api_client.post(f"/api/v1/campaigns/{campaign.id}/validate")).json()

    assert body["valid"] is False
    assert any("sender" in e.lower() for e in body["errors"])


@pytest.mark.asyncio
async def test_validate_reports_the_sms_projection_too(api_client, api_db, sending_clock,
                                                       sms_gateway_configured):
    sending_clock(WED_NOON)
    campaign = await make_campaign(api_db, channel="sms", n=50)

    projection = (await api_client.post(f"/api/v1/campaigns/{campaign.id}/validate")).json()[
        "projection"]

    assert projection["sendable"] == 50
    assert projection["daily_cap"] == 1000
    assert projection["sending_days_needed"] == 1


@pytest.mark.asyncio
async def test_ads_validate_reports_the_projection(api_client, api_db, sending_clock):
    sending_clock(WED_NOON)
    account = await make_account(api_db, default=True)
    campaign = await _active_ads_email_campaign(api_db, account, 100)
    campaign.status = "draft"
    await api_db.flush()

    body = (await api_client.post(f"/api/v1/ads/campaigns/{campaign.id}/validate")).json()

    projection = body["summary"]["projection"]
    assert projection["daily_cap"] == (await sending_limits.email_capacity(api_db))["per_mailbox"]
    assert projection["sending_days_needed"] >= 2


# --------------------------------------------------------------------------
# the rules API
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sending_rules_api_exposes_the_new_rules_and_the_effective_email_cap(
    api_client, api_db
):
    await make_accounts(api_db, 2)

    rules = (await api_client.get("/api/v1/settings/sending-rules")).json()
    assert rules["email_daily_per_mailbox"] >= 20
    assert rules["breaker_enabled"] is True

    status = (await api_client.get("/api/v1/settings/sending-rules/status")).json()
    assert status["email"]["mailboxes"] == 2
    assert status["email"]["daily_cap"] == 2 * rules["email_daily_per_mailbox"]
    assert "breaker" in status


@pytest.mark.asyncio
async def test_sending_rules_can_be_changed_and_are_validated(api_client, api_db):
    ok = await api_client.put(
        "/api/v1/settings/sending-rules",
        params={"email_daily_per_mailbox": 40, "breaker_bounce_pct": 3.5,
                "breaker_min_sample": 30},
    )
    assert ok.status_code == 200, ok.text
    rules = (await api_client.get("/api/v1/settings/sending-rules")).json()
    assert rules["email_daily_per_mailbox"] == 40
    assert rules["breaker_bounce_pct"] == 3.5
    assert rules["breaker_min_sample"] == 30

    for bad in ({"email_daily_per_mailbox": 0}, {"email_daily_per_mailbox": 100000},
                {"breaker_bounce_pct": 0}, {"breaker_bounce_pct": 80},
                {"breaker_min_sample": 0}):
        response = await api_client.put("/api/v1/settings/sending-rules", params=bad)
        assert response.status_code == 422, (bad, response.text)


# --------------------------------------------------------------------------
# existing deployments: one-time safe-defaults migration
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_defaults_are_applied_once_when_every_throttle_is_off(api_db):
    for key, value in {
        "enable_daily_limit": "false", "enable_hourly_limit": "false",
        "enable_per_minute_limit": "false", "sending_start_time": "",
        "sending_end_time": "", "allow_weekends": "true",
    }.items():
        await _set(api_db, key, value)

    result = await sending_limits.apply_safe_defaults(api_db)

    assert result["applied"] is True
    rules = await get_sending_rules(api_db)
    assert rules["enable_daily_limit"] is True
    assert rules["sending_start_time"] == "09:00" and rules["sending_end_time"] == "17:00"
    assert rules["allow_weekends"] is False

    # The operator switches the window off again: that choice must stick.
    await _set_value(api_db, "sending_start_time", "")
    await _set_value(api_db, "sending_end_time", "")
    again = await sending_limits.apply_safe_defaults(api_db)
    assert again["applied"] is False and again["reason"] == "already_applied"
    assert (await get_sending_rules(api_db))["sending_start_time"] == ""


async def _set_value(db, key, value):
    row = (await db.execute(select(SystemSetting).where(SystemSetting.key == key))).scalar_one()
    row.value = value
    await db.flush()


@pytest.mark.asyncio
async def test_an_operators_own_configuration_is_never_overwritten(api_db):
    await _set(api_db, "enable_daily_limit", "true")
    await _set(api_db, "daily_maximum", "75")
    await _set(api_db, "sending_start_time", "")
    await _set(api_db, "sending_end_time", "")
    await _set(api_db, "allow_weekends", "true")

    result = await sending_limits.apply_safe_defaults(api_db)

    assert result["applied"] is False and result["reason"] == "operator_configured"
    rules = await get_sending_rules(api_db)
    assert rules["daily_maximum"] == 75
    assert rules["sending_start_time"] == "", "their open window is theirs to keep"
    assert rules["allow_weekends"] is True


@pytest.mark.asyncio
async def test_a_fresh_install_needs_no_migration(api_db):
    result = await sending_limits.apply_safe_defaults(api_db)
    assert result["applied"] is False and result["reason"] == "defaults_already_safe"


# --------------------------------------------------------------------------
# the operator is told when a started campaign is waiting, and why
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_starting_after_hours_says_nothing_will_send_yet(
    api_client, api_db, sending_clock, stub_queue
):
    sending_clock(WED_NIGHT)
    await make_account(api_db, default=True)
    campaign = await make_campaign(api_db, channel="email", n=5)

    response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/start")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "running"
    assert "outside sending hours" in body["message"]
    assert body["waiting"]["next_window"], "and when it will"


@pytest.mark.asyncio
async def test_starting_inside_the_window_has_no_waiting_note(
    api_client, api_db, sending_clock, stub_queue
):
    sending_clock(WED_NOON)
    await make_account(api_db, default=True)
    campaign = await make_campaign(api_db, channel="email", n=5)

    body = (await api_client.post(f"/api/v1/campaigns/{campaign.id}/start")).json()

    assert body["message"] == "Campaign started"
    assert body["waiting"] is None


# --------------------------------------------------------------------------
# wiring: the startup migration and the beat sweep exist
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_applies_the_safe_defaults_step(api_db, monkeypatch):
    import app.main as main
    from contextlib import asynccontextmanager

    for key, value in {"enable_daily_limit": "false", "sending_start_time": "",
                       "sending_end_time": "", "allow_weekends": "true"}.items():
        await _set(api_db, key, value)

    @asynccontextmanager
    async def _factory():
        yield api_db

    monkeypatch.setattr(main, "async_session_factory", _factory)
    await main._apply_safe_sending_defaults()

    assert (await get_sending_rules(api_db))["allow_weekends"] is False


def test_the_breaker_sweep_is_scheduled_and_registered():
    from app.tasks.celery_app import celery_app

    celery_app.loader.import_default_modules()
    entry = celery_app.conf.beat_schedule["check-circuit-breakers"]
    assert entry["task"] in celery_app.tasks
    assert entry["schedule"] <= timedelta(minutes=10), "a missed webhook must not wait long"
