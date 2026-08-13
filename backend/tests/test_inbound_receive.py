"""End-to-end cover for the receive path: webhook -> conversation -> chat.

These are the regressions that made incoming SMS never appear in the chat:

1. Webhooks were registered as {"url", "events": [...]}. The SMS-Gate API takes
   ONE event per webhook under a singular "event" key, so nothing was ever
   delivered. https://docs.sms-gate.app/features/webhooks/
2. Inbound senders were gated through the Nigerian-number normaliser, which
   discarded short codes, alphanumeric sender IDs and foreign numbers.
3. messageId is derived from message CONTENT, so a repeated reply ("YES")
   reused an existing id and was suppressed as a duplicate.
4. Delivery-status events matched incoming rows and regressed statuses
   (a late sms:sent overwrote delivered; sms:delivered fires once per part).
"""

import asyncio
import hashlib
import hmac
import json
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.services.sms_service import SMSService

SECRET = "test-signing-key"


def sign(body: bytes, ts: str) -> str:
    return hmac.new(SECRET.encode(), body + ts.encode(), hashlib.sha256).hexdigest()


def envelope(event: str, payload: dict, evt_id: str) -> dict:
    return {"deviceId": "dev-1", "event": event, "id": evt_id,
            "webhookId": "wh-1", "payload": payload}


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
async def client(db, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "SMSGATE_WEBHOOK_SECRET", SECRET, raising=False)
    monkeypatch.setattr(settings, "SMSGATE_WEBHOOK_ALLOW_UNSIGNED", False, raising=False)

    from app.main import app
    app.dependency_overrides[get_db] = lambda: db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_client(client):
    """`client` with authentication bypassed, for routes behind get_current_user."""
    from app.main import app
    from app.security.auth import get_current_user
    from app.models.user import User

    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="tester", email="t@example.com", password_hash="x",
        role="admin", is_active=True,
    )
    yield client
    app.dependency_overrides.pop(get_current_user, None)


async def post_webhook(client, body: dict):
    raw = json.dumps(body).encode()
    ts = str(int(time.time()))
    return await client.post(
        "/api/v1/webhooks/smsgateway",
        content=raw,
        headers={"Content-Type": "application/json",
                 "X-Signature": sign(raw, ts), "X-Timestamp": ts},
    )


class TestInboundReachesChat:
    @pytest.mark.asyncio
    async def test_sms_received_creates_conversation(self, client, db):
        r = await post_webhook(client, envelope("sms:received", {
            "messageId": "m1", "message": "Hello there",
            "sender": "+2348012345678", "recipient": "+2349099999999",
            "simNumber": 1, "receivedAt": "2024-06-22T15:46:11.000+07:00",
        }, "evt-1"))
        assert r.status_code == 200

        msg = (await db.execute(
            select(Message).where(Message.direction == "incoming")
        )).scalar_one()
        assert msg.body == "Hello there"
        assert msg.provider_message_id == "m1"
        # Real receive time is preserved so replayed history sorts correctly.
        assert msg.created_at.year == 2024

        conv = (await db.execute(select(Conversation))).scalar_one()
        assert conv.status == "unread"
        assert conv.unread_count == 1
        assert conv.message_count == 1
        assert conv.last_message_preview == "Hello there"

    @pytest.mark.asyncio
    async def test_shortcode_and_alphanumeric_senders_are_kept(self, client, db):
        """Regression: these used to be dropped by the Nigerian normaliser."""
        for i, sender in enumerate(["32665", "MTN", "+14155550123"]):
            r = await post_webhook(client, envelope("sms:received", {
                "messageId": f"m{i}", "message": f"from {sender}",
                "sender": sender, "receivedAt": f"2024-06-22T15:4{i}:11Z",
            }, f"evt-s{i}"))
            assert r.status_code == 200

        count = len((await db.execute(
            select(Message).where(Message.direction == "incoming")
        )).scalars().all())
        assert count == 3

    @pytest.mark.asyncio
    async def test_repeated_reply_text_is_not_swallowed(self, client, db):
        """messageId is content-derived, so two "YES" replies share an id."""
        for i, ts in enumerate(["2024-06-22T15:46:11Z", "2024-06-22T16:10:00Z"]):
            r = await post_webhook(client, envelope("sms:received", {
                "messageId": "same-content-id", "message": "YES",
                "sender": "+2348012345678", "receivedAt": ts,
            }, f"evt-y{i}"))
            assert r.status_code == 200

        msgs = (await db.execute(
            select(Message).where(Message.direction == "incoming")
        )).scalars().all()
        assert len(msgs) == 2

    @pytest.mark.asyncio
    async def test_redelivery_of_same_envelope_is_deduped(self, client, db):
        """The device retries for ~2 days; the same envelope id must not double-post."""
        body = envelope("sms:received", {
            "messageId": "m9", "message": "Only once",
            "sender": "+2348012345678", "receivedAt": "2024-06-22T15:46:11Z",
        }, "evt-dup")
        assert (await post_webhook(client, body)).status_code == 200
        assert (await post_webhook(client, body)).status_code == 200

        msgs = (await db.execute(
            select(Message).where(Message.direction == "incoming")
        )).scalars().all()
        assert len(msgs) == 1

    @pytest.mark.asyncio
    async def test_second_message_appends_to_same_thread(self, client, db):
        for i in range(2):
            await post_webhook(client, envelope("sms:received", {
                "messageId": f"t{i}", "message": f"msg {i}",
                "sender": "+2348012345678", "receivedAt": f"2024-06-22T1{i}:00:00Z",
            }, f"evt-t{i}"))

        convs = (await db.execute(select(Conversation))).scalars().all()
        assert len(convs) == 1
        assert convs[0].message_count == 2
        assert convs[0].unread_count == 2
        contacts = (await db.execute(select(Contact))).scalars().all()
        assert len(contacts) == 1

    @pytest.mark.asyncio
    async def test_out_of_order_replay_keeps_newest_preview(self, client, db):
        """Inbox export replays history out of order; the thread must not rewind."""
        await post_webhook(client, envelope("sms:received", {
            "messageId": "new", "message": "newest message",
            "sender": "+2348012345678", "receivedAt": "2026-08-11T09:45:00Z",
        }, "evt-o1"))
        await post_webhook(client, envelope("sms:received", {
            "messageId": "old", "message": "sent last week",
            "sender": "+2348012345678", "receivedAt": "2026-08-01T09:00:00Z",
        }, "evt-o2"))

        conv = (await db.execute(select(Conversation))).scalar_one()
        assert conv.last_message_preview == "newest message"
        assert conv.last_message_at.day == 11

    @pytest.mark.asyncio
    async def test_naive_stored_timestamp_does_not_crash(self, client, db):
        """SQLite/legacy rows return naive datetimes; comparing them used to raise."""
        from datetime import datetime as _dt
        await post_webhook(client, envelope("sms:received", {
            "messageId": "n1", "message": "first",
            "sender": "+2348012345678", "receivedAt": "2026-08-11T09:00:00Z",
        }, "evt-n1"))
        conv = (await db.execute(select(Conversation))).scalar_one()
        conv.last_message_at = _dt(2026, 8, 11, 9, 0, 0)  # naive, as SQLite returns
        await db.flush()

        r = await post_webhook(client, envelope("sms:received", {
            "messageId": "n2", "message": "second",
            "sender": "+2348012345678", "receivedAt": "2026-08-11T10:00:00Z",
        }, "evt-n2"))
        assert r.status_code == 200
        assert r.json().get("stored") is True

    @pytest.mark.asyncio
    async def test_unknown_and_system_events_return_2xx(self, client):
        """Non-2xx triggers ~14 retries over 2 days. Never do that."""
        for ev, payload in [("system:ping", {}), ("app:started", {"simCards": []}),
                            ("some:future-event", {"foo": "bar"})]:
            r = await post_webhook(client, envelope(ev, payload, f"evt-{ev}"))
            assert r.status_code == 200, ev

    @pytest.mark.asyncio
    async def test_malformed_payload_still_returns_2xx(self, client):
        r = await post_webhook(client, envelope("sms:received", {"nonsense": True}, "evt-bad"))
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_forged_webhook_still_rejected(self, client, db):
        raw = json.dumps(envelope("sms:received", {
            "messageId": "evil", "message": "INJECTED", "sender": "+2348012345678",
        }, "evt-evil")).encode()
        ts = str(int(time.time()))
        r = await client.post(
            "/api/v1/webhooks/smsgateway", content=raw,
            headers={"Content-Type": "application/json",
                     "X-Signature": "deadbeef", "X-Timestamp": ts},
        )
        assert r.status_code == 401
        assert (await db.execute(select(Message))).scalars().first() is None


class TestDeliveryStatus:
    async def _outgoing(self, db, pid="out-1"):
        c = Contact(phone_number="+2348012345678", country="Nigeria", lead_status="new")
        db.add(c)
        await db.flush()
        conv = Conversation(contact_id=c.id, status="active")
        db.add(conv)
        await db.flush()
        m = Message(conversation_id=conv.id, contact_id=c.id, direction="outgoing",
                    body="hi", status="sent", provider="smsgate",
                    provider_message_id=pid, idempotency_key=f"k-{pid}")
        db.add(m)
        await db.flush()
        return m

    @pytest.mark.asyncio
    async def test_delivered_updates_outgoing(self, client, db):
        m = await self._outgoing(db)
        r = await post_webhook(client, envelope("sms:delivered", {
            "messageId": "out-1", "deliveredAt": "2024-06-22T15:46:11Z",
        }, "evt-d1"))
        assert r.status_code == 200
        await db.refresh(m)
        assert m.status == "delivered"
        assert m.delivered_at is not None

    @pytest.mark.asyncio
    async def test_late_sent_does_not_regress_delivered(self, client, db):
        """sms:sent can arrive after sms:delivered; status must not go backwards."""
        m = await self._outgoing(db)
        await post_webhook(client, envelope("sms:delivered", {"messageId": "out-1"}, "e1"))
        await post_webhook(client, envelope("sms:sent", {"messageId": "out-1", "partsCount": 2}, "e2"))
        await db.refresh(m)
        assert m.status == "delivered"

    @pytest.mark.asyncio
    async def test_per_part_delivered_is_idempotent(self, client, db):
        """sms:delivered fires once PER PART of a multipart message."""
        m = await self._outgoing(db)
        for i in range(3):
            await post_webhook(client, envelope("sms:delivered", {"messageId": "out-1"}, f"e-p{i}"))
        await db.refresh(m)
        assert m.status == "delivered"

    @pytest.mark.asyncio
    async def test_per_part_delivered_keeps_first_timestamp(self, client, db):
        """Each part re-fires sms:delivered; delivered_at must not creep to now()."""
        m = await self._outgoing(db)
        await post_webhook(client, envelope("sms:delivered", {
            "messageId": "out-1", "deliveredAt": "2024-06-22T15:46:11Z",
        }, "e-t1"))
        await db.refresh(m)
        first = m.delivered_at
        # a later part arrives without a timestamp
        await post_webhook(client, envelope("sms:delivered", {"messageId": "out-1"}, "e-t2"))
        await db.refresh(m)
        assert m.delivered_at == first

    @pytest.mark.asyncio
    async def test_failed_records_reason(self, client, db):
        m = await self._outgoing(db)
        await post_webhook(client, envelope("sms:failed", {
            "messageId": "out-1", "reason": "No service", "failedAt": "2024-06-22T15:46:11Z",
        }, "evt-f1"))
        await db.refresh(m)
        assert m.status == "failed"
        assert m.last_error == "No service"
        assert m.failed_at is not None

    @pytest.mark.asyncio
    async def test_status_never_matches_incoming_message(self, db):
        """An inbound row sharing a provider id must not be flipped by a status event."""
        svc = SMSService(db)
        await svc.process_inbound_message(
            "+2348012345678", "hello",
            {"messageId": "shared-id", "receivedAt": "2024-06-22T15:46:11Z"},
        )
        inbound = (await db.execute(
            select(Message).where(Message.direction == "incoming")
        )).scalar_one()
        assert await svc.process_delivery_status("shared-id", "failed") is None
        await db.refresh(inbound)
        assert inbound.status == "delivered"


class TestWebhookRegistration:
    @pytest.mark.asyncio
    async def test_registers_one_webhook_per_event_singular_key(self, monkeypatch):
        """The API rejects an `events` array; it takes a singular `event`."""
        import app.providers.smsgate as sg

        monkeypatch.setattr(sg.settings, "SMSGATE_USERNAME", "u", raising=False)
        monkeypatch.setattr(sg.settings, "SMSGATE_PASSWORD", "p", raising=False)

        posted = []

        class FakeResponse:
            status_code = 200
            text = "[]"

            def json(self):
                return []

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, **kw):
                return FakeResponse()

            async def post(self, url, **kw):
                posted.append(kw.get("json"))
                return FakeResponse()

            async def delete(self, url, **kw):
                return FakeResponse()

        monkeypatch.setattr(sg.httpx, "AsyncClient", lambda **kw: FakeClient())

        result = await sg.register_webhook_direct("https://example.com/api/v1/webhooks/smsgateway")

        assert posted, "no webhook registration was attempted"
        for body in posted:
            assert "event" in body, "must use the singular `event` key"
            assert "events" not in body, "an `events` array is silently ignored by the API"
            assert isinstance(body["event"], str)
        assert {b["event"] for b in posted} == set(sg.DEFAULT_EVENTS)
        assert result["success"] is True


class TestOutboundReply:
    """The chat's reply box must report the true send outcome."""

    async def _conv(self, db, phone="+2348012345678", opted_out=False):
        c = Contact(phone_number=phone, country="Nigeria", lead_status="new",
                    is_opted_out=opted_out)
        db.add(c)
        await db.flush()
        conv = Conversation(contact_id=c.id, status="active")
        db.add(conv)
        await db.flush()
        return conv

    @pytest.mark.asyncio
    async def test_reply_success_records_provider_id(self, auth_client, db, monkeypatch):
        conv = await self._conv(db)
        import app.providers.smsgate as sg

        async def fake_send(phone, body, sim=1):
            return {"success": True, "provider_message_id": "srv-1", "status": "Pending"}

        monkeypatch.setattr(sg, "send_sms_direct", fake_send)
        r = await auth_client.post(f"/api/v1/inbox/conversations/{conv.id}/reply",
                              params={"body": "hello there"})
        assert r.status_code == 200
        assert r.json()["success"] is True
        assert r.json()["provider_message_id"] == "srv-1"
        m = (await db.execute(select(Message).where(
            Message.direction == "outgoing"))).scalar_one()
        assert m.status == "sent"

    @pytest.mark.asyncio
    async def test_reply_reports_gateway_failure(self, auth_client, db, monkeypatch):
        """A gateway rejection must NOT be reported to the UI as a success."""
        conv = await self._conv(db)
        import app.providers.smsgate as sg

        async def fake_send(phone, body, sim=1):
            return {"success": False, "error": "No SIM available"}

        monkeypatch.setattr(sg, "send_sms_direct", fake_send)
        r = await auth_client.post(f"/api/v1/inbox/conversations/{conv.id}/reply",
                              params={"body": "hello there"})
        assert r.status_code == 200
        payload = r.json()
        assert payload["success"] is False
        assert payload["status"] == "failed"
        assert "No SIM" in payload["error"]

    @pytest.mark.asyncio
    async def test_reply_to_opted_out_contact_is_rejected(self, auth_client, db):
        conv = await self._conv(db, opted_out=True)
        r = await auth_client.post(f"/api/v1/inbox/conversations/{conv.id}/reply",
                              params={"body": "hello there"})
        assert r.status_code == 409
        assert "opted out" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_reply_to_missing_conversation_is_404(self, auth_client, db):
        r = await auth_client.post("/api/v1/inbox/conversations/99999/reply",
                              params={"body": "hi"})
        assert r.status_code == 404


class TestChatListAndLabels:
    """The inbox list/labels regressions found by driving the live harness.

    1. Opening a thread reset conversation.status to "read" unconditionally,
       so Interested / Not interested / Closed were destroyed the instant the
       user clicked the thread, and Mark unread was undone by the next poll.
    2. The UI's default tab sends status="all", which was treated as a literal
       status and matched nothing.
    3. The Unread tab missed threads that had unread_count > 0 but a status
       left over from an earlier label.
    """

    async def _conv(self, db, phone, status="unread", unread=1, preview="hi"):
        c = Contact(phone_number=phone, country="Nigeria", lead_status="new")
        db.add(c)
        await db.flush()
        conv = Conversation(contact_id=c.id, status=status, unread_count=unread,
                            last_message_preview=preview)
        db.add(conv)
        await db.flush()
        return conv

    @pytest.mark.asyncio
    async def test_opening_thread_keeps_label_but_clears_badge(self, auth_client, db):
        conv = await self._conv(db, "+2348011110001")
        await auth_client.post(f"/api/v1/inbox/conversations/{conv.id}/mark-interested")

        # Opening it twice must not degrade the label back to "read".
        for _ in range(2):
            r = await auth_client.get(f"/api/v1/inbox/conversations/{conv.id}")
            assert r.status_code == 200
            assert r.json()["status"] == "interested"

        await db.refresh(conv)
        assert conv.status == "interested"
        assert conv.unread_count == 0  # badge still cleared

    @pytest.mark.asyncio
    async def test_plain_unread_thread_becomes_read_on_open(self, auth_client, db):
        conv = await self._conv(db, "+2348011110002", status="unread")
        r = await auth_client.get(f"/api/v1/inbox/conversations/{conv.id}")
        assert r.json()["status"] == "read"

    @pytest.mark.asyncio
    async def test_mark_unread_survives_and_shows_in_unread_tab(self, auth_client, db):
        conv = await self._conv(db, "+2348011110003", status="read", unread=0)
        await auth_client.post(f"/api/v1/inbox/conversations/{conv.id}/mark-unread")

        r = await auth_client.get("/api/v1/inbox/conversations", params={"status": "unread"})
        assert [i["id"] for i in r.json()["items"]] == [conv.id]

    @pytest.mark.asyncio
    async def test_status_all_is_not_a_literal_filter(self, auth_client, db):
        await self._conv(db, "+2348011110004", status="interested")
        await self._conv(db, "+2348011110005", status="closed")

        r = await auth_client.get("/api/v1/inbox/conversations", params={"status": "all"})
        assert r.json()["total"] == 2

    @pytest.mark.asyncio
    async def test_unread_tab_catches_badge_without_unread_status(self, auth_client, db):
        # Labelled "interested" but still carrying unread messages.
        conv = await self._conv(db, "+2348011110006", status="interested", unread=3)
        r = await auth_client.get("/api/v1/inbox/conversations", params={"status": "unread"})
        assert conv.id in [i["id"] for i in r.json()["items"]]

    @pytest.mark.asyncio
    async def test_search_matches_phone_and_preview(self, auth_client, db):
        await self._conv(db, "+2348011119999", preview="iPhone in Wuse")
        await self._conv(db, "+2349087776666", preview="Samsung price?")

        for term, expected in [("Wuse", 1), ("Samsung", 1), ("801111", 1), ("nomatch", 0)]:
            r = await auth_client.get("/api/v1/inbox/conversations",
                                      params={"status": "all", "search": term})
            assert r.json()["total"] == expected, term

    @pytest.mark.asyncio
    async def test_thread_exposes_failure_reason(self, auth_client, db):
        """A failed send must tell the chat WHY, not just show a dot."""
        conv = await self._conv(db, "+2348011110007")
        db.add(Message(conversation_id=conv.id, contact_id=conv.contact_id,
                       direction="outgoing", body="x", segment_count=1, char_count=1,
                       status="failed", last_error="SIM has no credit",
                       idempotency_key="k-fail-1", retry_count=0))
        await db.flush()

        r = await auth_client.get(f"/api/v1/inbox/conversations/{conv.id}")
        m = r.json()["messages"][-1]
        assert m["status"] == "failed"
        assert m["last_error"] == "SIM has no credit"
        assert m["segment_count"] == 1


class TestInboundNotifications:
    """Pushover alerts for inbound SMS.

    The notification is dispatched from inside the webhook request. SMS-Gate
    requires a 2xx within 30 seconds or it retries for ~2 days, so a slow or
    blackholed Pushover must never delay the response: measured at 15.3s before
    the fix, 0.38s after.
    """

    async def _setup_pushover(self, db, enabled=True):
        from app.models.notification import NotificationProvider
        from app.security.encryption import encrypt_value
        p = NotificationProvider(
            provider="pushover", is_enabled=enabled,
            config_json=json.dumps({
                "user_key_encrypted": encrypt_value("uk-test"),
                "app_token_encrypted": encrypt_value("at-test"),
            }),
        )
        db.add(p)
        await db.flush()
        return p

    @pytest.mark.asyncio
    async def test_inbound_triggers_pushover(self, client, db, monkeypatch):
        await self._setup_pushover(db)
        sent = []

        import app.providers.pushover as po

        async def fake_send(self, title, body, **kw):
            sent.append((title, body))
            return True

        monkeypatch.setattr(po.PushoverProvider, "send_notification", fake_send)

        r = await post_webhook(client, envelope("sms:received", {
            "messageId": "in-n1", "message": "notify me", "sender": "+2348012345678",
            "recipient": "+2340000000000", "simNumber": 1,
            "receivedAt": "2026-08-11T09:00:00.000Z"}, "evt-n1"))
        assert r.status_code == 200

        for _ in range(20):          # dispatched as a background task
            if sent:
                break
            await asyncio.sleep(0.05)

        assert len(sent) == 1
        assert "+2348012345678" in sent[0][0]
        assert sent[0][1] == "notify me"

    @pytest.mark.asyncio
    async def test_slow_pushover_does_not_delay_webhook(self, client, db, monkeypatch):
        """The whole point: a hanging Pushover must not risk SMS-Gate retries."""
        await self._setup_pushover(db)

        import app.providers.pushover as po

        async def hanging_send(self, title, body, **kw):
            await asyncio.sleep(30)
            return True

        monkeypatch.setattr(po.PushoverProvider, "send_notification", hanging_send)

        start = time.monotonic()
        r = await post_webhook(client, envelope("sms:received", {
            "messageId": "in-n2", "message": "slow notify", "sender": "+2348012345679",
            "recipient": "+2340000000000", "simNumber": 1,
            "receivedAt": "2026-08-11T09:01:00.000Z"}, "evt-n2"))
        elapsed = time.monotonic() - start

        assert r.status_code == 200
        assert elapsed < 5, f"webhook blocked for {elapsed:.1f}s on a slow Pushover"

        msg = (await db.execute(select(Message).where(
            Message.body == "slow notify"))).scalar_one_or_none()
        assert msg is not None  # stored regardless

    @pytest.mark.asyncio
    async def test_pushover_failure_never_loses_the_message(self, client, db, monkeypatch):
        await self._setup_pushover(db)

        import app.providers.pushover as po

        async def boom(self, title, body, **kw):
            raise RuntimeError("pushover down")

        monkeypatch.setattr(po.PushoverProvider, "send_notification", boom)

        r = await post_webhook(client, envelope("sms:received", {
            "messageId": "in-n3", "message": "still stored", "sender": "+2348012345680",
            "recipient": "+2340000000000", "simNumber": 1,
            "receivedAt": "2026-08-11T09:02:00.000Z"}, "evt-n3"))
        assert r.status_code == 200
        await asyncio.sleep(0.2)

        msg = (await db.execute(select(Message).where(
            Message.body == "still stored"))).scalar_one_or_none()
        assert msg is not None

    @pytest.mark.asyncio
    async def test_duplicate_delivery_does_not_double_notify(self, client, db, monkeypatch):
        await self._setup_pushover(db)
        sent = []

        import app.providers.pushover as po

        async def fake_send(self, title, body, **kw):
            sent.append(body)
            return True

        monkeypatch.setattr(po.PushoverProvider, "send_notification", fake_send)

        env = envelope("sms:received", {
            "messageId": "in-n4", "message": "only once", "sender": "+2348012345681",
            "recipient": "+2340000000000", "simNumber": 1,
            "receivedAt": "2026-08-11T09:03:00.000Z"}, "evt-n4")
        for _ in range(3):
            assert (await post_webhook(client, env)).status_code == 200
        await asyncio.sleep(0.3)

        assert sent == ["only once"]

    @pytest.mark.asyncio
    async def test_muted_sender_mtn_skips_pushover(self, client, db, monkeypatch):
        """MTN/Airtel alert short codes must not ping Pushover.

        The message is still stored in the inbox — only the notification is
        suppressed. This is what kills the constant "MTN alert" / "AIRTEL
        alert" push spam while keeping carrier conversations visible.
        """
        await self._setup_pushover(db)
        sent = []

        import app.providers.pushover as po

        async def fake_send(self, title, body, **kw):
            sent.append((title, body))
            return True

        monkeypatch.setattr(po.PushoverProvider, "send_notification", fake_send)

        for i, sender in enumerate(["MTN", "AIRTEL"]):
            r = await post_webhook(client, envelope("sms:received", {
                "messageId": f"in-muted-{i}", "message": f"{sender} alert: N500 debit",
                "sender": sender, "recipient": "+2340000000000", "simNumber": 1,
                "receivedAt": "2026-08-11T10:00:00.000Z"}, f"evt-muted-{i}"))
            assert r.status_code == 200

        await asyncio.sleep(0.3)
        assert sent == [], f"muted senders must not notify, got {sent}"

        # But the messages themselves still reached the inbox.
        msgs = (await db.execute(select(Message).where(
            Message.direction == "incoming"))).scalars().all()
        assert len(msgs) == 2

    @pytest.mark.asyncio
    async def test_muted_senders_are_configurable(self, client, db, monkeypatch):
        """Editing the muted list in Settings changes what gets muted."""
        await self._setup_pushover(db)
        from app.services.system_settings import set_muted_notify_senders
        await set_muted_notify_senders(db, ["MTN"])  # Airtel now NOT muted
        await db.flush()

        sent = []

        import app.providers.pushover as po

        async def fake_send(self, title, body, **kw):
            sent.append((title, body))
            return True

        monkeypatch.setattr(po.PushoverProvider, "send_notification", fake_send)

        await post_webhook(client, envelope("sms:received", {
            "messageId": "in-a", "message": "Airtel alert",
            "sender": "AIRTEL", "recipient": "+2340000000000", "simNumber": 1,
            "receivedAt": "2026-08-11T10:01:00.000Z"}, "evt-a"))
        await post_webhook(client, envelope("sms:received", {
            "messageId": "in-m", "message": "MTN alert",
            "sender": "MTN", "recipient": "+2340000000000", "simNumber": 1,
            "receivedAt": "2026-08-11T10:02:00.000Z"}, "evt-m"))

        for _ in range(20):
            if len(sent) >= 1:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)

        assert len(sent) == 1, f"expected only Airtel to notify, got {sent}"
        assert sent[0][1] == "Airtel alert"

    @pytest.mark.asyncio
    async def test_disabled_pushover_is_silent(self, client, db, monkeypatch):
        await self._setup_pushover(db, enabled=False)
        sent = []

        import app.providers.pushover as po

        async def fake_send(self, title, body, **kw):
            sent.append(body)
            return True

        monkeypatch.setattr(po.PushoverProvider, "send_notification", fake_send)

        await post_webhook(client, envelope("sms:received", {
            "messageId": "in-n5", "message": "no alert", "sender": "+2348012345682",
            "recipient": "+2340000000000", "simNumber": 1,
            "receivedAt": "2026-08-11T09:04:00.000Z"}, "evt-n5"))
        await asyncio.sleep(0.2)

        assert sent == []


class TestOptOut:
    """STOP must be recorded as a real consent revocation, not just a flag.

    The opt-out flag was set, but consent_status stayed "unknown" and the
    reason/timestamp were never exposed by the API - so opted-out contacts
    were indistinguishable from never-asked ones in a compliance export.
    """

    async def _seed(self, db, phone="+2348044445555"):
        c = Contact(phone_number=phone, country="Nigeria", lead_status="new")
        db.add(c)
        await db.commit()
        await db.refresh(c)
        return c

    @pytest.mark.asyncio
    @pytest.mark.parametrize("word", ["STOP", "stop", "Stop", "UNSUBSCRIBE"])
    async def test_stop_revokes_consent(self, client, db, word):
        c = await self._seed(db, f"+23480444455{len(word):02d}")
        await post_webhook(client, envelope(
            "sms:received",
            {"messageId": f"m-{word}", "message": word,
             "sender": c.phone_number, "receivedAt": "2025-01-01T10:00:00+00:00"},
            f"e-{word}"))
        await db.refresh(c)
        assert c.is_opted_out is True
        assert c.consent_status == "opted_out"
        assert c.has_consented is False
        assert c.opt_out_reason and word.upper() in c.opt_out_reason.upper()
        assert c.opted_out_at is not None

    @pytest.mark.asyncio
    async def test_ordinary_reply_does_not_opt_out(self, client, db):
        c = await self._seed(db, "+2348044446666")
        await post_webhook(client, envelope(
            "sms:received",
            {"messageId": "m-ok", "message": "Yes please, tell me more",
             "sender": c.phone_number, "receivedAt": "2025-01-01T10:00:00+00:00"},
            "e-ok"))
        await db.refresh(c)
        assert c.is_opted_out is False
        assert c.consent_status != "opted_out"
        assert c.opt_out_reason is None
