"""Tests for the P1-9 / P1-10 / P2 follow-up additions.

* validate_campaign flags senders that receive no delivery confirmations.
* A mailbox whose OAuth grant is dead is marked ``needs_reconnect``.
* "send now" on one follow-up row processes exactly that row.
* POST /campaigns/{id}/process-now runs the campaign's rules immediately.
* Follow-up listings carry a live ``is_overdue``.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact
from app.models.user import User
from app.security.auth import get_current_user
from app.services import ads_service, email_service


@pytest_asyncio.fixture
async def db(monkeypatch):
    import app.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.tasks.sms_tasks.async_session_factory", factory)
    monkeypatch.setattr("app.services.campaign_jobs.async_session_factory", factory)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def api(db):
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


async def _account(db, n, **kw):
    from app.models.email import EmailAccount
    from app.security.encryption import encrypt_value

    acc = EmailAccount(
        name=f"Sender {n}", from_email=f"sender{n}@brandmail{n}.io",
        from_name=f"Sender {n}", api_key_encrypted=encrypt_value(f"key-{n}"),
        is_active=True, **kw,
    )
    db.add(acc)
    await db.flush()
    return acc


@pytest.mark.asyncio
async def test_validate_flags_senders_without_delivery_confirmations(db, monkeypatch):
    a1, a2 = await _account(db, 1), await _account(db, 2)
    # a1 has a registered webhook that has received events; a2 has none.
    await email_service.ensure_webhook_token(db, a1)
    a1.last_webhook_event_at = datetime.now(timezone.utc)
    await db.commit()

    from app.models.ads import AdsCampaign

    campaign = AdsCampaign(
        name="Webhook check", objective="replies", status="draft",
        channel="email", test_mode=False, email_account_id=a1.id,
        email_account_ids=[a1.id, a2.id],
    )
    db.add(campaign)
    await db.commit()

    # Skip the live provider probes — this test is about the webhook check.
    async def ok_health(*args, **kwargs):
        return {"success": True}

    async def _usable(account):
        return (True, None)

    monkeypatch.setattr("app.services.email_service.sender_is_usable", _usable)

    result = await ads_service.validate_campaign(db, campaign)
    check = result["checks"]["webhook_events"]
    assert check["pass"] is False
    assert "1 of 2" in check["message"]
    # It is a warning, not a launch blocker.
    assert not any("delivery confirmations" in e for e in result.get("errors", []))
    assert any("delivery confirmations" in w for w in result.get("warnings", []))


@pytest.mark.asyncio
async def test_mailbox_needs_reconnect_on_dead_oauth_grant(db):
    from app.models.email_inbox import EmailMailbox

    box = EmailMailbox(
        name="Inbox", provider="gmail_api", email_address="me@gmail.com",
        credential_encrypted="x", last_sync_status="error",
        last_error="invalid_grant: Token has been expired or revoked.",
    )
    db.add(box)
    await db.commit()

    data = email_service.serialize_account  # not the one under test
    from app.services import mailbox_service

    out = mailbox_service.serialize_mailbox(box)
    assert out["needs_reconnect"] is True

    box.last_error = "Connection reset by peer"
    out = mailbox_service.serialize_mailbox(box)
    assert out["needs_reconnect"] is False


@pytest.mark.asyncio
async def test_send_now_processes_exactly_that_followup(db, api, monkeypatch):
    from app.models.ads import AdsFollowUpTask

    c = Contact(phone_number="+2348031234501", email="now@prospect.io")
    db.add(c)
    await db.flush()
    due_later = AdsFollowUpTask(
        campaign_id=1, contact_id=c.id,
        due_at=datetime.now(timezone.utc) + timedelta(days=2),
        status="pending", body="later",
    )
    db.add(due_later)
    await db.commit()

    sent = []

    async def fake_process(session, *, limit=50, send_inline=False, task_ids=None,
                           include_paused=False):
        sent.append(list(task_ids or []))
        for t in (await session.execute(
            select(AdsFollowUpTask).where(AdsFollowUpTask.id.in_(task_ids or []))
        )).scalars().all():
            t.status = "completed"
        return {"sent": len(task_ids or [])}

    monkeypatch.setattr(ads_service, "process_followups", fake_process)
    resp = await api.post(f"/api/v1/ads/followups/{due_later.id}/send-now")
    assert resp.status_code == 200
    assert resp.json()["sent"] == 1
    assert sent == [[due_later.id]]


@pytest.mark.asyncio
async def test_campaign_process_now_endpoint_runs_the_rules(db, api, monkeypatch):
    from app.models.campaign import Campaign
    from app.models.campaign_followup import CampaignFollowUp

    campaign = Campaign(
        name="Classic", status="running", channel="sms",
        list_id=None, message_body="hi",
    )
    db.add(campaign)
    await db.flush()
    for i in (1, 2):
        db.add(CampaignFollowUp(
            campaign_id=campaign.id, step_order=i, channel="sms",
            message_text=f"nudge {i}", is_active=True,
        ))
    await db.commit()

    runs = []

    async def fake_rule(session, rule, *, limit=100):
        runs.append(rule.step_order)
        return {"sent": 1, "stopped": 0, "skipped": 0, "failed": 0}

    monkeypatch.setattr(
        "app.api.v1.campaign_followups.process_rule", fake_rule
    )
    resp = await api.post(
        f"/api/v1/campaign-followups/campaigns/{campaign.id}/process-now"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["rules"] == 2
    assert body["sent"] == 2
    assert sorted(runs) == [1, 2]


def test_followup_item_is_overdue_is_computed_live():
    from app.api.v1.followups import _followup_item
    from app.models.followup import FollowUp

    past = FollowUp(
        contact_id=1, channel="sms", message_text="x", status="pending",
        scheduled_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    future = FollowUp(
        contact_id=1, channel="sms", message_text="x", status="pending",
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    done = FollowUp(
        contact_id=1, channel="sms", message_text="x", status="sent",
        scheduled_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    assert _followup_item(past, None)["is_overdue"] is True
    assert _followup_item(future, None)["is_overdue"] is False
    assert _followup_item(done, None)["is_overdue"] is False
