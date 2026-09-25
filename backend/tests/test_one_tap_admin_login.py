"""One-tap admin sign-in — the login wall is gone.

The login screen is a single "Log in as admin" button. It posts
``/api/v1/auth/admin`` with no body at all and gets the operator's session
cookie back. Nothing is typed, nothing is verified, and — critically — nothing
in the database changes apart from ``last_login``.

Guards against the regressions that would put the wall back or hide data:

1. A request with no credentials must be signed in, not rejected.
2. The button must hand back the *existing* operator row, not a second admin.
3. Data created before the change must be visible after tapping the button.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.test_inbound_receive import client, db  # noqa: F401


@pytest.fixture(autouse=True)
def _no_rate_limit():
    from app.security.rate_limit import limiter
    limiter.enabled = False
    yield
    limiter.enabled = True


@pytest.mark.asyncio
async def test_button_signs_in_without_any_credentials(client):
    """POST /auth/admin with an empty body — this is the button."""
    r = await client.post("/api/v1/auth/admin")
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
async def test_login_endpoint_also_accepts_an_empty_password(client):
    """The older /auth/login route takes the same one-tap path."""
    for payload in ({}, {"username": "", "password": ""}, {"password": "   "}):
        r = await client.post("/api/v1/auth/login", json=payload)
        assert r.status_code == 200, (payload, r.text)


@pytest.mark.asyncio
async def test_tapping_twice_reuses_the_same_operator_row(client, db):
    """The button must not create a second admin account."""
    from app.models.user import User

    first = await client.post("/api/v1/auth/admin")
    second = await client.post("/api/v1/auth/admin")
    assert first.status_code == second.status_code == 200
    assert first.json()["user_id"] == second.json()["user_id"]

    admins = (
        await db.execute(select(User).where(User.role == "admin"))
    ).scalars().all()
    assert len(admins) == 1


@pytest.mark.asyncio
async def test_existing_data_survives_the_one_tap_sign_in(client):
    """Data made before the wall came down is still there after the button."""
    sign_in = await client.post("/api/v1/auth/admin")
    cookie = {"sendsms_session": sign_in.cookies["sendsms_session"]}

    for name, phone in [("Ada", "08011111111"), ("Bola", "08022222222")]:
        created = await client.post(
            "/api/v1/contacts/",
            json={"first_name": name, "phone_number": phone},
            cookies=cookie,
        )
        assert created.status_code == 201, created.text

    # A completely fresh browser: stopped at the door until it taps the button.
    async with AsyncClient(
        transport=ASGITransport(app=client._transport.app), base_url="http://test"
    ) as fresh:
        assert (await fresh.get("/api/v1/contacts/")).status_code == 401

        tapped = await fresh.post("/api/v1/auth/admin")
        assert tapped.status_code == 200, tapped.text
        fresh_cookie = {"sendsms_session": tapped.cookies["sendsms_session"]}

        contacts = await fresh.get("/api/v1/contacts/", cookies=fresh_cookie)
        assert contacts.status_code == 200, contacts.text
        data = contacts.json()
        assert data["total"] == 2, f"Expected 2 contacts, got {data['total']}"
        assert {c["first_name"] for c in data["items"]} == {"Ada", "Bola"}

        stats = await fresh.get("/api/v1/dashboard/stats", cookies=fresh_cookie)
        assert stats.status_code == 200, stats.text
        assert stats.json()["total_contacts"] == 2


@pytest.mark.asyncio
async def test_passwords_still_work_if_someone_still_sends_one(client):
    """Removing the wall must not break the older credential logins."""
    env_login = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert env_login.status_code == 200, env_login.text

    legacy = await client.post(
        "/api/v1/auth/login", json={"password": "12345678"}
    )
    assert legacy.status_code == 200, legacy.text

    wrong = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "nope"}
    )
    assert wrong.status_code == 401
