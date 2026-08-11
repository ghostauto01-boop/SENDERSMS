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
