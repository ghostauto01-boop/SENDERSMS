"""No false good/bad results on SMTP policy failures, DNS outages or catch-all."""
import asyncio
import threading
from types import SimpleNamespace

import dns.exception
import dns.resolver
import pytest

from app.services import email_validator as ev
from app.services import mail_dns
from app.services.email_enrichment import Diagnosis, mx_records_exist


@pytest.fixture(autouse=True)
def reset_cache():
    mail_dns._lookup.cache_clear()
    yield
    mail_dns._lookup.cache_clear()


@pytest.mark.parametrize("error", [dns.exception.Timeout, dns.resolver.NoNameservers])
def test_dns_outage_is_unknown_not_dead(monkeypatch, error):
    def resolve(*args, **kwargs):
        raise error()
    monkeypatch.setattr(dns.resolver, "resolve", resolve)
    assert mx_records_exist("working-domain.com") is None


def test_null_mx_is_undeliverable(monkeypatch):
    monkeypatch.setattr(dns.resolver, "resolve", lambda *a, **k: [SimpleNamespace(preference=0, exchange=".")])
    assert mx_records_exist("no-mail.com") is False


def test_no_mx_has_a_fallback(monkeypatch):
    def resolve(domain, kind, **kwargs):
        if kind == "MX":
            raise dns.resolver.NoAnswer()
        return ["93.184.216.34"]
    monkeypatch.setattr(dns.resolver, "resolve", resolve)
    assert mx_records_exist("implicit-mx.com") is True
    assert mail_dns.mail_route("implicit-mx.com").hosts == ((0, "implicit-mx.com"),)


def test_no_mx_has_aaaa_fallback(monkeypatch):
    def resolve(domain, kind, **kwargs):
        if kind != "AAAA":
            raise dns.resolver.NoAnswer()
        return ["2001:4860:4860::8888"]
    monkeypatch.setattr(dns.resolver, "resolve", resolve)
    assert mx_records_exist("ipv6-only.com") is True


def test_transient_failure_is_not_cached(monkeypatch):
    answers = [None, [SimpleNamespace(preference=10, exchange="mx.mail.com.")]]
    def resolve(*args, **kwargs):
        answer = answers.pop(0)
        if answer is None:
            raise dns.exception.Timeout()
        return answer
    monkeypatch.setattr(dns.resolver, "resolve", resolve)
    assert mx_records_exist("recovering.com") is None
    assert mx_records_exist("recovering.com") is True


@pytest.fixture
def smtp(monkeypatch):
    import smtplib
    monkeypatch.setattr(ev, "_mx_hosts", lambda d: [(0, "mx.domain.com")])
    monkeypatch.setattr(ev, "_public_mail_ips", lambda d: ["93.184.216.34"])
    responses = []
    sender_code = [250]
    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def ehlo_or_helo_if_needed(self):
            pass
        def mail(self, sender):
            return sender_code[0], b"sender status"
        def rcpt(self, address):
            return responses.pop(0)
        def data(self, message):
            pytest.fail("Validator must never send a message")
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return responses, sender_code


@pytest.mark.parametrize("responses,expected,catch_all", [
    ([(550, b"5.1.1 User unknown")], "undeliverable", None),
    ([(250, b"ok"), (550, b"5.1.1 No such user")], "deliverable", False),
    ([(250, b"ok"), (250, b"ok")], "risky", True),
    ([(250, b"ok"), (451, b"Try later")], "risky", None),
    ([(550, b"5.7.1 IP address blocked")], "unknown", None),
    ([(552, b"Mailbox full")], "unknown", None),
    ([(554, b"Transaction refused")], "unknown", None),
    ([(421, b"Greylisted")], "unknown", None),
])
def test_smtp_verdicts_are_conservative(smtp, responses, expected, catch_all):
    smtp[0].extend(responses)
    result = ev._smtp_probe("ada@domain.com", "domain.com")
    assert result.verdict == expected
    assert result.is_catch_all is catch_all
    assert result.is_valid_syntax is True


def test_refused_sender_is_not_bad_mailbox(smtp):
    smtp[1][0] = 550
    result = ev._smtp_probe("ada@domain.com", "domain.com")
    assert result.verdict == "unknown"
    assert "sender/policy" in " ".join(result.problems)


def test_private_mx_never_probed(monkeypatch):
    monkeypatch.setattr(dns.resolver, "resolve", lambda *a, **k: ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"])
    assert ev._public_mail_ips("attacker.com") == []


@pytest.mark.asyncio
async def test_smtp_setting_is_honoured_and_dns_off_event_loop(monkeypatch):
    event_thread = threading.get_ident()
    def diagnosis(address, check_dns=True):
        assert threading.get_ident() != event_thread
        return Diagnosis(address=address, valid_syntax=True, domain="domain.com", mx_found=True)
    monkeypatch.setattr(ev, "diagnose", diagnosis)
    monkeypatch.setattr(ev.settings, "REACHER_API_URL", None)
    monkeypatch.setattr(ev.settings, "EMAIL_VALIDATOR_SMTP", False)
    monkeypatch.setattr(ev, "_smtp_probe", lambda *a: pytest.fail("SMTP disabled"))
    result = await ev.validate_email("ada@domain.com", deep=True)
    assert result.verdict == "unknown"
    assert "disabled" in " ".join(result.problems)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, [], "invalid response", {"smtp": "error"}, {"is_reachable": "safe", "syntax": "malformed"}])
async def test_malformed_reacher_falls_back(monkeypatch, payload):
    import httpx
    class Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw):
            return SimpleNamespace(status_code=200, json=lambda: payload)
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(ev.settings, "REACHER_API_URL", "http://service/v2/check_email")
    assert await ev.check_with_reacher("ada@domain.com") is None


@pytest.mark.asyncio
async def test_bad_stored_email_is_not_counted_as_missing(monkeypatch):
    from app.models.contact import Contact
    class Session:
        async def flush(self): pass
    contact = Contact(id=1, email="broken address", email_verified=True)
    result = await ev.validate_contacts(Session(), [contact], deep=False)
    assert result["undeliverable"] == 1
    assert result["no_email"] == 0
    assert contact.is_email_undeliverable is True
    assert contact.email_verified is False


def test_risky_result_demotes_verified_but_unknown_preserves_it():
    from app.models.contact import Contact
    contact = Contact(email="ada@domain.com", email_verified=True)
    ev.apply_verdict(contact, ev.Verdict(address=contact.email, verdict="unknown"))
    assert contact.email_verified is True
    ev.apply_verdict(contact, ev.Verdict(address=contact.email, verdict="risky"))
    assert contact.email_verified is False
    assert not contact.is_email_undeliverable


@pytest.mark.parametrize("address", ["ada..obi@domain.com", "ada.@domain.com", "ada@-domain.com", "ada@domain..com", "a" * 65 + "@domain.com"])
@pytest.mark.asyncio
async def test_malformed_syntax_needs_no_network(address, monkeypatch):
    monkeypatch.setattr(ev, "diagnose", lambda *a, **k: pytest.fail("invalid syntax must not reach DNS"))
    assert (await ev.validate_email(address)).verdict == "undeliverable"
