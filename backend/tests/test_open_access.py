"""A session cookie is still needed, but getting one takes no password.

The login screen is a single "Log in as admin" button (see
``test_one_tap_admin_login``). These cases cover what is left of the
credential path: an old client that still sends ADMIN_PASSWORD, the optional
site password from Settings → Site access, and the stored hash following the
environment when ADMIN_PASSWORD is rotated on Render.
"""
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
async def test_api_requires_login(client):
    """A browser that never signed in gets 401 everywhere."""
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 401

    contacts = await client.get("/api/v1/contacts/")
    assert contacts.status_code == 401


@pytest.mark.asyncio
async def test_login_with_env_admin_credentials(client):
    """ADMIN_USERNAME + ADMIN_PASSWORD from the environment sign in."""
    r = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert r.status_code == 200, r.text
    assert "sendsms_session" in r.headers.get("set-cookie", "")

    me = await client.get(
        "/api/v1/auth/me",
        cookies={"sendsms_session": r.cookies["sendsms_session"]},
    )
    assert me.status_code == 200
    assert me.json()["username"] == "admin"

    # A signed-in browser can use the API normally.
    contacts = await client.get(
        "/api/v1/contacts/",
        cookies={"sendsms_session": r.cookies["sendsms_session"]},
    )
    assert contacts.status_code == 200, contacts.text


@pytest.mark.asyncio
async def test_wrong_credentials_rejected(client):
    r = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "totally-wrong"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid username or password"


@pytest.mark.asyncio
async def test_changing_env_admin_password_takes_effect(client, db, monkeypatch):
    """The regression that locked the operator out.

    The admin row used to be bootstrapped once with the bootstrap-time
    ADMIN_PASSWORD. Changing the variable in Render never updated the stored
    hash, so the real password was rejected as "invalid username or
    password" forever. The stored hash must follow the environment.
    """
    from app.config import settings

    # The operator first signs in with the original env password.
    ok = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert ok.status_code == 200, ok.text

    # Then ADMIN_PASSWORD is rotated in the Render environment.
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "brand-new-render-pass", raising=False)

    stale = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert stale.status_code == 401

    fresh = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "brand-new-render-pass"},
    )
    assert fresh.status_code == 200, fresh.text
    me = await client.get(
        "/api/v1/auth/me",
        cookies={"sendsms_session": fresh.cookies["sendsms_session"]},
    )
    assert me.status_code == 200


@pytest.mark.asyncio
async def test_stale_admin_row_is_repaired_on_login(client, db):
    """A database from an older deploy stores a hash that matches nothing —
    the env password must still sign in and repair the row."""
    from app.models.user import User

    db.add(User(username="admin", password_hash="stale-hash", role="admin", is_active=True))
    await db.flush()

    r = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_site_password_also_signs_in(client):
    """The optional extra password from Settings → Site access works too."""
    saved = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
    )
    cookie = {"sendsms_session": saved.cookies["sendsms_session"]}
    put = await client.put(
        "/api/v1/settings/access",
        json={"password_required": True, "password": "extra-pass"},
        cookies=cookie,
    )
    assert put.status_code == 200, put.text

    # A fresh browser signs in with the extra password (any username).
    async with AsyncClient(transport=ASGITransport(app=client._transport.app), base_url="http://test") as fresh:
        r = await fresh.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "extra-pass"},
        )
        assert r.status_code == 200, r.text
        me = await fresh.get(
            "/api/v1/auth/me",
            cookies={"sendsms_session": r.cookies["sendsms_session"]},
        )
        assert me.status_code == 200
