"""The site is open by default. A password wall is optional and off until Settings turns it on."""
import pytest
from httpx import ASGITransport, AsyncClient

from tests.test_inbound_receive import client, db  # noqa: F401


@pytest.fixture(autouse=True)
def _no_rate_limit():
    from app.security.rate_limit import limiter
    limiter.enabled = False
    yield
    limiter.enabled = True


@pytest.mark.asyncio
async def test_site_is_open_without_a_cookie(client):
    access = await client.get("/api/v1/auth/access")
    assert access.status_code == 200
    assert access.json()["password_required"] is False

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["username"] == "admin"

    # A normal API route must work with nothing but the site address.
    contacts = await client.get("/api/v1/contacts/")
    assert contacts.status_code == 200, contacts.text


@pytest.mark.asyncio
async def test_settings_can_turn_the_password_wall_back_on(client):
    missing = await client.put("/api/v1/settings/access", json={"password_required": True})
    assert missing.status_code == 400

    saved = await client.put(
        "/api/v1/settings/access",
        json={"password_required": True, "password": "later-pass"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["password_required"] is True
    assert saved.json()["password_set"] is True
    assert "sendsms_session" in saved.headers.get("set-cookie", "")

    # A browser that never signed in is stopped at the gate.
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as fresh:
        assert (await fresh.get("/api/v1/auth/access")).json()["password_required"] is True
        assert (await fresh.get("/api/v1/auth/me")).status_code == 401
        assert (await fresh.post("/api/v1/auth/login", json={"password": "12345678"})).status_code == 401
        good = await fresh.post("/api/v1/auth/login", json={"password": "later-pass"})
        assert good.status_code == 200, good.text
        assert (await fresh.get("/api/v1/auth/me")).status_code == 200

        # Turning it off again makes the address enough.
        off = await fresh.put("/api/v1/settings/access", json={"password_required": False})
        assert off.status_code == 200
        assert off.json()["password_required"] is False

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as stranger:
        assert (await stranger.get("/api/v1/auth/me")).status_code == 200
