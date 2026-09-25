"""Regression test: data created with password ON must be accessible after password is removed.

This test simulates the exact scenario where:
1. User has password wall enabled
2. User logs in and creates contacts, campaigns, messages
3. User disables the password wall
4. A fresh browser (no cookies) must still see ALL the data

This ensures data is never lost or hidden when switching access modes.
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
async def test_data_accessible_after_password_removal(test_db):
    """Data created with password ON must be visible after password is removed."""
    from app.main import app

    # Phase 1: Enable password wall
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/v1/settings/access",
            json={"password_required": True, "password": "testpass123"},
        )
        assert response.status_code == 200
        assert response.json()["password_required"] is True

    # Phase 2: Login and create data
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Login
        login = await client.post("/api/v1/auth/login", json={"password": "testpass123"})
        assert login.status_code == 200

        # Get user info
        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200
        admin_user_id = me.json()["id"]

        # Create contacts
        contacts_created = []
        for name, phone in [("Alice", "08011111111"), ("Bob", "08022222222"), ("Charlie", "08033333333")]:
            response = await client.post(
                "/api/v1/contacts/",
                json={"first_name": name, "phone_number": phone},
            )
            assert response.status_code == 201
            contacts_created.append(response.json())

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

    # Phase 3: Disable password wall
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Login first
        login = await client.post("/api/v1/auth/login", json={"password": "testpass123"})
        assert login.status_code == 200

        # Turn off password
        response = await client.put(
            "/api/v1/settings/access",
            json={"password_required": False},
        )
        assert response.status_code == 200
        assert response.json()["password_required"] is False

    # Phase 4: Fresh browser (no cookies) must see ALL data
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as fresh_client:
        # Verify password is off
        access = await fresh_client.get("/api/v1/auth/access")
        assert access.status_code == 200
        assert access.json()["password_required"] is False

        # Verify user is authenticated (via ensure_admin)
        me = await fresh_client.get("/api/v1/auth/me")
        assert me.status_code == 200
        fresh_user = me.json()
        # Should be the SAME admin user
        assert fresh_user["id"] == admin_user_id
        assert fresh_user["username"] == "admin"

        # CRITICAL: All contacts must be visible
        contacts = await fresh_client.get("/api/v1/contacts/")
        assert contacts.status_code == 200
        contacts_data = contacts.json()
        assert contacts_data["total"] == 3, f"Expected 3 contacts, got {contacts_data['total']}"

        # Verify specific contacts are present
        contact_names = {c["first_name"] for c in contacts_data["items"]}
        assert contact_names == {"Alice", "Bob", "Charlie"}

        # Campaigns must be visible
        campaigns = await fresh_client.get("/api/v1/campaigns/")
        assert campaigns.status_code == 200
        campaigns_data = campaigns.json()
        campaign_items = campaigns_data.get("items", campaigns_data if isinstance(campaigns_data, list) else [])
        assert len(campaign_items) == 1, f"Expected 1 campaign, got {len(campaign_items)}"

        # Dashboard stats must reflect the data
        dashboard = await fresh_client.get("/api/v1/dashboard/stats")
        assert dashboard.status_code == 200
        dashboard_data = dashboard.json()
        assert dashboard_data["total_contacts"] == 3


@pytest.mark.asyncio
async def test_password_can_be_reenabled_without_data_loss(test_db):
    """Turning password back on must not hide or delete any data."""
    from app.main import app

    # Start with password off, create data
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create contacts without password
        for name, phone in [("Dave", "08044444444"), ("Eve", "08055555555")]:
            response = await client.post(
                "/api/v1/contacts/",
                json={"first_name": name, "phone_number": phone},
            )
            assert response.status_code == 201

    # Enable password
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/v1/settings/access",
            json={"password_required": True, "password": "newpass456"},
        )
        assert response.status_code == 200

    # Login and verify data is still there
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"password": "newpass456"})
        assert login.status_code == 200

        contacts = await client.get("/api/v1/contacts/")
        assert contacts.status_code == 200
        assert contacts.json()["total"] == 2
