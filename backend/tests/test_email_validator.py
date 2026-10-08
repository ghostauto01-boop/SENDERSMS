"""Email validator — Reacher integration and the built-in twin.

Covers the parts the product depends on: invalid addresses are condemned
cheaply and offline, only confirmed-good addresses become verified, a
confirmed-bad address is quarantined for every send path, Reacher's response
maps onto the shared verdict vocabulary, and a batch run writes the same
fields the enrichment pipeline writes.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.contact import Contact
from app.services import email_validator as ev
from app.services.email_enrichment import (
    VERDICT_DELIVERABLE,
    VERDICT_RISKY,
    VERDICT_UNDELIVERABLE,
    VERDICT_UNKNOWN,
)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------- offline checks


@pytest.mark.asyncio
async def test_invalid_syntax_is_undeliverable_without_network():
    v = await ev.validate_email("not-an-address", deep=False)
    assert v.verdict == VERDICT_UNDELIVERABLE
    assert v.is_valid_syntax is False
    assert "invalid syntax" in v.problems


@pytest.mark.asyncio
async def test_dead_domain_is_undeliverable():
    v = await ev.validate_email("someone@definitely-not-a-real-domain-xyzzy.test", deep=False)
    assert v.verdict == VERDICT_UNDELIVERABLE
    assert v.accepts_mail is False


@pytest.mark.asyncio
async def test_disposable_domain_is_risky_not_verified():
    v = await ev.validate_email("throwaway@mailinator.com", deep=False)
    assert v.is_disposable is True
    assert v.verdict == VERDICT_RISKY


@pytest.mark.asyncio
async def test_role_address_is_flagged():
    v = await ev.validate_email("info@gmail.com", deep=False)
    assert v.is_role_account is True


def test_normalize_punctuation():
    assert ev.normalize_email(" Ada@Example.COM ") == "ada@example.com"


# ---------------------------------------------------------- Reacher client


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_reacher_response_maps_to_verdicts(monkeypatch):
    import app.config as config
    from app.services.email_enrichment import Diagnosis

    monkeypatch.setattr(config.settings, "REACHER_API_URL", "http://reacher.local/v2/check_email")
    # Offline stage must not depend on live DNS in tests.
    monkeypatch.setattr(
        ev,
        "diagnose",
        lambda a, check_dns=True: Diagnosis(address=a, valid_syntax=True, domain="example.com",
                                            mx_found=True),
    )

    payload = {
        "input": "ada@example.com",
        "is_reachable": "safe",
        "syntax": {"is_valid_syntax": True, "suggested_email": None},
        "mx": {"accepts_mail": True},
        "smtp": {"can_connect_smtp": True, "is_catch_all": False},
        "misc": {"is_disposable": False, "is_role_account": False},
    }

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            assert url == "http://reacher.local/v2/check_email"
            assert json["to_email"] == "ada@example.com"
            return _FakeResponse(payload)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    v = await ev.validate_email("ada@example.com", deep=True)
    assert v.provider == "reacher"
    assert v.verdict == VERDICT_DELIVERABLE
    assert v.is_reachable == "safe"


@pytest.mark.asyncio
async def test_reacher_invalid_and_catch_all(monkeypatch):
    import app.config as config
    from app.services.email_enrichment import Diagnosis

    monkeypatch.setattr(config.settings, "REACHER_API_URL", "http://reacher.local/v2/check_email")
    monkeypatch.setattr(
        ev,
        "diagnose",
        lambda a, check_dns=True: Diagnosis(address=a, valid_syntax=True, domain="example.com",
                                            mx_found=True),
    )

    answers = [
        _FakeResponse({"is_reachable": "invalid", "syntax": {}, "mx": {"accepts_mail": False},
                       "smtp": {}, "misc": {}}),
        _FakeResponse({"is_reachable": "safe", "syntax": {"is_valid_syntax": True},
                       "mx": {"accepts_mail": True},
                       "smtp": {"can_connect_smtp": True, "is_catch_all": True},
                       "misc": {"is_disposable": False, "is_role_account": False}}),
    ]

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            return answers.pop(0)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    v = await ev.validate_email("gone@example.com", deep=True)
    assert v.verdict == VERDICT_UNDELIVERABLE

    v = await ev.validate_email("anyone@example.com", deep=True)
    assert v.verdict == VERDICT_RISKY  # catch-all accept proves nothing
    assert v.is_catch_all is True


@pytest.mark.asyncio
async def test_reacher_unreachable_falls_back_to_offline(monkeypatch):
    import app.config as config
    from app.services.email_enrichment import Diagnosis

    monkeypatch.setattr(config.settings, "REACHER_API_URL", "http://reacher.local/v2/check_email")
    monkeypatch.setattr(
        ev,
        "diagnose",
        lambda a, check_dns=True: Diagnosis(address=a, valid_syntax=True, domain="example.com",
                                            mx_found=True),
    )

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            raise OSError("connection refused")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(ev, "_smtp_probe", lambda a, d: ev.Verdict(
        address=a, verdict=VERDICT_DELIVERABLE, is_reachable="safe", provider="builtin"))
    v = await ev.validate_email("ada@example.com", deep=True)
    assert v.verdict == VERDICT_DELIVERABLE
    assert v.provider == "builtin"


# ---------------------------------------------------------- contact writes


@pytest.mark.asyncio
async def test_validate_contacts_marks_contacts(db):
    good = Contact(email="ada@example.com", phone_number=None, country="Nigeria")
    bad = Contact(email="gone@example.com", phone_number=None, country="Nigeria")
    none = Contact(email=None, phone_number="+2348012345678", country="Nigeria")
    db.add_all([good, bad, none])
    await db.flush()

    async def fake_validate(address, *, deep=True):
        if address.startswith("gone"):
            return ev.Verdict(address=address, verdict=VERDICT_UNDELIVERABLE,
                              is_reachable="invalid", problems=["mailbox rejected by SMTP server"])
        return ev.Verdict(address=address, verdict=VERDICT_DELIVERABLE, is_reachable="safe")

    original = ev.validate_email
    ev.validate_email = fake_validate
    try:
        result = await ev.validate_contacts(db, [good, bad, none], deep=True)
    finally:
        ev.validate_email = original

    assert result["scanned"] == 3
    assert result["no_email"] == 1
    assert result["deliverable"] == 1
    assert result["undeliverable"] == 1
    assert good.email_verified is True
    assert good.email_verified_at is not None
    assert bad.is_email_undeliverable is True
    assert bad.email_status == "invalid"
    assert bad.email_verified is not True
    # A phone-only contact is simply skipped, never touched.
    assert none.email_enriched_at is None


@pytest.mark.asyncio
async def test_unknown_never_quarantines(db):
    c = Contact(email="maybe@example.com", country="Nigeria")
    db.add(c)
    await db.flush()
    ev.apply_verdict(c, ev.Verdict(address="maybe@example.com", verdict=VERDICT_UNKNOWN,
                                   problems=["SMTP inconclusive (blocked or timed out)"]))
    await db.flush()
    assert c.is_email_undeliverable is not True
    assert c.email_verified is not True
    assert "inconclusive" in (c.email_enrichment_note or "").lower()
