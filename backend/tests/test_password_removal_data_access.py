"""Regression test: existing data must never be hidden or lost by access changes.

The scenario this guards (updated for the restored login screen):
1. The operator signs in and creates contacts, campaigns, messages
2. Later deployments change access settings (extra site password on/off,
   rotated ADMIN_PASSWORD, fresh browser, new session cookie)
3. After signing in again — with the env admin credentials or the saved
   site password — ALL the data must still be there.

Data lives in the database; the login screen only guards the door.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db


@pytest_asyncio.fixture
async def test_db():
    """Create a fresh in-memory database with all models."""
    # Import all models to ensure they're registered
    import app.models.system
    import app.models.user
    import app.models.contact
    import app.models.contact_list
    import app.models.conversation
    import app.models.campaign
    import app.models.template
    import app.models.sequence
    import app.models.followup
    import app.models.scheduled
    import app.models.suppression
    import app.models.gateway
    import app.models.notification
    import app.models.autoreply
    import app.models.automation
    import app.models.variable
    import app.models.webhook
    import app.models.meeting
    import app.models.call
    import app.models.ads
    import app.models.campaign_followup
    import app.models.audit
    import app.models.push

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    from app.main import app
    app.dependency_overrides[get_db] = override_get_db

    from app.security.rate_limit import limiter
    limiter.enabled = False

    yield

    app.dependency_overrides.clear()
    limiter.enabled = True
    await engine.dispose()


@pytest.mark.asyncio
async def test_data_accessible_from_any_browser_after_login(test_db):
    """Data created in one session must be visible after signing in again."""
    from app.main import app

    # Phase 1: Sign in with the env admin credentials and create data
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert login.status_code == 200

        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200
        admin_user_id = me.json()["id"]

        # Create contacts
        for name, phone in [("Alice", "08011111111"), ("Bob", "08022222222"), ("Charlie", "08033333333")]:
            response = await client.post(
                "/api/v1/contacts/",
                json={"first_name": name, "phone_number": phone},
            )
            assert response.status_code == 201

        # Verify contacts exist
        contacts_list = await client.get("/api/v1/contacts/")
        assert contacts_list.status_code == 200
        assert contacts_list.json()["total"] == 3

        # Create a campaign
        campaign = await client.post(
            "/api/v1/campaigns/",
            json={"name": "Test Campaign", "message_body": "Hello {{first_name}}!"},
        )
        assert campaign.status_code == 201

    # Phase 2: A completely fresh browser (no cookies) is stopped at the door…
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as fresh:
        me = await fresh.get("/api/v1/auth/me")
        assert me.status_code == 401
        contacts = await fresh.get("/api/v1/contacts/")
        assert contacts.status_code == 401

    # Phase 3: …but after signing in again it sees ALL the data.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as fresh:
        login = await fresh.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert login.status_code == 200

        me = await fresh.get("/api/v1/auth/me")
        assert me.status_code == 200
        fresh_user = me.json()
        # Should be the SAME admin user
        assert fresh_user["id"] == admin_user_id
        assert fresh_user["username"] == "admin"

        # CRITICAL: All contacts must be visible
        contacts = await fresh.get("/api/v1/contacts/")
        assert contacts.status_code == 200
        contacts_data = contacts.json()
        assert contacts_data["total"] == 3, f"Expected 3 contacts, got {contacts_data['total']}"

        # Verify specific contacts are present
        contact_names = {c["first_name"] for c in contacts_data["items"]}
        assert contact_names == {"Alice", "Bob", "Charlie"}

        # Campaigns must be visible
        campaigns = await fresh.get("/api/v1/campaigns/")
        assert campaigns.status_code == 200
        campaigns_data = campaigns.json()
        campaign_items = campaigns_data.get("items", campaigns_data if isinstance(campaigns_data, list) else [])
        assert len(campaign_items) == 1, f"Expected 1 campaign, got {len(campaign_items)}"

        # Dashboard stats must reflect the data
        dashboard = await fresh.get("/api/v1/dashboard/stats")
        assert dashboard.status_code == 200
        dashboard_data = dashboard.json()
        assert dashboard_data["total_contacts"] == 3


@pytest.mark.asyncio
async def test_toggling_site_password_keeps_data_intact(test_db):
    """Turning the extra site password on or off must not hide or delete data."""
    from app.main import app

    # Create data while signed in with the env admin credentials
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert login.status_code == 200
        for name, phone in [("Dave", "08044444444"), ("Eve", "08055555555")]:
            response = await client.post(
                "/api/v1/contacts/",
                json={"first_name": name, "phone_number": phone},
            )
            assert response.status_code == 201

    # Enable the extra site password
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
        )
        cookie = {"sendsms_session": login.cookies["sendsms_session"]}
        response = await client.put(
            "/api/v1/settings/access",
            json={"password_required": True, "password": "newpass456"},
            cookies=cookie,
        )
        assert response.status_code == 200

    # Sign in with the site password — data must still be there
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "newpass456"}
        )
        assert login.status_code == 200

        contacts = await client.get("/api/v1/contacts/")
        assert contacts.status_code == 200
        contacts_data = contacts.json()
        assert contacts_data["total"] == 2, f"Expected 2 contacts, got {contacts_data['total']}"
        contact_names = {c["first_name"] for c in contacts_data["items"]}
        assert contact_names == {"Dave", "Eve"}

        # Dashboard must agree
        dashboard = await client.get("/api/v1/dashboard/stats")
        assert dashboard.status_code == 200
        assert dashboard.json()["total_contacts"] == 2

    # Turn the extra password off again — data still intact
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "newpass456"}
        )
        cookie = {"sendsms_session": login.cookies["sendsms_session"]}
        response = await client.put(
            "/api/v1/settings/access",
            json={"password_required": False},
            cookies=cookie,
        )
        assert response.status_code == 200

        contacts = await client.get("/api/v1/contacts/")
        assert contacts.status_code == 200
        assert contacts.json()["total"] == 2
