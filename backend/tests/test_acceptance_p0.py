"""Acceptance tests for the P0–P3 deliverables.

Each test maps to one line of the acceptance list the operator runs the
product against:

1. A campaign with multiple senders keeps every contact's follow-ups pinned to
   the contact's ORIGINAL sender, threaded, with a personal-looking body —
   even after the campaign's senders are changed.
2. The heartbeat layer detects a dead worker within five minutes while work is
   due, the health payload drives the UI banner, and overdue follow-ups go out
   when the worker returns ("Process now").
3. Replying from the Inbox sends from the conversation's original sender, in
   the same thread, with one "Re: "; provider failures surface for real.
4. "unsubscribe" replies and one-click links opt the contact out and cancel
   their follow-ups; the GET page never opts anyone out.
5. A real reply registers on the campaign (reply event + assignment), stops
   follow-ups and raises an alert.
6. Delivery confirmations land for every sender (webhook + Brevo event-API
   poll fallback), and messages with no event are reported as "unconfirmed".
7. The calendar endpoints work on a database migrated from the old schema.

Plus the smaller guarantees the list implies: bot engagement is never counted,
and "plain personal email" mode keeps the body looking like a real person.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.security.auth import get_current_user
from app.services import ads_service, email_service


# ---------------------------------------------------------------------------
# Fake Brevo: records every send and answers the few GETs the client makes.
# ---------------------------------------------------------------------------


class _Brevo:
    def __init__(self, posts, *, fail=False, senders=None):
        self.posts = posts
        self.fail = fail
        self.senders = senders or []
        self._n = 0

    def client(self):
        outer = self

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, *, headers=None, **kwargs):
                class Response:
                    status_code = 200
                    text = "{}"

                    @staticmethod
                    def json():
                        return {"senders": outer.senders}

                return Response()

            async def post(self, url, *, headers=None, json=None, **kwargs):
                payload = json or {}
                outer._n += 1
                outer.posts.append(payload)
                if outer.fail:

                    class Response:
                        status_code = 400
                        text = '{"message": "simulated rejection"}'

                        @staticmethod
                        def json():
                            return {"message": "simulated rejection"}

                    return Response()

                class Response:
                    status_code = 201
                    text = '{"messageId": "ok"}'

                    @staticmethod
                    def json():
                        return {"messageId": f"pm-{outer._n}"}

                return Response()

        return FakeClient


@pytest_asyncio.fixture
async def db(monkeypatch):
    import app.models  # noqa: F401  — register every mapped table

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # Inline sends run through the task layer's own session factory; point it
    # at this test's in-memory database (StaticPool = one shared connection).
    monkeypatch.setattr("app.tasks.sms_tasks.async_session_factory", factory)
    monkeypatch.setattr("app.services.campaign_jobs.async_session_factory", factory)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def api(db):
    """HTTP client over the app, backed by the same session as ``db``."""
    from app.main import app

    async def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="tester", email="t@example.com",
        password_hash="x", role="admin", is_active=True,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def brevo(monkeypatch):
    posts: list[dict] = []
    fake = _Brevo(posts)
    # Install the CLIENT CLASS (not the factory method): the provider builds
    # httpx.AsyncClient(timeout=..., headers=...), and FakeClient.__init__
    # swallows any arguments.
    monkeypatch.setattr("app.providers.brevo.httpx.AsyncClient", fake.client())
    return fake


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _account(db, n: int, **kw):
    from app.models.email import EmailAccount
    from app.security.encryption import encrypt_value

    acc = EmailAccount(
        name=kw.pop("name", f"Sender {n}"),
        from_email=kw.pop("from_email", f"sender{n}@brandmail{n}.io"),
        from_name=kw.pop("from_name", f"Sender {n}"),
        api_key_encrypted=encrypt_value(f"key-{n}"),
        is_active=True,
        is_default=kw.pop("is_default", n == 1),
        **kw,
    )
    db.add(acc)
    await db.flush()
    return acc


async def _ads_email_campaign(db, accounts, *, n_contacts=4, subject="Quick question"):
    """An Email Manager campaign with a sender pool, set + creative + audience."""
    from app.models.ads import AdsCampaign, AdsCreative, AdsFollowUpStep, AdsSet
    from app.models.contact_list import ContactList, ContactListMember

    contacts = []
    listing = ContactList(name="Acceptance audience")
    db.add(listing)
    await db.flush()
    for i in range(n_contacts):
        c = Contact(
            email=f"lead{i}@prospect{i}.io",
            first_name=f"Lead{i}",
            email_verified=True,
            email_verified_at=datetime.now(timezone.utc),
            email_confidence=95,
        )
        db.add(c)
        await db.flush()
        db.add(ContactListMember(list_id=listing.id, contact_id=c.id))
        contacts.append(c)
    await db.flush()

    campaign = AdsCampaign(
        name="Acceptance campaign",
        objective="replies",
        status="draft",
        channel="email",
        test_mode=False,
        subject=subject,
        email_account_ids=[a.id for a in accounts],
    )
    db.add(campaign)
    await db.flush()
    ads_set = AdsSet(campaign_id=campaign.id, name="Primary", status="active",
                     list_ids=str(listing.id))
    db.add(ads_set)
    await db.flush()
    creative = AdsCreative(
        campaign_id=campaign.id, set_id=ads_set.id, name="Hello",
        subject=subject, body="Hi {{first_name}}, quick question about your team.",
        status="active", allocation=100,
    )
    db.add(creative)
    await db.flush()
    await ads_service.ensure_version(db, creative)

    step = AdsFollowUpStep(
        campaign_id=campaign.id, step_order=1, name="Reminder 1",
        subject=subject, body="Just bumping this {{first_name}}.",
        wait_hours=0, condition="no_reply", action="send_email", is_active=True,
    )
    db.add(step)
    await db.flush()
    return campaign, contacts, ads_set, creative


async def _root_message(db, contact, account, *, subject="Quick question", **kw):
    """A first outbound email already sent to ``contact`` from ``account``."""
    conv = (await db.execute(
        select(Conversation).where(Conversation.contact_id == contact.id,
                                   Conversation.channel == "email")
    )).scalars().first()
    if conv is None:
        conv = Conversation(contact_id=contact.id, channel="email",
                            subject=subject, email_account_id=account.id)
        db.add(conv)
        await db.flush()
    tag = kw.pop("tag", "x")
    msg = Message(
        conversation_id=conv.id, contact_id=contact.id, direction="outgoing",
        channel="email", body="Hi", subject=subject, status="delivered",
        to_address=contact.email, from_address=account.from_email,
        email_account_id=account.id, provider="brevo",
        sent_at=datetime.now(timezone.utc),
        rfc_message_id=f"<root-{contact.id}-{tag}@brandmail1.io>",
        provider_message_id=f"root-{contact.id}-{tag}",
        idempotency_key=f"root-{contact.id}-{kw.pop('key', tag)}",
        **kw,
    )
    db.add(msg)
    await db.flush()
    return msg


# ---------------------------------------------------------------------------
# Acceptance 1 — pinned sender, threading, no footer in follow-ups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acceptance_followups_keep_original_sender_thread_and_plain_body(
    db, brevo,
):
    a1, a2 = await _account(db, 1), await _account(db, 2)
    campaign, contacts, ads_set, _creative = await _ads_email_campaign(db, [a1, a2])
    result = await ads_service.assign_contacts(db, campaign, ads_set, contacts)
    assert result["assigned"] == 4
    campaign.status = "active"
    await db.commit()

    dispatched = await ads_service.dispatch_campaign(db, campaign, send_inline=True)
    await db.commit()
    assert dispatched["sent"] == 4

    first = (await db.execute(
        select(Message).where(Message.ads_campaign_id == campaign.id,
                              Message.direction == "outgoing")
    )).scalars().all()
    assert len(first) == 4
    sender_of = {m.contact_id: m.email_account_id for m in first}
    assert all(sid in (a1.id, a2.id) for sid in sender_of.values())
    assert len(brevo.posts) == 4  # test_mode is off — the provider really got them

    # The operator now swaps the campaign's senders.
    a3, a4 = await _account(db, 3), await _account(db, 4)
    campaign.email_account_ids = [a3.id, a4.id]
    await db.commit()

    # Every queued follow-up is due; run the sweep like the worker would.
    from app.models.ads import AdsFollowUpTask

    tasks = (await db.execute(select(AdsFollowUpTask))).scalars().all()
    assert tasks, "dispatch must have queued the first follow-up step"
    for t in tasks:
        t.due_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    await db.commit()

    result = await ads_service.process_followups(db, send_inline=True, limit=50)
    await db.commit()
    assert result["sent"] == 4

    followups = (await db.execute(
        select(Message).where(Message.ads_campaign_id == campaign.id,
                              Message.direction == "outgoing")
        .order_by(Message.id)
    )).scalars().all()
    assert len(followups) == 8  # 4 first emails + 4 follow-ups
    for m in followups[4:]:
        root = next(r for r in first if r.contact_id == m.contact_id)
        # 1. sender is the contact's ORIGINAL sender, not a3/a4
        assert m.email_account_id == sender_of[m.contact_id]
        # 2. threaded: In-Reply-To/References point at the ORIGINAL thread
        assert m.in_reply_to == root.rfc_message_id
        post = next(p for p in brevo.posts if p.get("subject") == m.subject
                    and any(t.get("email") == m.to_address for t in p.get("to", [])))
        headers = post.get("headers") or {}
        assert headers.get("In-Reply-To") == root.rfc_message_id
        assert root.rfc_message_id in (headers.get("References") or "")
        # 3. exactly one "Re: "
        assert m.subject == f"Re: {root.subject}"
        assert not m.subject.startswith("Re: Re:")
        # 4. personal-looking body: no button, no card — and NO visible
        #    unsubscribe control of any kind (permanently removed)
        assert "<button" not in (m.html_body or "")
        assert "600px" not in (m.html_body or "")
        assert "unsubscribe" not in (m.body or "").lower()
        assert "unsubscribe" not in (m.html_body or "").lower()
        # 5. the send-log row records the sender used
        from app.models.email import EmailSendLog

        log_rows = (await db.execute(
            select(EmailSendLog).where(EmailSendLog.message_id == m.id)
        )).scalars().all()
        assert log_rows and log_rows[0].account_id == sender_of[m.contact_id]


@pytest.mark.asyncio
async def test_acceptance_two_replies_never_prefix_again(db):
    """Replying twice keeps one 'Re: ' (plain mail-client semantics)."""
    a1 = await _account(db, 1)
    c = Contact(email="twice@prospect.io", first_name="T")
    db.add(c)
    await db.flush()
    root = await _root_message(db, c, a1, subject="Original subject", tag="r2", key="r2")
    assert email_service.reply_subject(root.subject) == "Re: Original subject"
    assert email_service.reply_subject(email_service.reply_subject(root.subject)) == \
        "Re: Original subject"


# ---------------------------------------------------------------------------
# Acceptance 2 — dead-worker detection + overdue follow-ups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acceptance_dead_worker_flagged_within_five_minutes_and_cleared(db, api, brevo):
    from app.models.followup import FollowUp
    from app.services.system_settings import WORKER_HEARTBEAT, set_setting

    # A follow-up is overdue right now…
    c = Contact(phone_number="+2348031234901", email="late@prospect.io")
    db.add(c)
    await db.flush()
    fu = FollowUp(
        contact_id=c.id, channel="email", message_text="Overdue reminder",
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        status="pending",
    )
    db.add(fu)
    # …and the worker heartbeat is 6 minutes old → dead.
    await set_setting(db, WORKER_HEARTBEAT, f"{time.time() - 360:.1f}")
    await db.commit()

    resp = await api.get("/api/v1/system/health")
    assert resp.status_code == 200
    body = resp.json()
    worker = body["components"]["background_worker"]
    assert worker["stale"] is True
    assert worker["age_seconds"] > 300
    assert worker["due_followups"] >= 1
    assert body["degraded"] is True
    # The banner text names the fix (start a worker / process now).
    assert "worker" in worker["message"].lower()

    # The worker comes back: a fresh heartbeat clears the stale flag.
    from app.tasks.system_tasks import touch_worker_heartbeat

    await touch_worker_heartbeat(db, source="test-worker")
    await db.commit()
    resp = await api.get("/api/v1/system/health")
    assert resp.json()["components"]["background_worker"]["stale"] is False


@pytest.mark.asyncio
async def test_acceptance_process_now_sends_overdue_followups(db, brevo):
    a1 = await _account(db, 1)
    campaign, contacts, ads_set, _creative = await _ads_email_campaign(db, [a1], n_contacts=1)
    c = contacts[0]
    result = await ads_service.assign_contacts(db, campaign, ads_set, contacts)
    assert result["assigned"] == 1
    campaign.status = "active"
    await db.commit()
    await ads_service.dispatch_campaign(db, campaign, send_inline=True)
    await db.commit()

    from app.models.ads import AdsFollowUpTask

    task = (await db.execute(select(AdsFollowUpTask))).scalars().first()
    assert task is not None and task.status == "pending"
    # Even before its due time, "Process now" (task_ids) sends it.
    result = await ads_service.process_followups(db, send_inline=True, task_ids=[task.id])
    await db.commit()
    assert result["sent"] == 1
    await db.refresh(task)
    assert task.status == "completed"
    followup_msgs = (await db.execute(
        select(Message).where(Message.ads_campaign_id == campaign.id,
                              Message.direction == "outgoing")
    )).scalars().all()
    assert len(followup_msgs) == 2  # first + follow-up


# ---------------------------------------------------------------------------
# Acceptance 3 — Inbox reply: pinned sender, one Re:, thread, real failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acceptance_inbox_reply_uses_original_sender_and_threads(db, api, brevo):
    a1, a2 = await _account(db, 1), await _account(db, 2)
    c = Contact(email="inbox@prospect.io", first_name="Ina")
    db.add(c)
    await db.flush()
    root = await _root_message(db, c, a1, subject="Original subject", tag="inbox", key="inbox")

    resp = await api.post(
        f"/api/v1/email/inbox/conversations/{root.conversation_id}/reply",
        json={"body": "Thanks for getting back to me."},
    )
    assert resp.status_code == 200, resp.text
    sent = resp.json()
    assert sent["success"] is True
    assert sent["message"]["sender"]["email_account_id"] == a1.id

    post = brevo.posts[-1]
    assert post["sender"]["email"] == a1.from_email
    assert post["subject"] == "Re: Original subject"
    headers = post.get("headers") or {}
    assert headers.get("In-Reply-To") == root.rfc_message_id
    assert root.rfc_message_id in (headers.get("References") or "")

    # A second reply still shows exactly one "Re: ".
    resp = await api.post(
        f"/api/v1/email/inbox/conversations/{root.conversation_id}/reply",
        json={"body": "One more thing."},
    )
    assert resp.status_code == 200
    assert resp.json()["message"]["subject"] == "Re: Original subject"

    # The conversation detail shows which sender each message left from.
    resp = await api.get(f"/api/v1/email/inbox/conversations/{root.conversation_id}")
    assert resp.status_code == 200
    detail = resp.json()
    rows = detail.get("messages") or detail.get("items") or []
    if isinstance(detail.get("conversation"), dict) and not rows:
        rows = detail["conversation"].get("messages") or []
    assert rows, "conversation detail must include its messages"
    outgoing = [r for r in rows if (r.get("direction") or r.get("message", {}).get("direction")) == "outgoing"]
    assert outgoing
    for row in outgoing:
        msg = row.get("message", row)
        sender = msg.get("sender") or {}
        assert sender.get("email_account_id") in (a1.id,)


@pytest.mark.asyncio
async def test_acceptance_inbox_reply_failure_surfaces_real_error(db, api, brevo):
    a1 = await _account(db, 1)
    c = Contact(email="bounce@prospect.io", first_name="B")
    db.add(c)
    await db.flush()
    root = await _root_message(db, c, a1, subject="Subject", tag="fail", key="fail")

    brevo.fail = True
    resp = await api.post(
        f"/api/v1/email/inbox/conversations/{root.conversation_id}/reply",
        json={"body": "This should surface the provider error."},
    )
    assert resp.status_code == 502
    assert "simulated rejection" in resp.text
    # No stub/phantom message row.
    rows = (await db.execute(
        select(Message).where(Message.conversation_id == root.conversation_id)
    )).scalars().all()
    assert len(rows) == 1  # just the root


# ---------------------------------------------------------------------------
# Acceptance 4 — unsubscribe: reply keyword + one-click; GET never mutates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acceptance_unsubscribe_reply_opts_out_and_stops_followups(db):
    a1 = await _account(db, 1)
    c = Contact(email="bye@prospect.io", first_name="B", is_email_opted_out=False)
    db.add(c)
    await db.flush()
    root = await _root_message(db, c, a1, subject="Offer", tag="unsub", key="unsub")

    from app.models.followup import FollowUp

    fu = FollowUp(
        contact_id=c.id, channel="email", message_text="pending nudge",
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=2),
        status="pending",
    )
    db.add(fu)
    await db.commit()

    await email_service.process_inbound_email(db, {
        "From": "bye@prospect.io",
        "To": a1.from_email,
        "Subject": "Re: Offer",
        "InReplyTo": root.rfc_message_id,
        "RawTextBody": "Please unsubscribe me",
    })
    await db.commit()

    await db.refresh(c)
    assert c.is_email_opted_out is True
    await db.refresh(fu)
    assert fu.status == "cancelled"


@pytest.mark.asyncio
async def test_acceptance_one_click_unsubscribe_and_safe_get(db, api):
    c = Contact(email="click@prospect.io", first_name="C", is_email_opted_out=False)
    db.add(c)
    await db.commit()
    token = email_service._unsubscribe_token("click@prospect.io")
    url = f"/api/v1/email/unsubscribe?e=click@prospect.io&t={token}"

    # GET shows the confirm page and changes NOTHING.
    resp = await api.get(url, headers={"accept": "text/html"})
    assert resp.status_code == 200
    await db.refresh(c)
    assert c.is_email_opted_out is False

    # The RFC 8058 one-click POST is what opts the contact out.
    resp = await api.post(url)
    assert resp.status_code == 200
    await db.refresh(c)
    assert c.is_email_opted_out is True

    # Forged token: refused, no opt-out.
    other = Contact(email="forged@prospect.io", first_name="F", is_email_opted_out=False)
    db.add(other)
    await db.commit()
    resp = await api.post("/api/v1/email/unsubscribe?e=forged@prospect.io&t=nope")
    assert resp.status_code in (400, 401, 403)
    await db.refresh(other)
    assert other.is_email_opted_out is False


# ---------------------------------------------------------------------------
# Acceptance 5 — real replies register, stop follow-ups and alert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acceptance_positive_reply_counts_stops_and_alerts(db, brevo):
    from app.models.ads import AdsAssignment, AdsEvent, AdsFollowUpTask
    from app.models.notification import NotificationEvent

    a1 = await _account(db, 1)
    campaign, contacts, ads_set, _creative = await _ads_email_campaign(db, [a1], n_contacts=1)
    c = contacts[0]
    await ads_service.assign_contacts(db, campaign, ads_set, contacts)
    campaign.status = "active"
    await db.commit()
    await ads_service.dispatch_campaign(db, campaign, send_inline=True)
    await db.commit()

    root = (await db.execute(
        select(Message).where(Message.ads_campaign_id == campaign.id)
    )).scalars().first()
    fu = (await db.execute(select(AdsFollowUpTask))).scalars().first()
    assert fu.status in ("pending", "paused")

    await email_service.process_inbound_email(db, {
        "From": c.email,
        "To": a1.from_email,
        "Subject": "Re: Quick question",
        "InReplyTo": root.rfc_message_id,
        "RawTextBody": "Yes, I'm interested. Please send details.",
    })
    await db.commit()

    await db.refresh(c)
    assert c.lead_status == "replied"
    await db.refresh(fu)
    assert fu.status == "cancelled"  # the reply stops the follow-up chain

    # The reply registers on the campaign through the assignment + event log.
    assignment = (await db.execute(
        select(AdsAssignment).where(AdsAssignment.campaign_id == campaign.id)
    )).scalars().first()
    assert assignment.reply_status == "positive"
    events = (await db.execute(
        select(AdsEvent).where(AdsEvent.campaign_id == campaign.id)
    )).scalars().all()
    assert any("REPLY" in (e.event_type or "") for e in events)

    # …and the operator is alerted.
    notes = (await db.execute(select(NotificationEvent))).scalars().all()
    blob = " ".join(
        f"{n.event_type} {n.title or ''} {n.body or ''}".lower() for n in notes
    )
    assert "reply" in blob or "interested" in blob


# ---------------------------------------------------------------------------
# Acceptance 6 — delivery confirmations per sender + "unconfirmed"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acceptance_delivery_confirmations_and_unconfirmed_poll(db, api, monkeypatch):
    a1, a2 = await _account(db, 1), await _account(db, 2)
    await email_service.ensure_webhook_token(db, a1)
    await email_service.ensure_webhook_token(db, a2)
    c1 = Contact(email="d1@prospect.io")
    c2 = Contact(email="d2@prospect.io")
    c3 = Contact(email="d3@prospect.io")
    db.add_all([c1, c2, c3])
    await db.flush()
    m1 = await _root_message(db, c1, a1, subject="S", tag="d1", key="d1")
    m2 = await _root_message(db, c2, a2, subject="S", tag="d2", key="d2")
    m3 = await _root_message(db, c3, a2, subject="S", tag="d3", key="d3")
    old = datetime.now(timezone.utc) - timedelta(minutes=45)
    for m in (m1, m2, m3):
        m.status = "sent"
        m.sent_at = old
    await db.commit()

    # Sender 1 confirms via the webhook…
    resp = await api.post(
        f"/api/v1/webhooks/brevo/{a1.id}?token={a1.webhook_token}",
        json={"event": "delivered", "email": "d1@prospect.io",
              "message-id": m1.provider_message_id},
    )
    assert resp.status_code in (200, 202), resp.text
    assert resp.json().get("success") is not False
    await db.refresh(m1)
    assert m1.status == "delivered"

    # Sender 2's webhook is broken → the Brevo event-API poll confirms m2…
    async def fake_list_events(api_key, **kw):
        return {"success": True, "events": [
            {"event": "delivered", "email": "d2@prospect.io",
             "messageId": m2.provider_message_id,
             "date": datetime.now(timezone.utc).isoformat()},
        ]}

    monkeypatch.setattr("app.providers.brevo.list_events", fake_list_events)
    result = await email_service.poll_unconfirmed_deliveries(db, limit=25)
    await db.commit()
    assert result["confirmed"] >= 1
    await db.refresh(m2)
    assert m2.status == "delivered"

    # m3 has NO event → flagged "unconfirmed", not "failed".
    await db.refresh(m3)
    assert m3.status == "sent"
    assert email_service.delivery_state(m3) == "unconfirmed"

    # The account UI payload reports webhook health per sender.
    await db.refresh(a1)
    await db.refresh(a2)
    s1 = email_service.serialize_account(a1)
    s2 = email_service.serialize_account(a2)
    assert s1["webhook_status"]["last_event_at"] is not None
    assert s1["webhook_status"]["receiving"] is True
    assert s2["webhook_status"]["registered"] is True

    totals = await email_service.email_totals(db, days=30)
    assert totals["unconfirmed"] >= 1
    assert "human_opens" in totals and "bot_opens" in totals


# ---------------------------------------------------------------------------
# Acceptance 7 — calendar works on a migrated legacy database
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acceptance_calendar_upcoming_on_migrated_legacy_db(tmp_path):
    import sqlite3
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from app.models.user import User as UserModel

    legacy = tmp_path / "legacy.db"
    starts = datetime.now(timezone.utc) + timedelta(days=1)
    ends = starts + timedelta(minutes=30)
    conn = sqlite3.connect(legacy)
    conn.executescript(
        f"""
        CREATE TABLE meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT,
            starts_at TEXT,
            ends_at TEXT,
            location TEXT,
            contact_id INTEGER,
            created_at TEXT,
            updated_at TEXT
        );
        INSERT INTO meetings (title, status, starts_at, ends_at, contact_id,
                              created_at, updated_at)
        VALUES ('Legacy meeting', 'scheduled', '{starts.isoformat()}',
                '{ends.isoformat()}', 1, '{starts.isoformat()}',
                '{starts.isoformat()}');
        """
    )
    conn.commit()
    conn.close()

    backend_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_dir / "alembic.ini"))
    # script_location in the ini is relative to CWD; pin it to backend/.
    cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{legacy}")
    # env.py drives alembic with asyncio.run(); run it off the event loop.
    import asyncio as _asyncio

    await _asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(f"sqlite+aiosqlite:///{legacy}")
    # schema_repair's additive create_all for the rest of the tables — the
    # legacy DB only ever had the ones its app needed.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(UserModel(id=1, username="u", email="u@e.io",
                              password_hash="x", role="admin", is_active=True))
        await session.commit()

    from app.main import app

    async def _get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="tester", email="t@example.com",
        password_hash="x", role="admin", is_active=True,
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/calendar/upcoming")
            assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Smaller unit checks the acceptance list implies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bot_clicks_and_proxy_opens_never_count(db):
    from app.models.email import EmailEvent

    acc = await _account(db, 1)
    c = Contact(email="bot@prospect.io")
    db.add(c)
    await db.flush()
    m = await _root_message(db, c, acc, subject="S", tag="bot", key="bot")
    m.status = "delivered"
    m.open_count = 0
    m.click_count = 0
    await db.commit()

    # A proxy open (pixel-proxy flag) is stored as evidence, never counted.
    await email_service.apply_event(db, "opened", {
        "email": "bot@prospect.io",
        "message-id": m.provider_message_id,
        "proxy_open": "true",
    })
    await db.commit()
    await db.refresh(m)
    assert (m.open_count or 0) == 0

    # A security-scanner click is never counted either.
    await email_service.apply_event(db, "clicked", {
        "email": "bot@prospect.io",
        "message-id": m.provider_message_id,
        "user_agent": "Mozilla/5.0 (compatible; Barracuda 300)",
    })
    await db.commit()
    await db.refresh(m)
    assert (m.click_count or 0) == 0
    events = (await db.execute(
        select(EmailEvent).where(EmailEvent.message_id == m.id)
    )).scalars().all()
    assert any(e.event_type == "opened" and e.is_bot
               and e.bot_reason == "proxy_open" for e in events)
    assert any(e.event_type == "clicked" and e.is_bot
               and (e.bot_reason or "").startswith("scanner_ua:") for e in events)

    # A click seconds after an open is the scanner signature — also not counted.
    m2 = await _root_message(db, c, acc, subject="S2", tag="bot2", key="bot2")
    m2.status = "delivered"
    await db.commit()
    await email_service.apply_event(db, "opened", {
        "email": "bot@prospect.io", "message-id": m2.provider_message_id,
    })
    await email_service.apply_event(db, "clicked", {
        "email": "bot@prospect.io", "message-id": m2.provider_message_id,
    })
    await db.commit()
    await db.refresh(m2)
    assert (m2.click_count or 0) == 0
    events = (await db.execute(
        select(EmailEvent).where(EmailEvent.message_id == m2.id)
    )).scalars().all()
    assert any(e.event_type == "clicked" and e.is_bot
               and e.bot_reason == "click_within_10s_of_open" for e in events)


def test_plain_mode_has_no_pixel_no_button_and_no_unsubscribe_footer(monkeypatch):
    monkeypatch.setattr(
        "app.utils.urls.public_base_url", lambda: "https://app.example.com"
    )
    html = email_service.text_to_html(
        "Hi Ada,\nJust checking in.\nhttps://track.example/x", plain=True
    )
    assert "1x1" not in html and "tracking" not in html.lower()
    assert "<button" not in html and "<table" not in html
    assert "background-color: #f4f4f5" not in html
    # The unsubscribe button/line is permanently removed: nothing visible.
    assert "unsubscribe" not in html.lower()
    assert email_service.plain_footer_html("ada@example.org") is None
    # The raw URL never gets wrapped into a tracked link in plain mode.
    assert "track.example/x</a>" not in html
