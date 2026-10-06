"""The password wall, back on.

Sign-in is one password (``ADMIN_PASSWORD``, default ``12345678``) and nothing
else. These cases pin the behaviour down, because it is the one thing that can
lock the operator out of their own data:

1. the app password signs in and hands back the operator's session cookie;
2. no password, or a wrong one, is refused — and no cookie is issued;
3. tapping the button twice never creates a second admin row;
4. data created before the password was restored is still there afterwards;
5. ``/auth/access`` tells the UI a password is required without ever returning
   the password or its hash;
6. rotating ``ADMIN_PASSWORD`` in the environment takes effect on the next
   login (this is the failure that used to strand an operator).
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from tests.test_inbound_receive import client, db  # noqa: F401

#: The password this deployment is configured with — read from settings rather
#: than hardcoded, so the tests describe "the configured password works".
APP_PASSWORD = settings.ADMIN_PASSWORD


@pytest.fixture(autouse=True)
def _no_rate_limit():
    from app.security.rate_limit import limiter
    limiter.enabled = False
    yield
    limiter.enabled = True


@pytest.mark.asyncio
async def test_admin_endpoint_accepts_the_app_password(client):
    """POST /auth/admin {"password": …} — this is what the login form sends."""
    r = await client.post("/api/v1/auth/admin", json={"password": APP_PASSWORD})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["username"] == "admin"
    assert body["role"] == "admin"
    assert "sendsms_session" in r.headers.get("set-cookie", "")

    me = await client.get(
        "/api/v1/auth/me",
        cookies={"sendsms_session": r.cookies["sendsms_session"]},
    )
    assert me.status_code == 200, me.text
    assert me.json()["username"] == "admin"


@pytest.mark.asyncio
async def test_admin_endpoint_refuses_without_a_password(client):
    """No password at all is a 401 — the wall is back on."""
    r = await client.post("/api/v1/auth/admin")
    assert r.status_code == 401, r.text
    assert "password" in r.json()["detail"].lower()
    assert "sendsms_session" not in r.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_admin_endpoint_refuses_a_wrong_password(client):
    r = await client.post("/api/v1/auth/admin", json={"password": "definitely-not-it"})
    assert r.status_code == 401
    assert "sendsms_session" not in r.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_login_endpoint_requires_the_password_too(client):
    """The older /auth/login route behaves the same as the button."""
    refused = await client.post("/api/v1/auth/login", json={})
    assert refused.status_code == 401

    for payload in (
        {"username": "admin", "password": APP_PASSWORD},
        {"password": APP_PASSWORD},
        {"username": "", "password": APP_PASSWORD},
    ):
        r = await client.post("/api/v1/auth/login", json=payload)
        assert r.status_code == 200, (payload, r.text)


@pytest.mark.asyncio
async def test_signing_in_twice_reuses_the_same_operator_row(client, db):
    """One operator account, however many times the form is submitted."""
    from app.models.user import User

    first = await client.post("/api/v1/auth/admin", json={"password": APP_PASSWORD})
    second = await client.post("/api/v1/auth/admin", json={"password": APP_PASSWORD})
    assert first.status_code == second.status_code == 200
    assert first.json()["user_id"] == second.json()["user_id"]

    admins = (await db.execute(select(User).where(User.role == "admin"))).scalars().all()
    assert len(admins) == 1


@pytest.mark.asyncio
async def test_existing_data_survives_the_password_sign_in(client):
    """Data made before the wall came back is still there after signing in."""
    sign_in = await client.post("/api/v1/auth/admin", json={"password": APP_PASSWORD})
    cookie = {"sendsms_session": sign_in.cookies["sendsms_session"]}

    for name, phone in [("Ada", "08011111111"), ("Bola", "08022222222")]:
        created = await client.post(
            "/api/v1/contacts/",
            json={"first_name": name, "phone_number": phone},
            cookies=cookie,
        )
        assert created.status_code == 201, created.text

    # A completely fresh browser: stopped at the door until it signs in.
    async with AsyncClient(
        transport=ASGITransport(app=client._transport.app), base_url="http://test"
    ) as fresh:
        assert (await fresh.get("/api/v1/contacts/")).status_code == 401

        blocked = await fresh.post("/api/v1/auth/admin")
        assert blocked.status_code == 401

        signed_in = await fresh.post(
            "/api/v1/auth/admin", json={"password": APP_PASSWORD}
        )
        assert signed_in.status_code == 200, signed_in.text
        fresh_cookie = {"sendsms_session": signed_in.cookies["sendsms_session"]}

        contacts = await fresh.get("/api/v1/contacts/", cookies=fresh_cookie)
        assert contacts.status_code == 200, contacts.text
        data = contacts.json()
        assert data["total"] == 2, f"Expected 2 contacts, got {data['total']}"
        assert {c["first_name"] for c in data["items"]} == {"Ada", "Bola"}

        stats = await fresh.get("/api/v1/dashboard/stats", cookies=fresh_cookie)
        assert stats.status_code == 200, stats.text
        assert stats.json()["total_contacts"] == 2


@pytest.mark.asyncio
async def test_access_endpoint_reports_the_wall_without_leaking_it(client):
    """The login screen needs to know a password is required — and nothing more."""
    r = await client.get("/api/v1/auth/access")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["password_required"] is True
    assert body["source"] in {"admin", "legacy", "site"}
    # The password itself, and every hash of it, must never be in this payload.
    assert APP_PASSWORD not in r.text
    assert "password_hash" not in r.text


@pytest.mark.asyncio
async def test_rotating_the_env_password_takes_effect_on_the_next_login(client, monkeypatch):
    """Changing ADMIN_PASSWORD in the deployment must not lock the operator out."""
    original = settings.ADMIN_PASSWORD
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "rotated-by-the-operator", raising=False)

    stale = await client.post("/api/v1/auth/admin", json={"password": original})
    assert stale.status_code == 401

    fresh = await client.post(
        "/api/v1/auth/admin", json={"password": "rotated-by-the-operator"}
    )
    assert fresh.status_code == 200, fresh.text


@pytest.mark.asyncio
async def test_site_password_also_signs_in(client):
    """The optional extra password from Settings → Site access still works."""
    signed_in = await client.post("/api/v1/auth/admin", json={"password": APP_PASSWORD})
    cookie = {"sendsms_session": signed_in.cookies["sendsms_session"]}
    put = await client.put(
        "/api/v1/settings/access",
        json={"password_required": True, "password": "extra-pass"},
        cookies=cookie,
    )
    assert put.status_code == 200, put.text

    async with AsyncClient(
        transport=ASGITransport(app=client._transport.app), base_url="http://test"
    ) as fresh:
        r = await fresh.post("/api/v1/auth/login", json={"username": "admin", "password": "extra-pass"})
        assert r.status_code == 200, r.text
