"""Walk the whole API against a REAL PostgreSQL, with data in it.

The inbox crash that produced "column conversations.status must appear in the
GROUP BY clause" was invisible to the SQLite test suite: SQLite happily groups
by an expression it does not recognise in the select list, PostgreSQL does not.
Anything that only ever runs on SQLite can therefore ship broken SQL to
production and take a whole page down.

This tool boots an embedded PostgreSQL (pgserver, the same one the
test_postgres_compat suite uses), creates the schema, seeds realistic rows
across every feature the app reads from, and then drives every parameterless
GET endpoint in the app's own OpenAPI document plus the important POST flows.

Usage:
    python tools/pg_walk.py            # full walk
    python tools/pg_walk.py --quiet    # only failures
"""
import argparse
import asyncio
import os
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pgserver  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.database import Base, get_db  # noqa: E402
from app.models import *  # noqa: F401,F403,E402  (registers every table)
from app.models.ads import AdsAssignment, AdsCampaign, AdsCreative, AdsSet  # noqa: E402
from app.models.campaign import Campaign, CampaignContact  # noqa: E402
from app.models.contact import Contact, Tag  # noqa: E402
from app.models.contact_list import ContactList, ContactListMember  # noqa: E402
from app.models.conversation import Conversation, Message  # noqa: E402
from app.models.followup import FollowUp  # noqa: E402
from app.models.sequence import Sequence, SequenceStep  # noqa: E402
from app.models.template import Template  # noqa: E402
from app.models.user import User  # noqa: E402
from app.security.auth import get_current_user  # noqa: E402

NOW = datetime.now(timezone.utc)

# Endpoints expected to fail for reasons unrelated to the database:
# third-party credentials, network access, a required parameter this walker
# cannot invent, or a feature that is deliberately disabled by default.
EXPECTED = {
    "/api/v1/auth/me",
    "/api/v1/ads/search",
    "/api/v1/calendar/day",
    "/api/v1/email/unsubscribe",
    "/api/v1/mailbox/google/start",
    "/api/v1/calls/webhooks",
    "/api/v1/settings/callgate/webhooks",
    "/api/v1/settings/gateway/webhooks",
    "/api/v1/mcp/public",
    "/api/v1/mcp/oauth/authorize",
    "/api/v1/overview/queue",
}
# A database failure is never acceptable, even on an "expected" endpoint.
DB_MARKERS = ("Database unavailable", "DATABASE_UNAVAILABLE", "must appear in the GROUP BY")


async def seed(db: AsyncSession) -> dict:
    """Realistic rows across the features the API reads from."""
    ids: dict = {}

    contacts = [
        Contact(phone_number="+2348011110001", first_name="Ada", last_name="Obi",
                business_name="Obi Foods", email="ada@example.com", state="Lagos",
                city="Ikeja", lead_status="interested", source="import",
                is_opted_out=False),
        Contact(phone_number="+2348011110002", first_name="Chidi", last_name="Eze",
                email="chidi@example.com", lead_status="replied", source="campaign",
                state="Abuja", is_email_opted_out=False),
        Contact(phone_number="+2348011110003", first_name="Ngozi", lead_status="new",
                source="inbound_sms"),
    ]
    db.add_all(contacts)
    await db.flush()
    ids["contacts"] = [c.id for c in contacts]

    tag = Tag(name="vip")
    db.add(tag)
    await db.flush()
    ids["tag"] = tag.id

    template = Template(name="Weekend promo", body="Hi {{first_name}}, 20% off this weekend!",
                        category="promo", channel="sms", is_active=True,
                        char_count=44, segment_count=1)
    db.add(template)
    await db.flush()
    ids["template"] = template.id

    campaign = Campaign(name="Weekend blast", status="running", channel="sms",
                        message_body="Hi {{first_name}}, 20% off!", total_contacts=3,
                        messages_sent=2, messages_delivered=2, replies=1)
    db.add(campaign)
    await db.flush()
    ids["campaign"] = campaign.id
    db.add_all([
        CampaignContact(campaign_id=campaign.id, contact_id=c.id, status="sent")
        for c in contacts
    ])

    seq = Sequence(name="Welcome", description="drip")
    db.add(seq)
    await db.flush()
    ids["sequence"] = seq.id
    db.add(SequenceStep(sequence_id=seq.id, step_order=1, step_type="send_sms",
                        config='{"body": "Step one"}', wait_duration_hours=1,
                        template_id=template.id))

    # Three threads: read SMS, unread SMS, and an email thread.
    convs = []
    for i, (contact, status, unread) in enumerate(
        [(contacts[0], "interested", 0), (contacts[1], "unread", 2), (contacts[2], "active", 0)]
    ):
        conv = Conversation(contact_id=contact.id, channel="sms", status=status,
                            unread_count=unread, message_count=2,
                            last_message_preview=f"preview {i}",
                            last_message_at=NOW - timedelta(minutes=i),
                            campaign_id=campaign.id)
        db.add(conv)
        convs.append(conv)
    await db.flush()
    ids["conversations"] = [c.id for c in convs]

    msgs = []
    for conv in convs:
        msgs.append(Message(conversation_id=conv.id, contact_id=conv.contact_id,
                            direction="outgoing", channel="sms", body="Hello there",
                            status="delivered", provider="smsgate",
                            idempotency_key=f"out-{conv.id}",
                            created_at=NOW - timedelta(minutes=5),
                            sent_at=NOW - timedelta(minutes=5),
                            delivered_at=NOW - timedelta(minutes=4)))
        msgs.append(Message(conversation_id=conv.id, contact_id=conv.contact_id,
                            direction="incoming", channel="sms", body="Yes please",
                            status="delivered", provider="smsgate",
                            ai_sentiment="positive", ai_intent="interested",
                            idempotency_key=f"in-{conv.id}", created_at=NOW))
    db.add_all(msgs)
    await db.flush()
    ids["messages"] = [m.id for m in msgs]

    lst = ContactList(name="Lagos leads", description="demo")
    db.add(lst)
    await db.flush()
    ids["list"] = lst.id
    db.add_all([ContactListMember(list_id=lst.id, contact_id=c.id) for c in contacts])

    followup = FollowUp(contact_id=contacts[0].id, campaign_id=campaign.id,
                        scheduled_at=NOW + timedelta(hours=2), status="pending",
                        message_text="Checking in!")
    db.add(followup)
    await db.flush()
    ids["followup"] = followup.id

    ads_campaign = AdsCampaign(name="Ads promo", status="active", channel="sms",
                               daily_limit=100)
    db.add(ads_campaign)
    await db.flush()
    ids["ads_campaign"] = ads_campaign.id
    ads_set = AdsSet(name="Set A", campaign_id=ads_campaign.id, status="active")
    db.add(ads_set)
    await db.flush()
    creative = AdsCreative(campaign_id=ads_campaign.id, set_id=ads_set.id,
                           name="Creative A", body="Buy now", status="approved")
    db.add(creative)
    await db.flush()
    db.add(AdsAssignment(campaign_id=ads_campaign.id, set_id=ads_set.id,
                         creative_id=creative.id, contact_id=contacts[0].id,
                         phone_number=contacts[0].phone_number,
                         send_status="sent", idempotency_key="assign-walk-1"))
    await db.commit()
    return ids


async def walk(factory, quiet: bool = False) -> int:
    """Drive every endpoint the way production does: one session per request."""
    import app.main as main_module
    from app.config import settings
    from app.main import app

    # /health/db reports on the process-wide engine, not on the session the
    # other routes use. Point it at this walk's Postgres so the answer is real.
    main_module.async_session_factory = factory

    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="tester", email="t@example.com", password_hash="x",
        role="admin", is_active=True,
    )

    problems: list[tuple[str, int | str, str]] = []
    checked = 0
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                           base_url="http://test", timeout=60) as client:
        spec = app.openapi()
        targets = sorted({
            path for path, ops in spec["paths"].items()
            if path.startswith("/api/v1") and "{" not in path and "get" in ops
        })
        for path in targets:
            rel = path[len("/api/v1"):] or "/"
            try:
                resp = await client.get(path)
            except Exception as exc:  # noqa: BLE001
                problems.append((path, "EXC", f"{type(exc).__name__}: {exc}"[:160]))
                continue
            checked += 1
            body = resp.text[:200]
            if any(m in resp.text for m in DB_MARKERS):
                problems.append((path, resp.status_code, f"DB ERROR: {body}"))
            elif resp.status_code >= 500:
                problems.append((path, resp.status_code, body))
            elif resp.status_code >= 400 and rel not in EXPECTED and path not in EXPECTED:
                problems.append((path, resp.status_code, body))
            elif not quiet:
                print(f"  ok {resp.status_code} {path}")

        # --- POST flows that write through the ORM on the request path ------
        async with factory() as _s:
            conv_id = (await _s.execute(
                Conversation.__table__.select().with_only_columns(Conversation.id)
            )).first()[0]
        posts = [
            ("GET", f"/api/v1/inbox/conversations/{conv_id}", None),
            ("GET", f"/api/v1/inbox/conversations/{conv_id}", None),
            ("POST", f"/api/v1/inbox/conversations/{conv_id}/read", None),
            ("GET", "/api/v1/inbox/campaign-filters", None),
            ("POST", "/api/v1/auth/admin", {"password": os.environ.get("ADMIN_PASSWORD", "12345678")}),
            ("GET", "/api/v1/dashboard/stats", None),
            ("GET", "/api/v1/dashboard/charts?days=30", None),
            ("GET", "/api/v1/overview/metrics?channel=sms", None),
            ("GET", "/api/v1/overview/metrics", None),
            ("GET", "/api/v1/analytics/overview?days=30", None),
            ("GET", "/api/v1/contacts?page=1&per_page=25", None),
            ("GET", "/api/v1/campaigns/", None),
            ("GET", "/api/v1/lists/", None),
            ("GET", "/api/v1/templates/", None),
            # The receive-path diagnostics the inbox page calls. They talk to
            # the gateway over the network, which is unavailable here, so they
            # have to degrade to a report — never a 500.
            ("POST", "/api/v1/inbox/poll-debug", None),
            ("POST", "/api/v1/inbox/sync-full", None),
            ("POST", "/api/v1/inbox/poll-now", None),
            ("GET", "/api/v1/inbox/device-info", None),
        ]
        for method, url, payload in posts:
            try:
                resp = await (client.post(url, json=payload) if method == "POST"
                              else client.get(url))
            except Exception as exc:  # noqa: BLE001
                problems.append((url, "EXC", f"{type(exc).__name__}: {exc}"[:160]))
                continue
            checked += 1
            if any(m in resp.text for m in DB_MARKERS):
                problems.append((url, resp.status_code, f"DB ERROR: {resp.text[:200]}"))
            elif resp.status_code >= 500:
                problems.append((url, resp.status_code, resp.text[:200]))
            elif not quiet:
                print(f"  ok {resp.status_code} {url}")

        # The inbound webhook is how real SMS reach the inbox. Sign it the way
        # SMS-Gate.app does (HMAC-SHA256 over body + timestamp) and then check
        # the message really is in the inbox: sender -> contact -> thread.
        import hashlib
        import hmac
        import json
        import time

        settings.SMSGATE_WEBHOOK_SECRET = "walk-secret"
        body = {
            "deviceId": "dev-1", "event": "sms:received", "id": "evt-walk-1",
            "payload": {"messageId": "m-walk-1", "message": "I want to order",
                        "sender": "+2348099990001", "recipient": "+2340000000000",
                        "simNumber": 1, "receivedAt": NOW.isoformat()},
        }
        raw = json.dumps(body).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(b"walk-secret", raw + stamp.encode(), hashlib.sha256).hexdigest()
        resp = await client.post("/api/v1/webhooks/smsgateway", content=raw, headers={
            "Content-Type": "application/json",
            "X-Signature": signature, "X-Timestamp": stamp,
        })
        checked += 1
        if resp.status_code >= 400 or "error" in resp.text:
            problems.append(("/api/v1/webhooks/smsgateway", resp.status_code, resp.text[:200]))
        elif not quiet:
            print(f"  ok {resp.status_code} /api/v1/webhooks/smsgateway {resp.text[:80]}")

        # An unsigned delivery must be refused: proves the signature gate works.
        resp = await client.post("/api/v1/webhooks/smsgateway", json=body)
        checked += 1
        if resp.status_code != 401:
            problems.append(("/api/v1/webhooks/smsgateway (unsigned)", resp.status_code,
                             f"expected 401, got {resp.text[:120]}"))
        elif not quiet:
            print(f"  ok {resp.status_code} unsigned webhook refused")

        inbox = await client.get("/api/v1/inbox/conversations")
        checked += 1
        try:
            data = inbox.json()
            if inbox.status_code != 200 or not data.get("items"):
                problems.append(("/api/v1/inbox/conversations", inbox.status_code,
                                 f"no items: {inbox.text[:160]}"))
            elif not quiet:
                print(f"  ok inbox items={len(data['items'])} counts={data.get('counts')}")

            # The webhook above must have produced a visible thread for the
            # sender, with the text on it: this is "the inbox receives SMS".
            landed = [
                item for item in data.get("items", [])
                if item.get("contact_phone") == "+2348099990001"
            ]
            if not landed:
                problems.append(("/api/v1/inbox/conversations", 200,
                                 "inbound SMS sender missing from the inbox"))
            else:
                thread_url = f"/api/v1/inbox/conversations/{landed[0]['id']}"
                thread = await client.get(thread_url)
                checked += 1
                if thread.status_code != 200:
                    problems.append((thread_url, thread.status_code, thread.text[:160]))
                elif "I want to order" not in thread.text:
                    problems.append((thread_url, thread.status_code,
                                     f"inbound text missing: {thread.text[:160]}"))
                elif not quiet:
                    print("  ok inbound SMS visible in the inbox thread")
        except Exception as exc:  # noqa: BLE001
            problems.append(("/api/v1/inbox/conversations", inbox.status_code,
                             f"{type(exc).__name__}: {exc} / {inbox.text[:120]}"))

    app.dependency_overrides.clear()

    print(f"\nchecked {checked} requests")
    for path, code, detail in problems:
        print(f"  [{code}] {path}  {detail}")
    print("RESULT:", "ALL CLEAN" if not problems else f"{len(problems)} PROBLEM(S)")
    return len(problems)


async def main(quiet: bool) -> int:
    tmp = tempfile.mkdtemp(prefix="pgwalk-")
    server = pgserver.get_server(tmp)
    url = server.get_uri().replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            await seed(db)
            # A production database that was upgraded in place: rows written
            # before a column existed read NULL.
            from sqlalchemy import text
            await db.execute(text("ALTER TABLE conversations ALTER COLUMN channel DROP NOT NULL"))
            await db.execute(text("ALTER TABLE conversations ALTER COLUMN status DROP NOT NULL"))
            await db.execute(text("INSERT INTO conversations (contact_id, channel, status, "
                                  "message_count, unread_count, sequence_paused, created_at, updated_at) "
                                  "VALUES (:cid, NULL, NULL, 0, 0, false, now(), now())"),
                             {"cid": (await db.execute(
                                 Contact.__table__.select().with_only_columns(Contact.id))).first()[0]})
            await db.commit()
            return await walk(factory, quiet=quiet)
    finally:
        await engine.dispose()
        server.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    try:
        sys.exit(1 if asyncio.run(main(args.quiet)) else 0)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
