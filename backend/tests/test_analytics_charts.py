"""Analytics chart endpoints: timeseries, funnel, by-campaign, calls."""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.call import CallLog
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.conversation import Conversation, Message


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
async def auth_client(db):
    from app.main import app
    from app.security.auth import get_current_user
    from app.models.user import User

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="tester", email="t@example.com", password_hash="x",
        role="admin", is_active=True,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def seed(db):
    now = datetime.now(timezone.utc)
    c = Contact(phone_number="+2348031234567", country="Nigeria")
    db.add(c)
    await db.flush()
    conv = Conversation(contact_id=c.id, status="active")
    db.add(conv)
    await db.flush()
    camp = Campaign(name="Chart Camp", status="completed")
    db.add(camp)
    await db.flush()
    db.add_all([
        Message(conversation_id=conv.id, contact_id=c.id, campaign_id=camp.id,
                direction="outgoing", body="hi", status="delivered",
                idempotency_key="k1", created_at=now - timedelta(days=1)),
        Message(conversation_id=conv.id, contact_id=c.id, campaign_id=camp.id,
                direction="outgoing", body="hi", status="failed",
                last_error="No credit", idempotency_key="k2",
                created_at=now - timedelta(days=1)),
        Message(conversation_id=conv.id, contact_id=c.id,
                direction="incoming", body="YES", status="received",
                idempotency_key="k3", created_at=now),
    ])
    db.add(CallLog(phone_number="+2348031234567", contact_id=c.id,
                   direction="outgoing", status="ended", duration_seconds=42))
    await db.flush()
    return {"campaign_id": camp.id}


class TestChartEndpoints:
    @pytest.mark.asyncio
    async def test_timeseries(self, auth_client, seed):
        r = await auth_client.get("/api/v1/analytics/timeseries", params={"days": 30})
        assert r.status_code == 200, r.text
        pts = r.json()["points"]
        assert len(pts) >= 1
        total_sent = sum(p["sent"] for p in pts)
        total_failed = sum(p["failed"] for p in pts)
        total_replies = sum(p["replies"] for p in pts)
        assert total_sent == 2
        assert total_failed == 1
        assert total_replies == 1

    @pytest.mark.asyncio
    async def test_funnel(self, auth_client, seed):
        r = await auth_client.get("/api/v1/analytics/funnel", params={"days": 30})
        assert r.status_code == 200, r.text
        d = r.json()
        stages = {s["stage"]: s["count"] for s in d["stages"]}
        assert stages["Sent"] == 2
        assert stages["Delivered"] == 1
        assert stages["Replied"] == 1
        assert d["failed"] == 1
        assert any("credit" in x["reason"] for x in d["failure_reasons"])

    @pytest.mark.asyncio
    async def test_by_campaign(self, auth_client, seed):
        r = await auth_client.get("/api/v1/analytics/by-campaign", params={"days": 30})
        assert r.status_code == 200, r.text
        items = r.json()["campaigns"]
        assert len(items) == 1
        assert items[0]["campaign_name"] == "Chart Camp"
        assert items[0]["sent"] == 2
        assert items[0]["delivered"] == 1

    @pytest.mark.asyncio
    async def test_calls(self, auth_client, seed):
        r = await auth_client.get("/api/v1/analytics/calls", params={"days": 30})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["total"] == 1
        assert d["connected"] == 1
        assert d["connect_rate"] == 100.0

    @pytest.mark.asyncio
    async def test_empty_db_returns_zeros(self, auth_client, db):
        for p in ("timeseries", "funnel", "by-campaign", "calls"):
            r = await auth_client.get(f"/api/v1/analytics/{p}", params={"days": 7})
            assert r.status_code == 200, (p, r.text)
