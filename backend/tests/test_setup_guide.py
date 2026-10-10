"""The in-app setup tutorial.

The guide is only useful if its status is *true*, so these tests drive it from
both ends: a bare install must report the five things that block sending, and
each one must flip to done when the corresponding setting or row appears. They
also pin the contract the frontend renders — every step needs a title, a reason,
a status from the known set, and a route that actually exists in the app.
"""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base, get_db
from app.models.email import EmailAccount
from app.models.email_inbox import EmailMailbox
from app.models.user import User
from app.security.auth import get_current_user
from app.security.encryption import encrypt_value
from app.services import setup_guide


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def bare_environment(monkeypatch):
    """A deployment that has just been started and configured with nothing."""
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.setattr(settings, "SMSGATE_BASE_URL", "")
    monkeypatch.setattr(settings, "SMSGATE_USERNAME", "")
    monkeypatch.setattr(settings, "SMSGATE_PASSWORD", "")
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "")
    yield


def _step(guide, step_id):
    return next(s for s in guide["steps"] if s["id"] == step_id)


def _ids(guide, status=None):
    return {s["id"] for s in guide["steps"] if status is None or s["status"] == status}


# ==========================================================================
# A bare install
# ==========================================================================


@pytest.mark.asyncio
async def test_a_bare_install_reports_everything_that_blocks_sending(db):
    guide = await setup_guide.build_guide(db)

    assert guide["progress"]["ready_to_send"] is False
    blocking = _ids(guide, "todo")
    # The five that stop anything from working at all.
    assert {"public-base-url", "sms-gateway", "brevo-sender", "mailbox", "mcp"} <= blocking

    # ... and the ones that are configured-by-default are reported as done, so
    # the operator is not sent chasing things that are already fine.
    assert _step(guide, "database")["status"] in ("done", "attention")
    assert _step(guide, "worker")["status"] in ("done", "attention")
    assert _step(guide, "sim-slot")["status"] == "done"


@pytest.mark.asyncio
async def test_the_public_url_step_explains_what_it_breaks(db):
    guide = await setup_guide.build_guide(db)
    step = _step(guide, "public-base-url")

    assert step["status"] == "todo"
    # The three symptoms, named: inbound SMS, unsubscribe, AI connectors.
    assert "webhook" in step["why"]
    assert "unsubscribe" in step["why"]
    assert "OAuth" in step["why"] or "AI client" in step["why"]
    assert any("PUBLIC_BASE_URL" in s for s in step["steps"])

    # Without it, the steps that depend on it say so instead of showing a blank URL.
    assert _step(guide, "sms-webhook")["status"] == "todo"
    assert "PUBLIC_BASE_URL" in _step(guide, "sms-webhook")["detail"]
    assert _step(guide, "mcp")["status"] == "todo"


@pytest.mark.asyncio
async def test_the_mailbox_step_gives_the_app_password_route_first(db):
    """The operator has no domain, so IMAP + app password is the primary path."""
    guide = await setup_guide.build_guide(db)
    step = _step(guide, "mailbox")

    assert step["status"] == "todo"
    joined = " ".join(step["steps"])
    assert "apppasswords" in joined           # where to get the app password
    assert "2-Step Verification" in joined    # the prerequisite Google insists on
    assert "Spam" in joined                   # the rescue toggle that fixes the complaint
    assert "GOOGLE_CLIENT_ID" in joined       # OAuth is documented as the optional extra
    assert step["route"] == "/email-manager?section=Replies"


# ==========================================================================
# Each step flips when it is fixed
# ==========================================================================


@pytest.mark.asyncio
async def test_setting_the_public_url_opens_the_steps_that_needed_it(db, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example.test")
    guide = await setup_guide.build_guide(db)

    assert _step(guide, "public-base-url")["status"] == "done"
    assert _step(guide, "public-base-url")["copy"][0]["value"] == "https://app.example.test"

    webhook = _step(guide, "sms-webhook")
    assert webhook["status"] == "todo"       # still to register, but the URL exists now
    assert webhook["copy"][0]["value"] == "https://app.example.test/api/v1/webhooks/smsgateway"

    # The AI section is no longer blocked outright; it asks to be connected.
    mcp = _step(guide, "mcp")
    assert mcp["status"] == "todo"
    assert any("connectors/chatgpt/mcp" in c["value"] for c in mcp["copy"])
    assert any("connectors/claude/mcp" in c["value"] for c in mcp["copy"])
    assert any("connectors/arena/mcp" in c["value"] for c in mcp["copy"])


@pytest.mark.asyncio
async def test_a_gateway_and_a_sender_turn_their_steps_done(db, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example.test")
    monkeypatch.setattr(settings, "SMSGATE_BASE_URL", "https://gate.example.test")
    monkeypatch.setattr(settings, "SMSGATE_USERNAME", "user")
    monkeypatch.setattr(settings, "SMSGATE_PASSWORD", "pass")

    db.add(EmailAccount(
        name="Main", from_name="Acme", from_email="hello@acme-leads.io",
        reply_to="hello@acme-leads.io", api_key_encrypted=encrypt_value("key"),
        is_default=True, is_active=True, connection_status="ok",
    ))
    await db.flush()

    guide = await setup_guide.build_guide(db)
    assert _step(guide, "sms-gateway")["status"] == "done"

    # Registering the webhook is a separate step, and the app knows it is not done.
    webhook = _step(guide, "sms-webhook")
    assert webhook["status"] == "todo"
    assert "Not registered yet" in webhook["detail"]

    sender = _step(guide, "brevo-sender")
    assert sender["status"] == "done"
    assert "hello@acme-leads.io" in sender["detail"]

    brevo_hook = _step(guide, "brevo-webhook")
    assert brevo_hook["status"] == "attention"
    row = brevo_hook["copy"][0]
    value = row["value"]
    assert value.startswith("https://app.example.test/api/v1/webhooks/brevo/")
    # C10: the token is a credential and the guide is a read endpoint (an assistant reads
    # it too), so the row carries a masked hint -- this used to assert a full-length token
    # -- and says where the dashboard fetches the real URL when the copy button is pressed.
    assert "token=****" in value and len(value.split("token=")[1]) == 8
    assert row["reveal"]["path"].endswith("/webhook/reveal")
    assert row["reveal"]["field"] == "webhook_url"


@pytest.mark.asyncio
async def test_an_unregistered_webhook_becomes_attention_not_todo(db, monkeypatch):
    """The worst case: the gateway is posting to an address that no longer exists."""
    from app.services.system_settings import WEBHOOK_REGISTERED, set_setting

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example.test")
    monkeypatch.setattr(settings, "SMSGATE_BASE_URL", "https://gate.example.test")
    monkeypatch.setattr(settings, "SMSGATE_USERNAME", "user")
    monkeypatch.setattr(settings, "SMSGATE_PASSWORD", "pass")
    await set_setting(db, WEBHOOK_REGISTERED, "https://old-address.example.test/api/v1/webhooks/smsgateway")
    await db.commit()

    guide = await setup_guide.build_guide(db)
    webhook = _step(guide, "sms-webhook")
    assert webhook["status"] == "attention"
    assert "old-address.example.test" in webhook["detail"]
    assert webhook["copy"][0]["value"] == "https://app.example.test/api/v1/webhooks/smsgateway"


@pytest.mark.asyncio
async def test_connecting_a_mailbox_clears_the_reply_step(db):
    db.add(EmailMailbox(
        name="Replies — me@gmail.com", provider="imap", email_address="me@gmail.com",
        credential_encrypted=encrypt_value("sixteen-char-pwd"), folders="INBOX,[Gmail]/Spam",
        is_active=True, total_replies=3, total_rescued=1, last_sync_status="ok",
    ))
    await db.flush()

    guide = await setup_guide.build_guide(db)
    mailbox = _step(guide, "mailbox")
    assert mailbox["status"] == "done"
    assert "3 replies imported" in mailbox["detail"]
    assert "1 rescued from Spam" in mailbox["detail"]


@pytest.mark.asyncio
async def test_a_failing_mailbox_sync_is_surfaced(db):
    db.add(EmailMailbox(
        name="Replies — me@gmail.com", provider="imap", email_address="me@gmail.com",
        credential_encrypted=encrypt_value("sixteen-char-pwd"), is_active=True,
        last_sync_status="error", last_error="Gmail refused the sign-in",
    ))
    await db.flush()

    guide = await setup_guide.build_guide(db)
    mailbox = _step(guide, "mailbox")
    assert mailbox["status"] == "attention"
    assert "Gmail refused the sign-in" in mailbox["detail"]


@pytest.mark.asyncio
async def test_a_freemail_sender_is_named_as_the_spam_cause(db):
    """The diagnosis the operator actually asked for, in the tutorial."""
    db.add(EmailAccount(
        name="Main", from_name="Me", from_email="me@gmail.com", reply_to="me@gmail.com",
        api_key_encrypted=encrypt_value("key"), is_default=True, is_active=True,
    ))
    await db.flush()

    guide = await setup_guide.build_guide(db)
    routing = _step(guide, "reply-routing")
    assert routing["status"] == "attention"
    assert "DMARC" in routing["why"]
    assert "me@gmail.com" in routing["detail"]
    assert any("domain you control" in s for s in routing["steps"])


@pytest.mark.asyncio
async def test_ready_to_send_flips_once_the_blocking_steps_are_fixed(db, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example.test")
    monkeypatch.setattr(settings, "SMSGATE_BASE_URL", "https://gate.example.test")
    monkeypatch.setattr(settings, "SMSGATE_USERNAME", "user")
    monkeypatch.setattr(settings, "SMSGATE_PASSWORD", "pass")
    monkeypatch.setattr(settings, "SECRET_KEY", "a-real-secret-key-that-is-long-enough-1234567890")
    monkeypatch.setattr(settings, "CREDENTIAL_ENCRYPTION_KEY", "YS1yZWFsLWtleS10aGF0LWlzLTMyLWJ5dGVzLWxvbmc=")

    db.add(EmailAccount(
        name="Main", from_name="Acme", from_email="hello@acme-leads.io",
        reply_to="replies@acme-leads.io", api_key_encrypted=encrypt_value("key"),
        is_default=True, is_active=True, connection_status="ok",
    ))
    await db.flush()

    guide = await setup_guide.build_guide(db)
    assert _step(guide, "secrets")["status"] == "done"
    assert guide["progress"]["ready_to_send"] is True
    # The mailbox and the connectors are still open, but they do not block sending.
    assert "mailbox" in _ids(guide, "todo")
    assert guide["progress"]["blocking"] >= 2


# ==========================================================================
# The contract the frontend renders
# ==========================================================================


@pytest.mark.asyncio
async def test_every_step_is_renderable(db, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example.test")
    guide = await setup_guide.build_guide(db)

    allowed = {"done", "todo", "attention", "optional"}
    routes = {
        "/settings", "/send", "/email-manager", "/email-manager?section=Replies",
        "/contacts", "/dashboard", "/overview", "/setup", None,
    }
    assert guide["steps"], "the guide must never be empty"
    for step in guide["steps"]:
        assert step["status"] in allowed, step
        assert step["title"] and step["why"], step
        assert isinstance(step["steps"], list) and isinstance(step["copy"], list), step
        assert step["route"] in routes, f"unknown route {step['route']} in {step['id']}"
        assert step["group"] in {g["id"] for g in guide["groups"]}, step

    # Groups keep their order and account for every step.
    assert [g["id"] for g in guide["groups"]] == [
        "foundation", "sms", "email", "replies", "ai", "optional",
    ]
    assert sum(g["total"] for g in guide["groups"]) == len(guide["steps"])
    assert sum(g["done"] for g in guide["groups"]) == guide["progress"]["done"]

    # Percent is honest: done + optional over the total.
    progress = guide["progress"]
    assert progress["total"] == progress["done"] + progress["blocking"] + progress["optional"]
    assert 0 <= progress["percent"] <= 100


@pytest.mark.asyncio
async def test_summary_is_the_badge_shape(db):
    summary = await setup_guide.summary(db)
    assert {"done", "blocking", "optional", "total", "percent", "ready_to_send",
            "next_step", "next_route"} <= set(summary)
    assert summary["blocking"] > 0
    assert summary["next_step"]            # the badge can name what to do next


@pytest.mark.asyncio
async def test_a_broken_section_cannot_blank_the_guide(db, monkeypatch):
    """One failing query must degrade to a shorter guide, not an error page."""
    async def boom(_db):
        raise RuntimeError("mailbox table missing")

    monkeypatch.setattr(setup_guide, "_reply_steps", boom)
    guide = await setup_guide.build_guide(db)
    assert guide["steps"]
    assert not any(s["group"] == "replies" for s in guide["steps"])
    assert any(s["id"] == "public-base-url" for s in guide["steps"])


# ==========================================================================
# The API
# ==========================================================================


@pytest_asyncio.fixture
async def client(db):
    from app.main import app

    async def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="tester", email="tester@example.com",
        password_hash="x", role="admin", is_active=True,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_guide_endpoints_answer(client):
    full = await client.get("/api/v1/guide")
    assert full.status_code == 200, full.text
    payload = full.json()
    assert payload["groups"] and payload["steps"] and payload["progress"]
    assert payload["environment"]["app_env"] == "development"

    light = await client.get("/api/v1/guide/summary")
    assert light.status_code == 200
    assert light.json()["total"] == payload["progress"]["total"]
    # The whole guide is JSON-serialisable end to end (no datetimes leaking).
    json.dumps(payload)
