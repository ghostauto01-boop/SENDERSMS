"""Tests for the multi-gateway settings endpoints.

The operator experience this release promises: see both gateways, test each,
and flip the active one from Settings without touching env vars again.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base, get_db
from app.models.user import User


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db):
    from app.main import app
    from app.security.auth import get_current_user
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="admin", password_hash="x", role="admin"
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class TestGatewaysEndpoint:
    @pytest.mark.asyncio
    async def test_lists_both_gateways_with_default_active(self, client, monkeypatch):
        monkeypatch.setattr(settings, "SMSGATE_USERNAME", "u", raising=False)
        monkeypatch.setattr(settings, "SMSGATE_PASSWORD", "p", raising=False)
        monkeypatch.setattr(settings, "DMOBILI_USERNAME", None, raising=False)
        monkeypatch.setattr(settings, "DMOBILI_PASSWORD", None, raising=False)
        monkeypatch.setattr(settings, "DMOBILI_API_TOKEN", None, raising=False)

        r = await client.get("/api/v1/settings/gateways")
        assert r.status_code == 200
        data = r.json()
        assert data["active"] == "smsgate"
        assert set(data["gateways"].keys()) == {"smsgate", "dmobili"}
        assert data["gateways"]["smsgate"]["configured"] is True
        assert data["gateways"]["dmobili"]["configured"] is False
        # Passwords never leave the backend in the clear.
        assert data["gateways"]["smsgate"]["password"] != "p"


class TestToggle:
    @pytest.mark.asyncio
    async def test_switch_requires_configuration(self, client, monkeypatch):
        monkeypatch.setattr(settings, "DMOBILI_USERNAME", None, raising=False)
        monkeypatch.setattr(settings, "DMOBILI_PASSWORD", None, raising=False)
        monkeypatch.setattr(settings, "DMOBILI_API_TOKEN", None, raising=False)

        r = await client.put("/api/v1/settings/gateways/active", params={"provider": "dmobili"})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_switch_and_back(self, client, monkeypatch):
        monkeypatch.setattr(settings, "DMOBILI_USERNAME", "u", raising=False)
        monkeypatch.setattr(settings, "DMOBILI_PASSWORD", "p", raising=False)
        monkeypatch.setattr(settings, "DMOBILI_API_TOKEN", None, raising=False)
        monkeypatch.setattr(settings, "DMOBILI_BASE_URL", "https://dmobili.example", raising=False)
        monkeypatch.setattr(settings, "SMSGATE_USERNAME", "u", raising=False)
        monkeypatch.setattr(settings, "SMSGATE_PASSWORD", "p", raising=False)

        r = await client.put("/api/v1/settings/gateways/active", params={"provider": "dmobili"})
        assert r.status_code == 200
        assert r.json()["active"] == "dmobili"

        r = await client.get("/api/v1/settings/gateways")
        assert r.json()["active"] == "dmobili"

        r = await client.put("/api/v1/settings/gateways/active", params={"provider": "smsgate"})
        assert r.status_code == 200
        assert r.json()["active"] == "smsgate"

    @pytest.mark.asyncio
    async def test_unknown_provider_400(self, client):
        r = await client.put("/api/v1/settings/gateways/active", params={"provider": "smoke-signals"})
        assert r.status_code == 400
