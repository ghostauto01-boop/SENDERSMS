"""C10 — secrets are not in read responses, and the webhook token can be rotated.

The Brevo webhook token IS the credential for the inbound/events endpoint: whoever holds it can
inject fake inbound mail into the inbox and fake bounces onto real contacts. It was returned in
full -- as ``webhook_url`` and ``webhook_path`` -- by every account read, so it travelled into
logs, HAR files, screenshots and every MCP tool result. The SMS gateway's device id leaked the
same way from the diagnostics endpoints.

These tests pin: reads show only a hint of the token; the full URL comes from one deliberate
action (reveal) that an assistant (the MCP bridge) cannot perform; the token can be rotated, the
old one stops working at once; the webhook compares tokens in constant time and also accepts the
token in a header, which keeps it out of access logs.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from app.services import email_service
from tests.campaign_factory import make_account

EVENT = {"event": "delivered", "message-id": "<abc@brevo>", "email": "x@y.io"}


@pytest_asyncio.fixture
async def account(api_db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example.test", raising=False)
    acct = await make_account(api_db, default=True)
    await email_service.ensure_webhook_token(api_db, acct)
    await api_db.flush()
    return acct


def _hook(account_id, token=None):
    return f"/api/v1/webhooks/brevo/{account_id}" + (f"?token={token}" if token is not None else "")


# --------------------------------------------------------------------------
# reads are masked
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_account_reads_never_contain_the_webhook_token(api_client, account):
    token = account.webhook_token

    listing = await api_client.get("/api/v1/email/accounts")

    assert listing.status_code == 200
    assert token not in listing.text, "the full token must not be in a read response"
    row = listing.json()[0] if isinstance(listing.json(), list) else listing.json()["items"][0]
    assert "token=" in row["webhook_url"] and token not in row["webhook_url"]
    assert token not in row["webhook_path"]
    assert row["webhook_token_masked"].endswith(token[-4:])
    assert row["webhook_token_masked"] != token


@pytest.mark.asyncio
async def test_a_new_account_response_does_not_leak_its_token_either(api_client, api_db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example.test", raising=False)
    created = await api_client.post("/api/v1/email/accounts", json={
        "name": "New", "from_email": "hello@brandmail.io", "from_name": "Brand",
        "api_key": "xkeysib-test"})

    assert created.status_code == 201, created.text
    from sqlalchemy import select
    from app.models.email import EmailAccount

    stored = (await api_db.execute(select(EmailAccount))).scalars().one()
    assert stored.webhook_token and stored.webhook_token not in created.text


@pytest.mark.asyncio
async def test_tokens_are_long_and_url_safe(api_db):
    acct = await make_account(api_db)

    token = await email_service.ensure_webhook_token(api_db, acct)

    assert len(token) >= 32
    assert all(ch.isalnum() or ch in "-_" for ch in token)


# --------------------------------------------------------------------------
# reveal: one deliberate action, not available to the assistant bridge
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reveal_returns_the_full_url_once_asked_and_is_not_cacheable(api_client, account):
    response = await api_client.post(f"/api/v1/email/accounts/{account.id}/webhook/reveal")

    assert response.status_code == 200, response.text
    assert response.json()["webhook_url"].endswith(f"/brevo/{account.id}?token={account.webhook_token}")
    assert "no-store" in response.headers.get("cache-control", "")


@pytest.mark.asyncio
async def test_reveal_of_an_unknown_account_is_404(api_client):
    assert (await api_client.post("/api/v1/email/accounts/9999/webhook/reveal")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["reveal", "rotate"])
async def test_the_assistant_bridge_cannot_reveal_or_rotate(api_client, account, action):
    """The MCP bridge marks its in-process requests; these two actions refuse them."""
    response = await api_client.post(
        f"/api/v1/email/accounts/{account.id}/webhook/{action}", headers={"X-Sendsms-Via": "mcp"})

    assert response.status_code == 403
    assert account.webhook_token not in response.text
    assert response.json()["code"] == "SECRET_NOT_AVAILABLE_TO_ASSISTANTS"


@pytest.mark.asyncio
async def test_call_app_marks_its_requests_so_the_refusal_cannot_be_bypassed(
        api_client, api_db, account, monkeypatch):
    """End to end through the real bridge: the generic `api_request` escape hatch must not
    be a way around the refusal, and the secret must not appear in its output."""
    from app.mcp import server

    server.CURRENT_ADMIN_ID.set(1)
    seen = {}

    from app.main import app as fastapi_app
    from app.database import get_db

    async def _get_db():
        yield api_db

    fastapi_app.dependency_overrides[get_db] = _get_db
    original = server.httpx.AsyncClient.request

    async def spy(self, method, url, **kw):
        seen.setdefault("headers", kw.get("headers"))
        return await original(self, method, url, **kw)

    monkeypatch.setattr(server.httpx.AsyncClient, "request", spy)
    status, payload = await server.call_app(
        "POST", f"/api/v1/email/accounts/{account.id}/webhook/reveal")

    assert status == 403, payload
    assert seen["headers"].get("X-Sendsms-Via") == "mcp"
    assert account.webhook_token not in json.dumps(payload)


# --------------------------------------------------------------------------
# rotation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotation_replaces_the_token_and_the_old_one_stops_working(api_client, account):
    old = account.webhook_token
    assert (await api_client.post(_hook(account.id, old), json=EVENT)).status_code == 200

    rotated = await api_client.post(f"/api/v1/email/accounts/{account.id}/webhook/rotate")

    assert rotated.status_code == 200, rotated.text
    new_url = rotated.json()["webhook_url"]
    new = new_url.split("token=", 1)[1]
    assert new != old and len(new) >= 32
    assert rotated.json()["previous_token_revoked"] is True
    assert "no-store" in rotated.headers.get("cache-control", "")
    assert (await api_client.post(_hook(account.id, old), json=EVENT)).status_code == 403, \
        "the old token is dead immediately"
    assert (await api_client.post(_hook(account.id, new), json=EVENT)).status_code == 200
    # ...and a plain read shows only the hint of the new one:
    listing = await api_client.get("/api/v1/email/accounts")
    assert new not in listing.text


@pytest.mark.asyncio
async def test_rotating_an_unknown_account_is_404(api_client):
    assert (await api_client.post("/api/v1/email/accounts/9999/webhook/rotate")).status_code == 404


# --------------------------------------------------------------------------
# verifying the token
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_webhook_compares_tokens_in_constant_time(api_client, account, monkeypatch):
    import hmac

    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr("app.api.v1.webhooks.hmac.compare_digest", spy)

    response = await api_client.post(_hook(account.id, account.webhook_token), json=EVENT)

    assert response.status_code == 200
    assert calls, "tokens must be compared with hmac.compare_digest, not =="


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "", "wrong", "x" * 200, "é" * 40])
async def test_a_bad_or_missing_token_is_refused_cleanly(api_client, account, token):
    response = await api_client.post(_hook(account.id, token), json=EVENT)

    assert response.status_code == 403, response.text
    assert account.webhook_token not in response.text


@pytest.mark.asyncio
async def test_the_token_may_travel_in_a_header_instead_of_the_url(api_client, account):
    """Query strings end up in access logs and referrers; a header does not."""
    by_header = await api_client.post(
        _hook(account.id), json=EVENT, headers={"X-Webhook-Token": account.webhook_token})
    by_bearer = await api_client.post(
        _hook(account.id), json=EVENT, headers={"Authorization": f"Bearer {account.webhook_token}"})
    wrong = await api_client.post(
        _hook(account.id), json=EVENT, headers={"X-Webhook-Token": "nope"})

    assert by_header.status_code == 200 and by_bearer.status_code == 200
    assert wrong.status_code == 403


# --------------------------------------------------------------------------
# the setup guide and the SMS device id
# --------------------------------------------------------------------------


def _brevo_step(guide):
    steps = [s for group in guide["groups"] for s in group["steps"]]
    return next(s for s in steps if s["id"] == "brevo-webhook")


@pytest.mark.asyncio
async def test_the_setup_guide_does_not_embed_the_token(api_client, account):
    """The guide is a read endpoint (and an assistant reads it). The dashboard's copy button
    still works: the copy row says where to fetch the real URL instead of containing it."""
    response = await api_client.get("/api/v1/guide")

    assert response.status_code == 200, response.text
    assert account.webhook_token not in response.text
    row = _brevo_step(response.json())["copy"][0]
    assert "token=" in row["value"] and account.webhook_token not in row["value"]
    assert row["reveal"] == {
        "method": "POST",
        "path": f"/api/v1/email/accounts/{account.id}/webhook/reveal",
        "field": "webhook_url",
    }


@pytest.mark.asyncio
async def test_the_sms_device_id_is_masked_in_diagnostics(api_client, monkeypatch):
    from app.providers import smsgate

    full = "d3adbeef0123456789abcdef0123456789wxyz"

    async def devices():
        return [{"id": full, "name": "Pixel 7", "lastSeen": "2026-10-10T08:00:00Z"}]

    async def hooks():
        return {"webhooks": []}

    monkeypatch.setattr(smsgate, "get_devices_direct", devices)
    monkeypatch.setattr(smsgate, "list_webhooks_direct", hooks)

    response = await api_client.get("/api/v1/inbox/device-info")

    assert response.status_code == 200, response.text
    assert full not in response.text
    device = response.json()["devices"][0]
    assert device["name"] == "Pixel 7", "everything but the id is still shown"
    assert device["id"].endswith(full[-4:]) and device["id"] != full
