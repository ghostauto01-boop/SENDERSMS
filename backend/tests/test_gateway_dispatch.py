"""Tests for the active-gateway dispatcher.

The dispatcher is what makes the second gateway a toggle instead of a fork:
every send path asks it which provider is live, and it must never silently
route traffic through a gateway the operator did not select.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base
from app.services.gateway_dispatch import (
    get_active_gateway,
    poll_status_dispatch,
    send_sms_dispatch,
    set_active_gateway,
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


class TestActiveGateway:
    @pytest.mark.asyncio
    async def test_defaults_to_smsgate(self, db):
        assert await get_active_gateway(db) == "smsgate"

    @pytest.mark.asyncio
    async def test_roundtrip(self, db):
        await set_active_gateway(db, "dmobili")
        await db.flush()
        assert await get_active_gateway(db) == "dmobili"
        await set_active_gateway(db, "smsgate")
        await db.flush()
        assert await get_active_gateway(db) == "smsgate"

    @pytest.mark.asyncio
    async def test_unknown_provider_rejected(self, db):
        with pytest.raises(ValueError):
            await set_active_gateway(db, "carrierpigeon")


class TestSendDispatch:
    @pytest.mark.asyncio
    async def test_default_routes_to_smsgate(self, db, monkeypatch):
        calls = {}

        async def fake_smsgate(phone, body, sim=1):
            calls["smsgate"] = (phone, body, sim)
            return {"success": True, "provider_message_id": "sg-1", "status": "sent"}

        async def fake_dmobili(phone, body, sender_id=None):
            calls["dmobili"] = phone
            return {"success": True, "provider_message_id": "dm-1", "status": "sent"}

        monkeypatch.setattr("app.providers.smsgate.send_sms_direct", fake_smsgate)
        monkeypatch.setattr("app.providers.dmobili.send_sms_direct", fake_dmobili)

        provider, r = await send_sms_dispatch(db, "+2348031234567", "hi", 2)
        assert provider == "smsgate"
        assert r["success"] is True
        assert "smsgate" in calls and "dmobili" not in calls
        assert calls["smsgate"][2] == 2  # SIM slot forwarded

    @pytest.mark.asyncio
    async def test_dmobili_active_routes_to_dmobili(self, db, monkeypatch):
        monkeypatch.setattr(settings, "DMOBILI_USERNAME", "u", raising=False)
        monkeypatch.setattr(settings, "DMOBILI_PASSWORD", "p", raising=False)
        monkeypatch.setattr(settings, "DMOBILI_API_TOKEN", None, raising=False)
        monkeypatch.setattr(settings, "DMOBILI_BASE_URL", "https://dmobili.example", raising=False)
        await set_active_gateway(db, "dmobili")
        await db.flush()

        calls = {}

        async def fake_smsgate(phone, body, sim=1):
            calls["smsgate"] = phone
            return {"success": True, "provider_message_id": "sg-1", "status": "sent"}

        async def fake_dmobili(phone, body, sender_id=None):
            calls["dmobili"] = (phone, body)
            return {"success": True, "provider_message_id": "dm-1", "status": "sent"}

        monkeypatch.setattr("app.providers.smsgate.send_sms_direct", fake_smsgate)
        monkeypatch.setattr("app.providers.dmobili.send_sms_direct", fake_dmobili)

        provider, r = await send_sms_dispatch(db, "+2348031234567", "hi", 1)
        assert provider == "dmobili"
        assert r["provider_message_id"] == "dm-1"
        assert "dmobili" in calls and "smsgate" not in calls

    @pytest.mark.asyncio
    async def test_selected_but_unconfigured_dmobili_fails_loudly(self, db, monkeypatch):
        """Never silently fall back to the other gateway: that changes sender
        IDs and costs. The send must fail with an actionable error."""
        monkeypatch.setattr(settings, "DMOBILI_USERNAME", None, raising=False)
        monkeypatch.setattr(settings, "DMOBILI_PASSWORD", None, raising=False)
        monkeypatch.setattr(settings, "DMOBILI_API_TOKEN", None, raising=False)
        await set_active_gateway(db, "dmobili")
        await db.flush()

        async def fake_smsgate(phone, body, sim=1):
            raise AssertionError("must not fall back to smsgate")

        monkeypatch.setattr("app.providers.smsgate.send_sms_direct", fake_smsgate)

        provider, r = await send_sms_dispatch(db, "+2348031234567", "hi")
        assert provider == "dmobili"
        assert r["success"] is False
        assert "credentials" in r["error"].lower()


class TestPollDispatch:
    @pytest.mark.asyncio
    async def test_groups_by_provider(self, db, monkeypatch):
        seen = {}

        async def fake_smsgate(ids):
            seen["smsgate"] = ids
            return [{"provider_message_id": i, "status": "delivered"} for i in ids]

        async def fake_dmobili(ids):
            seen["dmobili"] = ids
            return [{"provider_message_id": i, "status": "sent"} for i in ids]

        monkeypatch.setattr("app.providers.smsgate.poll_status_for_ids", fake_smsgate)
        monkeypatch.setattr("app.providers.dmobili.poll_status_for_ids", fake_dmobili)

        out = await poll_status_dispatch(db, {"smsgate": ["a"], "dmobili": ["b", "c"]})
        assert seen["smsgate"] == ["a"]
        assert seen["dmobili"] == ["b", "c"]
        assert len(out) == 3

    @pytest.mark.asyncio
    async def test_empty_groups_skipped(self, db, monkeypatch):
        async def boom(ids):
            raise AssertionError("must not be called for empty group")

        monkeypatch.setattr("app.providers.smsgate.poll_status_for_ids", boom)
        monkeypatch.setattr("app.providers.dmobili.poll_status_for_ids", boom)
        assert await poll_status_dispatch(db, {}) == []
