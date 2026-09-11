"""Inbuilt notifications: centre API, push subscriptions, fanout."""

import json
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.asyncio
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.notification import NotificationEvent


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


SUB = {"endpoint": "https://fcm.example.com/send/sub123",
       "keys": {"p256dh": "p256dh-key", "auth": "auth-key"},
       "user_agent": "pytest"}


async def test_vapid_key_is_stable_and_valid(client, db):
    r1 = await client.get("/api/v1/notifications/push/vapid-key")
    r2 = await client.get("/api/v1/notifications/push/vapid-key")
    assert r1.status_code == 200
    k1, k2 = r1.json()["public_key"], r2.json()["public_key"]
    assert k1 == k2 and len(k1) == 87  # stable across calls, valid P-256


async def test_subscribe_list_unsubscribe(client, db):
    r = await client.post("/api/v1/notifications/push/subscribe", json=SUB)
    assert r.status_code == 200 and r.json()["devices"] == 1
    # Re-subscribing the same endpoint updates, not duplicates.
    r = await client.post("/api/v1/notifications/push/subscribe", json=SUB)
    assert r.json()["devices"] == 1
    r = await client.get("/api/v1/notifications/push/subscriptions")
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["endpoint_host"] == "fcm.example.com"
    r = await client.post("/api/v1/notifications/push/unsubscribe", json=SUB)
    assert r.json()["success"] is True
    r = await client.get("/api/v1/notifications/push/subscriptions")
    assert r.json()["total"] == 0


async def test_subscribe_rejects_garbage(client):
    bad = dict(SUB, endpoint="http://insecure/x")
    assert (await client.post("/api/v1/notifications/push/subscribe", json=bad)).status_code == 400
    bad = {"endpoint": "https://x/y", "keys": {"p256dh": "", "auth": ""}}
    assert (await client.post("/api/v1/notifications/push/subscribe", json=bad)).status_code == 400


async def test_centre_list_read_flow(client, db):
    db.add(NotificationEvent(event_type="new_reply", provider="browser",
                             title="t1", body="b1", status="sent", is_read=False))
    db.add(NotificationEvent(event_type="new_reply", provider="browser",
                             title="t2", body="b2", status="sent", is_read=False))
    await db.flush()
    r = await client.get("/api/v1/notifications/unread-count")
    assert r.json()["unread"] == 2
    r = await client.get("/api/v1/notifications/?unread_only=true")
    assert r.json()["total"] == 2
    first = r.json()["items"][0]["id"]
    assert (await client.post(f"/api/v1/notifications/{first}/read")).status_code == 200
    assert (await client.get("/api/v1/notifications/unread-count")).json()["unread"] == 1
    await client.post("/api/v1/notifications/read-all")
    assert (await client.get("/api/v1/notifications/unread-count")).json()["unread"] == 0
    assert (await client.post("/api/v1/notifications/999/read")).status_code == 404


async def test_push_test_endpoint_without_devices(client, db):
    r = await client.post("/api/v1/notifications/push/test")
    assert r.status_code == 200
    body = r.json()
    assert body["devices"] == 0 and "Enable notifications" in body["note"]
    n = (await db.execute(select(func.count(NotificationEvent.id)))).scalar()
    assert n == 1  # still recorded in-app


async def test_notify_fanout_success_and_prune(db, monkeypatch):
    from app.models.push import PushSubscription
    from app.services import push_service

    db.add(PushSubscription(endpoint="https://push.example.com/ok",
                             p256dh="a", auth="b"))
    db.add(PushSubscription(endpoint="https://push.example.com/dead",
                             p256dh="a", auth="b"))
    await db.flush()

    calls = []

    def fake_webpush(subscription_info, data, vapid_private_key, vapid_claims, timeout):
        calls.append(subscription_info["endpoint"])
        if "dead" in subscription_info["endpoint"]:
            resp = type("R", (), {"status_code": 410})()
            from pywebpush import WebPushException

            raise WebPushException("gone", response=resp)
        payload = json.loads(data)
        assert payload["title"].startswith("📱")

    import pywebpush

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    evt = await push_service.notify_inbound_sms(db, "Ada", "hello", 7)
    assert evt.status == "sent" and evt.url == "/inbox?conversation_id=7"
    remaining = list((await db.execute(select(PushSubscription))).scalars().all())
    assert [s.endpoint for s in remaining] == ["https://push.example.com/ok"]
    assert remaining[0].fail_count == 0 and remaining[0].last_used_at is not None


async def test_campaign_completion_emits_event(db, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.campaign import Campaign
    from app.tasks import campaign_tasks

    db.add(Campaign(name="Done Camp", status="running"))
    await db.commit()
    camp_id = (await db.execute(select(Campaign.id))).scalar_one()

    factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(campaign_tasks, "async_session_factory", factory)

    assert await campaign_tasks.process_campaign_batch_async(camp_id) == 0
    async with factory() as check:
        camp = (await check.execute(select(Campaign).where(Campaign.id == camp_id))).scalar_one()
        assert camp.status == "completed"
        evt = (await check.execute(
            select(NotificationEvent).where(NotificationEvent.provider == "browser"))).scalar_one()
        assert evt.event_type == "campaign_completed" and "Done Camp" in evt.title
        assert evt.is_read is False


async def test_notify_fanout_retry_then_prune_after_max_fails(db, monkeypatch):
    from app.models.push import PushSubscription
    from app.services import push_service

    db.add(PushSubscription(endpoint="https://push.example.com/flaky",
                             p256dh="a", auth="b"))
    await db.flush()

    import pywebpush

    def boom(*a, **k):
        raise pywebpush.WebPushException("timeout")

    monkeypatch.setattr(pywebpush, "webpush", boom)
    for _ in range(push_service.MAX_FAILS):
        evt = await push_service.notify_inbound_sms(db, "Ada", "hi", None)
    assert evt.status == "failed"
    left = (await db.execute(select(func.count(PushSubscription.id)))).scalar()
    assert left == 0  # pruned after MAX_FAILS consecutive failures
