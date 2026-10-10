"""P0-5 — an address nobody has verified is not sendable.

The QA sweep found 1,297 contacts, none verified, and every one of them sendable:
``unknown`` (could not be proven either way) was treated like ``deliverable`` by every
send path, and the validator wrote nothing down unless a caller remembered to pass
``save=true``. The cost lands on the sender: ~70% of the addresses were generated role
mailboxes, 184 had already bounced, and a new mailbox does not survive that.

What these tests pin:

* ``unknown`` (never verified, or the verifier could not decide) is NOT sendable by any
  outreach path: classic campaigns, Ads Manager, follow-ups, bulk ``queue_email``.
  ``deliverable`` is. ``risky`` (a catch-all, a role or disposable address) is sendable but
  goes LAST. One-to-one sends and a reply address are not outreach and are not gated.
* The send path and the audience screening apply the SAME rule (they used to differ).
* ``validate`` fails an email campaign that contains unknown contacts and says how many.
* The validator persists what it learned (verdict, confidence, verified flags), reports a
  normalised ``status`` (valid | invalid | risky | unknown), says honestly whether it can
  confirm mailboxes at all (``smtp_enabled`` + a reason), takes batches larger than 5, and
  can run a whole list as a queued, resumable background job.

DNS and SMTP are always mocked: nothing here touches the network.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config import settings
from app.models.campaign import Campaign, CampaignContact
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.conversation import Message
from app.services import email_service
from app.services import email_validator as ev
from tests.campaign_factory import make_account, make_campaign

pytestmark = pytest.mark.real_verification_gate


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

_n = iter(range(1, 10_000))


async def _contact(db, *, verified=False, verdict=None, source="csv", confidence=None,
                   email=None, **kw):
    i = next(_n)
    contact = Contact(
        first_name=f"P{i}",
        email=email or f"person{i}@leadco{i}.io",
        email_source=source,
        email_verified=verified,
        email_verified_at=datetime.now(timezone.utc) if verified else None,
        email_verdict=verdict,
        email_confidence=confidence,
        **kw,
    )
    db.add(contact)
    await db.flush()
    return contact


async def _list_of(db, contacts, name="Audience"):
    lst = ContactList(name=f"{name} {next(_n)}")
    db.add(lst)
    await db.flush()
    for contact in contacts:
        db.add(ContactListMember(list_id=lst.id, contact_id=contact.id))
    await db.flush()
    return lst


async def _email_campaign(db, contacts, **kw):
    account = await make_account(db, default=True)
    lst = await _list_of(db, contacts)
    campaign = Campaign(
        name="Outreach", status="draft", channel="email", list_id=lst.id,
        message_body="Hello {{first_name}}", subject="Hello", email_account_id=account.id,
        **kw,
    )
    db.add(campaign)
    await db.flush()
    return campaign


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_address_nobody_verified_is_not_sendable_in_outreach(api_db):
    contact = await _contact(api_db)  # imported from a CSV, never checked

    assert await email_service.contact_email_problem(api_db, contact, outreach=True) == "unverified"


@pytest.mark.asyncio
async def test_an_unknown_verdict_is_not_sendable(api_db):
    """The verifier ran and could not decide (blocked SMTP, timeout): that is not a yes."""
    contact = await _contact(api_db, verdict="unknown", confidence=0)

    assert await email_service.contact_email_problem(api_db, contact, outreach=True) == "unverified"


@pytest.mark.asyncio
async def test_a_deliverable_address_is_sendable(api_db):
    contact = await _contact(api_db, verified=True, verdict="deliverable", confidence=95)

    assert await email_service.contact_email_problem(api_db, contact, outreach=True) is None


@pytest.mark.asyncio
async def test_a_risky_address_is_sendable_but_ranked_after_the_deliverable_ones(api_db):
    risky = await _contact(api_db, verdict="risky", confidence=60)
    good = await _contact(api_db, verified=True, verdict="deliverable", confidence=95)

    assert await email_service.contact_email_problem(api_db, risky, outreach=True) is None
    assert email_service.verification_state(risky) == "risky"
    assert email_service.verification_state(good) == "deliverable"
    ordered = email_service.send_order([risky, good])
    assert [c.id for c in ordered] == [good.id, risky.id], "risky mail goes last"


@pytest.mark.asyncio
async def test_a_confirmed_bad_address_is_blocked_whatever_else_is_true(api_db):
    contact = await _contact(api_db, verdict="undeliverable", confidence=0,
                             is_email_undeliverable=True, email_status="invalid")

    assert await email_service.contact_email_problem(api_db, contact, outreach=True) == "email_bounced"
    assert email_service.verification_state(contact) == "undeliverable"


@pytest.mark.asyncio
async def test_one_to_one_mail_and_reply_addresses_are_not_outreach(api_db):
    """You can answer someone who wrote to you, and send one deliberate message, to an
    address the verifier never saw. The gate is for outreach."""
    contact = await _contact(api_db)

    assert await email_service.contact_email_problem(api_db, contact) is None
    alias = "reply-address@theirdomain.io"
    assert await email_service.contact_email_problem(
        api_db, contact, alias, outreach=True) is None, "a reply alias is not the unverified primary"


@pytest.mark.asyncio
async def test_an_unverified_address_that_is_also_suppressed_is_reported_as_suppressed(api_db):
    """`unverified` must count only what verifying could fix."""
    contact = await _contact(api_db)
    await email_service.suppress_email(api_db, contact.email, reason="asked to stop")

    assert await email_service.contact_email_problem(api_db, contact, outreach=True) == "suppressed"


@pytest.mark.asyncio
async def test_the_gate_has_an_explicit_off_switch(api_db, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_REQUIRE_VERIFIED", False)
    contact = await _contact(api_db)

    assert await email_service.contact_email_problem(api_db, contact, outreach=True) is None


@pytest.mark.asyncio
async def test_audience_screening_and_the_send_gate_apply_the_same_rule(api_db):
    """They used to differ: screening (validate/preview) had no `inferred` check at all, so a
    report could promise numbers the send path would then refuse."""
    contacts = {
        "verified": await _contact(api_db, verified=True, verdict="deliverable"),
        "unknown": await _contact(api_db),
        "risky": await _contact(api_db, verdict="risky"),
        "inferred": await _contact(api_db, source="inferred", verified=True, verdict="deliverable"),
        "opted_out": await _contact(api_db, verified=True, is_email_opted_out=True),
        "bounced": await _contact(api_db, verified=True, email_status="bounced"),
        "placeholder": await _contact(api_db, verified=True, email="someone@example.com"),
    }
    eligible, skipped = await email_service.screen_contacts_for_email(
        api_db, list(contacts.values()), outreach=True)

    gate = {}
    for name, contact in contacts.items():
        gate[name] = await email_service.contact_email_problem(api_db, contact, outreach=True)
    assert {c.id for c in eligible} == {c.id for n, c in contacts.items() if gate[n] is None}
    assert {c.id for c in eligible} == {contacts["verified"].id, contacts["risky"].id}
    assert skipped["unverified"] == 1
    assert skipped["inferred_unverified"] == 1


# --------------------------------------------------------------------------
# a campaign that contains unknown contacts fails validation, with a count
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_fails_a_campaign_that_contains_unknown_contacts_and_counts_them(
        api_client, api_db):
    good = [await _contact(api_db, verified=True, verdict="deliverable") for _ in range(3)]
    unknown = [await _contact(api_db) for _ in range(2)]
    campaign = await _email_campaign(api_db, good + unknown)

    response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/validate")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False and body["ok"] is False
    assert body["audience"]["unverified"] == 2
    assert body["audience"]["sendable"] == 3
    assert any("2 " in e and "unknown" in e.lower() for e in body["errors"]), body["errors"]
    assert body["changed"] is False and body["status"] == "draft", "validate stays report-only"


@pytest.mark.asyncio
async def test_validate_passes_when_everyone_is_verified(api_client, api_db):
    good = [await _contact(api_db, verified=True, verdict="deliverable") for _ in range(3)]
    campaign = await _email_campaign(api_db, good)

    body = (await api_client.post(f"/api/v1/campaigns/{campaign.id}/validate")).json()

    assert body["valid"] is True, body["errors"]
    assert body["audience"]["unverified"] == 0


@pytest.mark.asyncio
async def test_risky_contacts_do_not_fail_validation_but_are_counted(api_client, api_db):
    contacts = [await _contact(api_db, verified=True, verdict="deliverable"),
                await _contact(api_db, verdict="risky", confidence=60)]
    campaign = await _email_campaign(api_db, contacts)

    body = (await api_client.post(f"/api/v1/campaigns/{campaign.id}/validate")).json()

    assert body["valid"] is True, body["errors"]
    assert body["audience"]["risky"] == 1
    assert any("last" in w.lower() and "1" in w for w in body["warnings"]), body["warnings"]


@pytest.mark.asyncio
async def test_start_refuses_a_campaign_with_unknown_contacts_and_sends_nothing(
        api_client, api_db, stub_queue):
    contacts = [await _contact(api_db, verified=True, verdict="deliverable"),
                await _contact(api_db)]
    campaign = await _email_campaign(api_db, contacts)

    response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/start")

    assert response.status_code == 400
    assert "unknown" in response.text.lower()
    rows = (await api_db.execute(select(CampaignContact))).scalars().all()
    assert rows == [], "nothing was queued for anyone"
    assert (await api_db.execute(select(Message))).scalars().all() == []
    await api_db.refresh(campaign)
    assert campaign.status == "draft"


@pytest.mark.asyncio
async def test_verifying_the_contacts_lets_the_campaign_start(api_client, api_db, stub_queue):
    contacts = [await _contact(api_db, verified=True, verdict="deliverable"),
                await _contact(api_db)]
    campaign = await _email_campaign(api_db, contacts)
    assert (await api_client.post(f"/api/v1/campaigns/{campaign.id}/start")).status_code == 400

    ev.apply_verdict(contacts[1], ev.Verdict(
        address=contacts[1].email, verdict="deliverable", is_reachable="safe"))
    await api_db.flush()
    response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/start")

    assert response.status_code == 200, response.text
    await api_db.refresh(campaign)
    assert campaign.total_contacts == 2


@pytest.mark.asyncio
async def test_risky_contacts_are_queued_after_the_deliverable_ones(
        api_client, api_db, stub_queue):
    """Created first (lower ids) but still sent last: the daily cap is then spent on the
    addresses most likely to land."""
    risky = [await _contact(api_db, verdict="risky", confidence=60) for _ in range(2)]
    good = [await _contact(api_db, verified=True, verdict="deliverable") for _ in range(2)]
    campaign = await _email_campaign(api_db, risky + good)

    response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/start")

    assert response.status_code == 200, response.text
    queued = (await api_db.execute(
        select(CampaignContact.contact_id).where(CampaignContact.campaign_id == campaign.id)
        .order_by(CampaignContact.id)
    )).scalars().all()
    assert queued == [c.id for c in good] + [c.id for c in risky]


# --------------------------------------------------------------------------
# defence in depth: the send path refuses too
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def batch(api_db, monkeypatch):
    import app.tasks.campaign_tasks as ct
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():
        yield api_db

    monkeypatch.setattr(ct, "async_session_factory", _factory)
    monkeypatch.setattr(ct, "enqueue", lambda task, *a, **k: None)

    async def _run(campaign_id):
        return await ct.process_campaign_batch_async(campaign_id, batch_size=50)

    return _run


@pytest.mark.asyncio
async def test_the_send_path_refuses_an_unverified_contact_even_if_a_row_exists(api_db, batch):
    """A contact edited after the audience was built (an edit resets verification) must not
    be mailed just because a pending row for them already exists."""
    unknown = await _contact(api_db)
    good = await _contact(api_db, verified=True, verdict="deliverable")
    campaign = await _email_campaign(api_db, [unknown, good])
    campaign.status = "running"
    for contact in (unknown, good):
        api_db.add(CampaignContact(campaign_id=campaign.id, contact_id=contact.id, status="pending"))
    await api_db.flush()

    await batch(campaign.id)

    messages = (await api_db.execute(select(Message).where(Message.campaign_id == campaign.id))).scalars().all()
    assert [m.contact_id for m in messages] == [good.id], "only the verified contact was mailed"
    row = (await api_db.execute(select(CampaignContact).where(
        CampaignContact.contact_id == unknown.id))).scalar_one()
    assert row.status != "pending" and "unverified" in (row.last_error or "")


@pytest.mark.asyncio
async def test_bulk_queue_email_refuses_an_unverified_contact(api_db):
    contact = await _contact(api_db)
    account = await make_account(api_db, default=True)

    bulk = await email_service.queue_email(
        api_db, contact, subject="Hi", text_body="Hello", account=account, bulk=True)
    one_to_one = await email_service.queue_email(
        api_db, contact, subject="Hi", text_body="Hello", account=account)

    assert bulk is None, "bulk mail to an unverified address is refused"
    assert one_to_one is not None, "a deliberate one-to-one message is not"


# --------------------------------------------------------------------------
# the Ads Manager follows the same rule
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ads_audience_screening_excludes_unknown_contacts_and_counts_them(api_db):
    from app.services import ads_service as svc
    from tests.test_ads_manager import make_campaign as ads_campaign

    account = await make_account(api_db, default=True)
    campaign = await ads_campaign(api_db, name="Ads email", channel="email", status="draft",
                                  test_mode=False, email_account_id=account.id, subject="Hello")
    good = await _contact(api_db, verified=True, verdict="deliverable")
    unknown = await _contact(api_db)
    risky = await _contact(api_db, verdict="risky")

    eligible, skipped = await svc.screen_contacts(api_db, campaign, [good, unknown, risky])

    assert {c.id for c in eligible} == {good.id, risky.id}
    assert skipped == {"unverified": 1}


@pytest.mark.asyncio
async def test_ads_validate_fails_with_the_count_of_unknown_contacts(api_db):
    from app.services import ads_service as svc
    from tests.test_ads_manager import make_campaign as ads_campaign
    from tests.test_ads_manager import make_creatives, make_set

    account = await make_account(api_db, default=True)
    campaign = await ads_campaign(api_db, name="Ads email", channel="email", status="draft",
                                  test_mode=False, email_account_id=account.id, subject="Hello")
    contacts = [await _contact(api_db, verified=True, verdict="deliverable")] + [
        await _contact(api_db) for _ in range(3)]
    lst = await _list_of(api_db, contacts)
    ads_set = await make_set(api_db, campaign, lst)
    await make_creatives(api_db, ads_set, ["A"])

    report = await svc.validate_campaign(api_db, campaign)

    assert report["ok"] is False
    assert any("3 " in e and "unknown" in e.lower() for e in report["errors"]), report["errors"]
    assert report["summary"]["skipped"]["unverified"] == 3
    assert report["summary"]["eligible"] == 1


# --------------------------------------------------------------------------
# the validator: what it reports and what it remembers
# --------------------------------------------------------------------------


@pytest.mark.parametrize("verdict, status", [
    ("deliverable", "valid"), ("undeliverable", "invalid"), ("risky", "risky"), ("unknown", "unknown"),
])
def test_a_verdict_reports_a_normalised_status(verdict, status):
    assert ev.Verdict(address="a@b.io", verdict=verdict).as_dict()["status"] == status


@pytest.mark.asyncio
async def test_a_deliverable_verdict_is_persisted_with_its_confidence(api_db):
    contact = await _contact(api_db)

    ev.apply_verdict(contact, ev.Verdict(address=contact.email, verdict="deliverable",
                                         is_reachable="safe"))

    assert contact.email_verified is True
    assert contact.email_verified_at is not None
    assert contact.email_verdict == "deliverable"
    assert contact.email_confidence == 95


@pytest.mark.asyncio
async def test_risky_and_undeliverable_verdicts_are_persisted(api_db):
    risky = await _contact(api_db, verified=True, verdict="deliverable", confidence=95)
    bad = await _contact(api_db)

    ev.apply_verdict(risky, ev.Verdict(address=risky.email, verdict="risky", is_reachable="risky",
                                       is_catch_all=True))
    ev.apply_verdict(bad, ev.Verdict(address=bad.email, verdict="undeliverable",
                                     is_reachable="invalid"))

    assert risky.email_verified is False and risky.email_verdict == "risky"
    assert risky.email_confidence == 60
    assert bad.email_verdict == "undeliverable" and bad.email_confidence == 0
    assert bad.is_email_undeliverable is True and bad.email_status == "invalid"


@pytest.mark.asyncio
async def test_an_unknown_result_is_recorded_but_never_downgrades_a_proven_address(api_db):
    never_checked = await _contact(api_db)
    proven = await _contact(api_db, verified=True, verdict="deliverable", confidence=95)
    blip = ev.Verdict(address="x@y.io", verdict="unknown", problems=["SMTP inconclusive"])

    ev.apply_verdict(never_checked, blip)
    ev.apply_verdict(proven, blip)

    assert never_checked.email_verdict == "unknown"
    assert never_checked.email_verified is False
    assert proven.email_verdict == "deliverable" and proven.email_verified is True, \
        "one timeout must not un-verify an address a mailbox probe proved"


@pytest.mark.asyncio
async def test_a_contact_whose_address_is_edited_loses_its_verdict(api_client, api_db):
    contact = await _contact(api_db, verified=True, verdict="deliverable", confidence=95)

    response = await api_client.put(f"/api/v1/contacts/{contact.id}",
                                    json={"email": "someone.else@newdomain.io"})

    assert response.status_code == 200, response.text
    await api_db.refresh(contact)
    assert contact.email_verified is False
    assert contact.email_verdict is None and contact.email_confidence is None


# --- the pipeline, with DNS and SMTP mocked -----------------------------------


def _dns(monkeypatch, *, mx_found):
    from app.services.email_enrichment import Diagnosis

    def fake(address, *, check_dns=True):
        domain = address.rsplit("@", 1)[1]
        return Diagnosis(address=address, domain=domain, mx_found=mx_found, problems=[])

    monkeypatch.setattr(ev, "diagnose", fake)


@pytest.fixture
def no_reacher(monkeypatch):
    monkeypatch.setattr(settings, "REACHER_API_URL", "", raising=False)


@pytest.mark.asyncio
async def test_a_domain_without_mx_is_invalid(monkeypatch, no_reacher):
    _dns(monkeypatch, mx_found=False)

    verdict = await ev.validate_email("someone@no-mail-here.io")

    assert verdict.verdict == "undeliverable"
    assert verdict.as_dict()["status"] == "invalid"
    assert verdict.accepts_mail is False


@pytest.mark.asyncio
async def test_a_catch_all_domain_is_risky_not_valid(monkeypatch, no_reacher):
    _dns(monkeypatch, mx_found=True)
    monkeypatch.setattr(settings, "EMAIL_VALIDATOR_SMTP", True)
    monkeypatch.setattr(ev, "_smtp_probe", lambda address, domain: ev.Verdict(
        address=address, verdict="risky", is_reachable="risky", is_catch_all=True,
        problems=["Domain accepts any address (catch-all)"]))

    verdict = await ev.validate_email("someone@catchall.io")

    assert verdict.verdict == "risky" and verdict.is_catch_all is True
    assert verdict.as_dict()["status"] == "risky"


@pytest.mark.asyncio
async def test_a_mailbox_the_server_accepts_on_a_normal_domain_is_valid(monkeypatch, no_reacher):
    _dns(monkeypatch, mx_found=True)
    monkeypatch.setattr(settings, "EMAIL_VALIDATOR_SMTP", True)
    monkeypatch.setattr(ev, "_smtp_probe", lambda address, domain: ev.Verdict(
        address=address, verdict="deliverable", is_reachable="safe", is_catch_all=False))

    verdict = await ev.validate_email("ada@realcompany.io")

    assert verdict.verdict == "deliverable" and verdict.as_dict()["status"] == "valid"


@pytest.mark.asyncio
async def test_without_smtp_a_mailbox_stays_unknown(monkeypatch, no_reacher):
    """No probe means no proof. It must come back `unknown` -- and therefore not sendable."""
    _dns(monkeypatch, mx_found=True)
    monkeypatch.setattr(settings, "EMAIL_VALIDATOR_SMTP", False)

    verdict = await ev.validate_email("ada@realcompany.io")

    assert verdict.verdict == "unknown" and verdict.as_dict()["status"] == "unknown"


# --- status: be honest about what can be confirmed ------------------------------


@pytest.mark.asyncio
async def test_status_says_why_smtp_is_off(api_client, monkeypatch, no_reacher):
    monkeypatch.setattr(settings, "EMAIL_VALIDATOR_SMTP", False)

    body = (await api_client.get("/api/v1/validator/status")).json()

    assert body["smtp_enabled"] is False
    assert "EMAIL_VALIDATOR_SMTP" in body["smtp_reason"]
    assert body["can_confirm_mailboxes"] is False


@pytest.mark.asyncio
async def test_status_reports_blocked_egress_instead_of_claiming_smtp_works(
        api_client, monkeypatch, no_reacher):
    monkeypatch.setattr(settings, "EMAIL_VALIDATOR_SMTP", True)

    async def blocked():
        return False, "connection to gmail-smtp-in.l.google.com:25 timed out"

    monkeypatch.setattr(ev, "_probe_port_25", blocked)
    ev._egress_cache.clear()

    body = (await api_client.get("/api/v1/validator/status")).json()

    assert body["smtp_enabled"] is False
    assert "port 25" in body["smtp_reason"].lower() and "timed out" in body["smtp_reason"]
    assert "REACHER_API_URL" in body["smtp_reason"], "it says what to do about it"
    assert body["can_confirm_mailboxes"] is False


@pytest.mark.asyncio
async def test_status_reports_smtp_enabled_when_the_port_is_reachable(
        api_client, monkeypatch, no_reacher):
    monkeypatch.setattr(settings, "EMAIL_VALIDATOR_SMTP", True)

    async def open_port():
        return True, None

    monkeypatch.setattr(ev, "_probe_port_25", open_port)
    ev._egress_cache.clear()

    body = (await api_client.get("/api/v1/validator/status")).json()

    assert body["smtp_enabled"] is True and body["smtp_reason"] is None
    assert body["can_confirm_mailboxes"] is True


class _FakeSocket:
    def __init__(self, banner=None, recv_error=None):
        self._banner, self._recv_error = banner, recv_error

    def settimeout(self, seconds):
        pass

    def recv(self, n):
        if self._recv_error:
            raise self._recv_error
        return self._banner

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_the_port_25_probe_needs_a_greeting_not_just_a_connection(monkeypatch):
    """Found in this very environment: port 25 ACCEPTS the connection (something intercepts
    it) and then closes it with an empty banner. Connect-only would call SMTP "working"."""
    import socket

    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: _FakeSocket(banner=b""))
    ok, why = await ev._probe_port_25()
    assert ok is False and "no SMTP greeting" in why and "intercepting" in why

    monkeypatch.setattr(socket, "create_connection",
                        lambda *a, **k: _FakeSocket(banner=b"421 go away"))
    ok, why = await ev._probe_port_25()
    assert ok is False and "no SMTP greeting" in why

    monkeypatch.setattr(socket, "create_connection",
                        lambda *a, **k: _FakeSocket(recv_error=socket.timeout("timed out")))
    ok, why = await ev._probe_port_25()
    assert ok is False and "no SMTP greeting arrived" in why

    monkeypatch.setattr(socket, "create_connection",
                        lambda *a, **k: _FakeSocket(banner=b"220 mx.google.com ESMTP ready"))
    assert await ev._probe_port_25() == (True, None)


@pytest.mark.asyncio
async def test_the_port_25_probe_reports_a_refused_connection(monkeypatch):
    import socket

    def refuse(*a, **k):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(socket, "create_connection", refuse)
    ok, why = await ev._probe_port_25()
    assert ok is False and "failed" in why and "ConnectionRefusedError" in why


@pytest.mark.asyncio
async def test_status_with_a_reacher_service_does_not_need_local_port_25(
        api_client, monkeypatch):
    monkeypatch.setattr(settings, "REACHER_API_URL", "https://reacher.invalid/v0/check_email")
    monkeypatch.setattr(settings, "EMAIL_VALIDATOR_SMTP", False)

    async def must_not_probe():
        raise AssertionError("Reacher does the SMTP probing; this server's port 25 is irrelevant")

    monkeypatch.setattr(ev, "_probe_port_25", must_not_probe)

    body = (await api_client.get("/api/v1/validator/status")).json()

    assert body["engine"] == "reacher"
    assert body["smtp_enabled"] is True and body["can_confirm_mailboxes"] is True


# --- batches ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_advertises_a_batch_size_above_five(api_client):
    assert (await api_client.get("/api/v1/validator/status")).json()["batch_size"] > 5


@pytest.mark.asyncio
async def test_a_batch_of_twenty_five_is_accepted_and_twenty_six_is_not(
        api_client, monkeypatch):
    async def fake(address, *, deep=True):
        return ev.Verdict(address=address, verdict="deliverable", is_valid_syntax=True)

    monkeypatch.setattr(ev, "validate_email", fake)
    size = (await api_client.get("/api/v1/validator/status")).json()["batch_size"]
    items = lambda n: [{"email": f"p{i}@leadco{i}.io"} for i in range(n)]  # noqa: E731

    ok = await api_client.post("/api/v1/validator/batch", json={"items": items(size)})
    too_many = await api_client.post("/api/v1/validator/batch", json={"items": items(size + 1)})

    assert ok.status_code == 200 and ok.json()["processed"] == size
    assert too_many.status_code == 422


# --- queued verification --------------------------------------------------------


@pytest_asyncio.fixture
async def jobs(api_db, monkeypatch):
    """Verification jobs bound to the test database. Nothing starts on its own."""
    from contextlib import asynccontextmanager

    from app.services import verification_jobs

    @asynccontextmanager
    async def _factory():
        yield api_db

    async def open_port():
        return True, None

    monkeypatch.setattr(verification_jobs, "async_session_factory", _factory)
    monkeypatch.setattr(verification_jobs, "spawn", lambda job_id: None)
    # Deterministic capability: SMTP on, port 25 "open", no Reacher. Nothing touches the net.
    monkeypatch.setattr(settings, "REACHER_API_URL", "", raising=False)
    monkeypatch.setattr(settings, "EMAIL_VALIDATOR_SMTP", True)
    monkeypatch.setattr(ev, "_probe_port_25", open_port)
    ev._egress_cache.clear()
    return verification_jobs


def _stub_validator(monkeypatch, outcome):
    async def fake(address, *, deep=True):
        verdict = outcome(address)
        return ev.Verdict(address=address, verdict=verdict, is_valid_syntax=True,
                          is_reachable={"deliverable": "safe", "undeliverable": "invalid"}.get(
                              verdict, verdict))

    monkeypatch.setattr(ev, "validate_email", fake)


@pytest.mark.asyncio
async def test_a_whole_list_can_be_verified_as_a_queued_job(api_client, api_db, jobs, monkeypatch):
    contacts = [await _contact(api_db, email=f"good{i}@leadco{i}.io") for i in range(7)]
    contacts += [await _contact(api_db, email=f"bad{i}@leadco{i}.io") for i in range(3)]
    contacts += [await _contact(api_db, email=f"risk{i}@leadco{i}.io") for i in range(2)]
    contacts += [await _contact(api_db, email=f"huh{i}@leadco{i}.io") for i in range(2)]
    lst = await _list_of(api_db, contacts)
    _stub_validator(monkeypatch, lambda a: {
        "good": "deliverable", "bad": "undeliverable", "risk": "risky"}.get(a[:4].rstrip("0123456789"), "unknown"))

    created = await api_client.post("/api/v1/validator/jobs", json={"scope": "list", "list_id": lst.id})

    assert created.status_code == 202, created.text
    job = created.json()
    assert job["status"] == "queued" and job["total"] == 14 and job["processed"] == 0
    await jobs.run_job(job["id"])

    done = (await api_client.get(f"/api/v1/validator/jobs/{job['id']}")).json()
    assert done["status"] == "done" and done["processed"] == 14
    assert (done["valid"], done["invalid"], done["risky"], done["unknown"]) == (7, 3, 2, 2)
    # ...and every verdict was written down on the contacts:
    for contact in contacts:
        await api_db.refresh(contact)
    states = [email_service.verification_state(c) for c in contacts]
    assert states.count("deliverable") == 7 and states.count("undeliverable") == 3
    assert states.count("risky") == 2 and states.count("unknown") == 2
    assert all(c.email_verdict for c in contacts)


@pytest.mark.asyncio
async def test_a_job_skips_what_is_already_proven_unless_asked(api_client, api_db, jobs, monkeypatch):
    proven = await _contact(api_db, verified=True, verdict="deliverable", confidence=95)
    fresh = await _contact(api_db)
    _stub_validator(monkeypatch, lambda a: "deliverable")

    job = (await api_client.post("/api/v1/validator/jobs", json={
        "scope": "ids", "contact_ids": [proven.id, fresh.id]})).json()
    forced = (await api_client.post("/api/v1/validator/jobs", json={
        "scope": "ids", "contact_ids": [proven.id, fresh.id], "recheck": True})).json()

    assert job["total"] == 1, "the verified contact is not re-probed"
    assert forced["total"] == 2


@pytest.mark.asyncio
async def test_a_job_reports_why_nothing_could_be_confirmed(api_client, api_db, jobs, monkeypatch, no_reacher):
    """SMTP off: every mailbox comes back unknown. The job must say so, not just say 'done'."""
    monkeypatch.setattr(settings, "EMAIL_VALIDATOR_SMTP", False)
    contact = await _contact(api_db, email="ada@realcompany.io")
    _dns(monkeypatch, mx_found=True)

    job = (await api_client.post("/api/v1/validator/jobs", json={
        "scope": "ids", "contact_ids": [contact.id]})).json()
    await jobs.run_job(job["id"])

    done = (await api_client.get(f"/api/v1/validator/jobs/{job['id']}")).json()
    assert done["status"] == "done" and done["unknown"] == 1
    assert done["smtp_enabled"] is False and "EMAIL_VALIDATOR_SMTP" in done["smtp_reason"]


@pytest.mark.asyncio
async def test_an_interrupted_job_resumes_where_it_stopped(api_client, api_db, jobs, monkeypatch):
    contacts = [await _contact(api_db, email=f"c{i}@leadco{i}.io") for i in range(6)]
    seen: list[str] = []

    async def fake(address, *, deep=True):
        seen.append(address)
        return ev.Verdict(address=address, verdict="deliverable", is_valid_syntax=True)

    monkeypatch.setattr(ev, "validate_email", fake)
    monkeypatch.setattr(jobs, "CHUNK", 2)
    job = (await api_client.post("/api/v1/validator/jobs", json={
        "scope": "ids", "contact_ids": [c.id for c in contacts]})).json()

    # The process dies after the first chunk: the row says "running", processed = 2.
    await jobs.run_job(job["id"], max_chunks=1)
    row = await jobs.get_job(api_db, job["id"])
    assert row.status == "running" and row.processed == 2

    assert await jobs.pending_job_ids() == [job["id"]], "a restart finds it"
    await jobs.run_job(job["id"])

    done = (await api_client.get(f"/api/v1/validator/jobs/{job['id']}")).json()
    assert done["status"] == "done" and done["processed"] == 6
    assert len(seen) == 6 and len(set(seen)) == 6, "no address was probed twice"


@pytest.mark.asyncio
async def test_a_job_can_be_cancelled(api_client, api_db, jobs, monkeypatch):
    contacts = [await _contact(api_db) for _ in range(4)]
    _stub_validator(monkeypatch, lambda a: "deliverable")
    job = (await api_client.post("/api/v1/validator/jobs", json={
        "scope": "ids", "contact_ids": [c.id for c in contacts]})).json()

    cancelled = await api_client.post(f"/api/v1/validator/jobs/{job['id']}/cancel")
    await jobs.run_job(job["id"])

    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    after = (await api_client.get(f"/api/v1/validator/jobs/{job['id']}")).json()
    assert after["status"] == "cancelled" and after["processed"] == 0


@pytest.mark.asyncio
async def test_unknown_jobs_and_bad_selections_are_clear_errors(api_client):
    assert (await api_client.get("/api/v1/validator/jobs/does-not-exist")).status_code == 404
    assert (await api_client.post("/api/v1/validator/jobs", json={"scope": "ids"})).status_code == 400
    assert (await api_client.post("/api/v1/validator/jobs", json={
        "scope": "list", "list_id": 99999})).status_code == 404


@pytest.mark.asyncio
async def test_the_assistant_can_queue_and_follow_a_verification(api_client, api_db, jobs, monkeypatch):
    from app.mcp import server
    from app.mcp.registry import TOOLS_BY_NAME

    contact = await _contact(api_db)
    _stub_validator(monkeypatch, lambda a: "deliverable")

    class Stub:
        def add(self, row): pass
        async def flush(self): pass

    server.CURRENT_ADMIN_ID.set(1)
    token = server.TokenView(id=1, name="t", scope="write", prefix="mcp_t")
    queued = await server.run_tool(Stub(), token, TOOLS_BY_NAME["verify_contacts"],
                                   {"scope": "ids", "contact_ids": [contact.id]})
    envelope = json.loads(queued.result["content"][0]["text"])
    job_id = envelope["data"]["id"]
    await jobs.run_job(job_id)
    followed = await server.run_tool(Stub(), token, TOOLS_BY_NAME["verification_job"],
                                     {"job_id": job_id})

    assert json.loads(followed.result["content"][0]["text"])["data"]["status"] == "done"
    assert "validator_status" in TOOLS_BY_NAME
