"""Regression tests for optional sending-domain guidance and email sender compatibility.

Sender-domain authentication is no longer an application-side campaign launch gate: the app preserves
its existing email send route and lets the configured provider decide whether it accepts a message.
These tests pin that policy while retaining coverage for optional SendingDomain DNS planning, DMARC
merging, and warm-up guidance.

DNS and Brevo are always mocked: nothing here touches the network. A real Brevo response and DKIM=pass
integration test still require live credentials (see docs/SPRINT-3-REPORT.md).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.utils.email_domains import (
    FREE_MAIL_DOMAINS,
    domain_of,
    is_free_mail,
    merge_dmarc,
    parse_dmarc,
)
from tests.campaign_factory import make_account, make_campaign


# --------------------------------------------------------------------------
# free-mail senders
# --------------------------------------------------------------------------


@pytest.mark.parametrize("address", [
    "me@gmail.com", "ME@GMAIL.COM", "x@googlemail.com", "a@yahoo.com", "a@ymail.com", "a@outlook.com",
    "a@hotmail.com", "a@live.com", "a@icloud.com", "a@aol.com", "a@proton.me", "a@protonmail.com",
    "a@gmx.com", "a@yandex.com", "  spaced@gmail.com  ",
])
def test_free_mail_addresses_are_recognised(address):
    assert is_free_mail(address) is True


@pytest.mark.parametrize("address", ["sales@acme-leads.io", "a@gmail.com.acme.ng", "a@notgmail.com", "", None, "nope"])
def test_company_addresses_are_not_free_mail(address):
    assert is_free_mail(address) is False


def test_domain_of_handles_odd_input():
    assert domain_of("Ada@Acme.IO") == "acme.io"
    assert domain_of("no-at-sign") is None and domain_of(None) is None
    assert "gmail.com" in FREE_MAIL_DOMAINS


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["boss@gmail.com", "boss@Yahoo.com", "boss@outlook.com"])
async def test_free_mail_senders_can_be_saved_for_the_existing_brevo_route(api_client, address):
    response = await api_client.post("/api/v1/email/accounts", json={
        "name": "Mine", "from_email": address, "from_name": "Boss", "api_key": "xkeysib-test"})

    assert response.status_code == 201, response.text
    assert response.json()["from_email"].lower() == address.strip().lower()


@pytest.mark.asyncio
async def test_a_company_sender_is_still_accepted(api_client):
    response = await api_client.post("/api/v1/email/accounts", json={
        "name": "Mine", "from_email": "hello@acme-leads.io", "from_name": "Acme", "api_key": "xkeysib-test"})

    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_changing_a_sender_to_free_mail_is_allowed(api_client, api_db):
    account = await make_account(api_db, default=True)

    response = await api_client.patch(f"/api/v1/email/accounts/{account.id}", json={"from_email": "me@gmail.com"})

    assert response.status_code == 200, response.text
    assert response.json()["from_email"] == "me@gmail.com"


@pytest.mark.asyncio
async def test_a_campaign_from_a_free_mail_sender_validates_and_starts(api_client, api_db, stub_queue):
    """A legacy Gmail From uses the configured campaign route without an app-side domain gate."""
    from app.models.email import EmailAccount

    account = await make_account(api_db, default=True)
    stored = (await api_db.execute(select(EmailAccount))).scalar_one()
    stored.from_email = "legacy@gmail.com"
    campaign = await make_campaign(api_db, status="draft", channel="email", email_account_id=account.id)

    report = (await api_client.post(f"/api/v1/campaigns/{campaign.id}/validate")).json()
    started = await api_client.post(f"/api/v1/campaigns/{campaign.id}/start")

    assert report["valid"] is True, report
    assert not any("free-mail" in item.lower() or "dmarc" in item.lower()
                   for item in report["errors"] + report["warnings"])
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "running"
    # A successful start dispatches the existing campaign worker; no auth gate
    # or alternate provider route intercepted the legacy sender.
    assert stub_queue and stub_queue[0][1] == (campaign.id,)


@pytest.mark.asyncio
async def test_ads_email_campaign_launch_keeps_the_existing_sender_route(
    api_client, api_db, monkeypatch
):
    from app.models.ads import AdsCampaign
    from app.models.contact import Contact
    from app.models.contact_list import ContactList, ContactListMember
    from tests.test_ads_manager import make_creatives, make_set

    async def brevo_ok(_api_key):
        return {"success": True, "status_code": 200}

    monkeypatch.setattr("app.providers.brevo.test_api_key", brevo_ok)
    account = await make_account(api_db, default=True)
    account.from_email = "legacy@gmail.com"
    campaign = AdsCampaign(
        name="Email outreach", objective="replies", channel="email", status="draft",
        test_mode=False, email_account_id=account.id, subject="Hello",
    )
    contact = Contact(
        phone_number="+2348031234567", email="lead@brandmail.io", first_name="Lead"
    )
    listing = ContactList(name="Email outreach audience")
    api_db.add_all([campaign, contact, listing])
    await api_db.flush()
    api_db.add(ContactListMember(list_id=listing.id, contact_id=contact.id))
    ads_set = await make_set(api_db, campaign, listing)
    await make_creatives(api_db, ads_set, ["Welcome"])
    await api_db.flush()

    report = (await api_client.post(f"/api/v1/ads/campaigns/{campaign.id}/validate")).json()
    launched = await api_client.post(f"/api/v1/ads/campaigns/{campaign.id}/launch")

    assert report["ok"] is True, report
    assert not any("sender_not_ready" in item.lower() or "dmarc" in item.lower()
                   for item in report["errors"] + report["warnings"])
    assert launched.status_code == 200, launched.text
    assert launched.json()["status"] == "active"


# --------------------------------------------------------------------------
# exactly one DMARC record
# --------------------------------------------------------------------------


def test_dmarc_tags_parse():
    assert parse_dmarc("v=DMARC1; p=quarantine; rua=mailto:a@x.io; pct=50") == {
        "v": "DMARC1", "p": "quarantine", "rua": "mailto:a@x.io", "pct": "50"}


def test_with_no_existing_record_the_new_one_starts_in_monitoring_mode():
    out = merge_dmarc(None, rua="dmarc@acme.io")

    assert out["record"] == "v=DMARC1; p=none; rua=mailto:dmarc@acme.io"
    assert out["changed"] is True and out["action"] == "create"


def test_the_requested_rua_is_merged_into_the_existing_record_not_added_beside_it():
    existing = "v=DMARC1; p=quarantine; pct=50; rua=mailto:old@acme.io; adkim=s"

    out = merge_dmarc(existing, rua="new@acme.io")

    tags = parse_dmarc(out["record"])
    assert tags["rua"] == "mailto:old@acme.io,mailto:new@acme.io"
    assert (tags["p"], tags["pct"], tags["adkim"]) == ("quarantine", "50", "s"), "every other tag is kept"
    assert out["action"] == "update" and out["changed"] is True
    assert out["record"].count("v=DMARC1") == 1


def test_a_rua_that_is_already_there_changes_nothing():
    existing = "v=DMARC1; p=reject; rua=mailto:dmarc@acme.io"

    out = merge_dmarc(existing, rua="DMARC@acme.io")

    assert out["changed"] is False and out["action"] == "keep"
    assert parse_dmarc(out["record"]) == parse_dmarc(existing)


def test_a_policy_is_never_weakened():
    out = merge_dmarc("v=DMARC1; p=reject", rua="a@acme.io")

    assert parse_dmarc(out["record"])["p"] == "reject"


def test_two_existing_records_become_one_and_it_says_so():
    """Two DMARC records at one name make DMARC fail validation (receivers treat the domain as having none)."""
    out = merge_dmarc(["v=DMARC1; p=none; rua=mailto:a@acme.io", "v=DMARC1; p=quarantine; rua=mailto:b@acme.io"],
                      rua="c@acme.io")

    tags = parse_dmarc(out["record"])
    assert out["record"].count("v=DMARC1") == 1
    assert tags["rua"].split(",") == ["mailto:a@acme.io", "mailto:b@acme.io", "mailto:c@acme.io"]
    assert tags["p"] == "quarantine", "the stricter policy of the two"
    assert out["had_multiple"] is True and out["action"] == "merge"


def test_a_txt_record_that_is_not_dmarc_is_ignored_and_a_bare_address_gets_mailto():
    out = merge_dmarc(["google-site-verification=abc", "v=spf1 -all"], rua="dmarc@acme.io")

    assert out["action"] == "create"
    assert parse_dmarc(out["record"])["rua"] == "mailto:dmarc@acme.io"


def test_no_rua_at_all_still_gives_a_valid_single_record():
    out = merge_dmarc(None, rua=None)

    assert out["record"] == "v=DMARC1; p=none"


# --------------------------------------------------------------------------
# the SendingDomain object
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def world(api_db, monkeypatch):
    """A Brevo stub and a DNS stub; returns the DNS table the test can edit."""
    from app.providers import brevo
    from app.services import sending_domains

    dns: dict[str, list[str]] = {}

    async def fake_txt(host):
        return list(dns.get(host.lower(), []))

    monkeypatch.setattr(sending_domains, "txt_records", fake_txt)
    state = {"authenticated": False, "created": []}

    async def create_domain(api_key, domain):
        state["created"].append(domain)
        return {"success": True, "domain": domain, "authenticated": False, "records": [
            {"type": "TXT", "host": domain, "value": "brevo-code:abc123", "purpose": "brevo_code", "verified": False},
            {"type": "TXT", "host": f"mail._domainkey.{domain}", "value": "k=rsa;p=MIIB-fake", "purpose": "dkim", "verified": False},
        ]}

    async def authenticate_domain(api_key, domain):
        return {"success": True, "authenticated": state["authenticated"], "records": [
            {"type": "TXT", "host": domain, "value": "brevo-code:abc123", "purpose": "brevo_code",
             "verified": state["authenticated"]},
            {"type": "TXT", "host": f"mail._domainkey.{domain}", "value": "k=rsa;p=MIIB-fake", "purpose": "dkim",
             "verified": state["authenticated"]},
        ]}

    monkeypatch.setattr(brevo, "create_domain", create_domain)
    monkeypatch.setattr(brevo, "authenticate_domain", authenticate_domain)
    await make_account(api_db, default=True)
    return {"dns": dns, "brevo": state}


async def _register(client, domain="acme-leads.io", **extra):
    return await client.post("/api/v1/email/domains", json={"domain": domain, **extra})


def _by_purpose(body):
    out = {}
    for record in body["records"]:
        out.setdefault(record["purpose"], []).append(record)
    return out


@pytest.mark.asyncio
async def test_registering_a_domain_plans_the_dns_records(api_client, world):
    response = await _register(api_client, rua="dmarc@acme-leads.io")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["domain"] == "acme-leads.io" and body["status"] == "pending"
    records = _by_purpose(body)
    assert records["brevo_code"][0]["value"] == "brevo-code:abc123"
    assert records["dkim"][0]["host"] == "mail._domainkey.acme-leads.io"
    assert len(records["dmarc"]) == 1, "exactly one DMARC record"
    assert records["dmarc"][0]["host"] == "_dmarc.acme-leads.io"
    assert records["dmarc"][0]["value"] == "v=DMARC1; p=none; rua=mailto:dmarc@acme-leads.io"
    assert world["brevo"]["created"] == ["acme-leads.io"]


@pytest.mark.asyncio
async def test_an_existing_dmarc_record_is_merged_never_duplicated(api_client, world):
    world["dns"]["_dmarc.acme-leads.io"] = ["v=DMARC1; p=quarantine; rua=mailto:boss@acme-leads.io; pct=100"]

    body = (await _register(api_client, rua="reports@acme-leads.io")).json()

    dmarc = _by_purpose(body)["dmarc"]
    assert len(dmarc) == 1
    assert dmarc[0]["action"] == "update"
    tags = parse_dmarc(dmarc[0]["value"])
    assert tags["rua"] == "mailto:boss@acme-leads.io,mailto:reports@acme-leads.io"
    assert tags["p"] == "quarantine" and tags["pct"] == "100"
    assert "replace the existing" in dmarc[0]["note"].lower(), "it says REPLACE, not add"


@pytest.mark.asyncio
async def test_a_duplicate_domain_is_a_409(api_client, world):
    await _register(api_client)

    response = await _register(api_client, "ACME-LEADS.io")

    assert response.status_code == 409 and response.json()["code"] == "DOMAIN_EXISTS"


@pytest.mark.asyncio
async def test_a_free_mail_domain_cannot_be_registered(api_client, world):
    response = await _register(api_client, "gmail.com")

    assert response.status_code == 422 and "free-mail" in response.json()["message"]
    assert world["brevo"]["created"] == [], "Brevo was never asked"


@pytest.mark.asyncio
async def test_a_malformed_domain_is_a_422(api_client, world):
    for bad in ("", "not a domain", "http://acme.io", "a@b.io"):
        assert (await _register(api_client, bad)).status_code == 422, bad


@pytest.mark.asyncio
async def test_verifying_authenticates_the_domain_and_starts_the_warm_up(api_client, api_db, world):
    created = (await _register(api_client)).json()
    pending = (await api_client.post(f"/api/v1/email/domains/{created['id']}/verify")).json()
    assert pending["status"] == "pending" and pending["warmup"] is None

    world["brevo"]["authenticated"] = True
    done = (await api_client.post(f"/api/v1/email/domains/{created['id']}/verify")).json()

    assert done["status"] == "authenticated"
    assert done["warmup"]["day"] == 1 and done["warmup"]["cap"] == 5
    again = (await api_client.post(f"/api/v1/email/domains/{created['id']}/verify")).json()
    assert again["warmup"]["started_at"] == done["warmup"]["started_at"], "the clock starts once"


@pytest.mark.asyncio
async def test_a_dmarc_record_published_twice_is_reported_by_verify(api_client, world):
    created = (await _register(api_client)).json()
    world["dns"]["_dmarc.acme-leads.io"] = ["v=DMARC1; p=none", "v=DMARC1; p=reject; rua=mailto:x@acme-leads.io"]

    checked = (await api_client.post(f"/api/v1/email/domains/{created['id']}/verify")).json()

    assert checked["dmarc"]["had_multiple"] is True
    assert any("two DMARC records" in w for w in checked["warnings"]), checked["warnings"]


@pytest.mark.asyncio
async def test_domains_can_be_listed_read_and_removed(api_client, world):
    created = (await _register(api_client)).json()

    listing = (await api_client.get("/api/v1/email/domains")).json()
    one = (await api_client.get(f"/api/v1/email/domains/{created['id']}")).json()
    gone = await api_client.delete(f"/api/v1/email/domains/{created['id']}")

    assert [d["domain"] for d in listing["items"]] == ["acme-leads.io"] and one["id"] == created["id"]
    assert gone.status_code == 204
    assert (await api_client.get(f"/api/v1/email/domains/{created['id']}")).status_code == 404


# --------------------------------------------------------------------------
# the warm-up ramp
# --------------------------------------------------------------------------


@pytest.mark.parametrize("day, cap", [(0, 5), (6, 5), (7, 10), (13, 10), (14, 18), (20, 18), (21, 25), (200, 25)])
def test_the_ramp_goes_from_5_a_day_to_25_in_week_four(day, cap):
    from app.services.warmup import warmup_cap

    started = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    assert warmup_cap(started, now=started + timedelta(days=day, hours=3)) == cap


def test_a_clock_that_has_not_started_means_no_ramp():
    from app.services.warmup import warmup_cap

    assert warmup_cap(None) is None


async def _domain(db, address, *, status="authenticated", days_ago=None):
    from app.models.sending_domain import SendingDomain

    row = SendingDomain(
        domain=domain_of(address), status=status, records_json="[]",
        warmup_started_at=(datetime.now(timezone.utc) - timedelta(days=days_ago)) if days_ago is not None else None)
    db.add(row)
    await db.flush()
    return row


@pytest.mark.asyncio
async def test_warmup_and_legacy_sender_limits_do_not_restrict_sending(api_db):
    """Warm-up remains advisory; every actual pacing rule is chosen on a campaign."""
    from app.services.email_service import effective_daily_limit
    from app.services.sending_limits import SendingGate

    account = await make_account(api_db, default=True, daily_limit=30)
    await _domain(api_db, account.from_email, days_ago=3)

    assert effective_daily_limit(account, default_limit=5) is None
    check = await SendingGate(api_db, channel="email", account_id=account.id).check()
    assert check["allowed"] is True
    assert check["reason"] is None


@pytest.mark.asyncio
async def test_campaign_projection_uses_only_campaign_controls_not_mailbox_warmup(api_client, api_db):
    account = await make_account(api_db, default=True)
    await _domain(api_db, account.from_email, days_ago=1)
    campaign = await make_campaign(api_db, status="draft", channel="email", n=20, email_account_id=account.id)

    report = (await api_client.post(f"/api/v1/campaigns/{campaign.id}/validate")).json()

    assert not any("warm-up" in w.lower() for w in report["warnings"]), report["warnings"]
    assert report["projection"]["campaign_only"] is True
    assert report["projection"]["daily_limit"] is None


# --------------------------------------------------------------------------
# sender-domain authentication is optional guidance, never a launch gate
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_registered_but_unauthenticated_domain_does_not_block_start(api_client, api_db, stub_queue):
    account = await make_account(api_db, default=True)
    await _domain(api_db, account.from_email, status="pending")
    campaign = await make_campaign(api_db, status="draft", channel="email", email_account_id=account.id)

    report = (await api_client.post(f"/api/v1/campaigns/{campaign.id}/validate")).json()
    started = await api_client.post(f"/api/v1/campaigns/{campaign.id}/start")

    assert report["valid"] is True, report
    assert not any("not authenticated" in item.lower() or "dmarc" in item.lower()
                   for item in report["errors"] + report["warnings"])
    assert started.status_code == 200, started.text
    await api_db.refresh(campaign)
    assert campaign.status == "running"


@pytest.mark.asyncio
async def test_an_authenticated_domain_lets_the_campaign_start(api_client, api_db, stub_queue):
    account = await make_account(api_db, default=True)
    await _domain(api_db, account.from_email, status="authenticated", days_ago=40)
    campaign = await make_campaign(api_db, status="draft", channel="email", email_account_id=account.id)

    started = await api_client.post(f"/api/v1/campaigns/{campaign.id}/start")

    assert started.status_code == 200, started.text


@pytest.mark.asyncio
async def test_an_unregistered_sender_domain_does_not_affect_campaign_validation(api_client, api_db):
    account = await make_account(api_db, default=True)
    campaign = await make_campaign(api_db, status="draft", channel="email", email_account_id=account.id)

    report = (await api_client.post(f"/api/v1/campaigns/{campaign.id}/validate")).json()

    assert report["valid"] is True, report
    assert not any("sending domain" in item.lower() or "dmarc" in item.lower()
                   for item in report["errors"] + report["warnings"])


@pytest.mark.asyncio
async def test_the_assistant_can_register_and_check_a_sending_domain(api_client, world):
    from tests.mcp_helpers import call_tool

    registered = await call_tool("register_sending_domain", domain="acme-leads.io", rua="dmarc@acme-leads.io")
    listed = await call_tool("list_sending_domains")

    assert registered["ok"] is True, registered
    assert len(_by_purpose(registered["data"])["dmarc"]) == 1
    assert [d["domain"] for d in listed["data"]["items"]] == ["acme-leads.io"]
    checked = await call_tool("verify_sending_domain", domain_id=registered["data"]["id"])
    assert checked["data"]["status"] in ("pending", "authenticated")
