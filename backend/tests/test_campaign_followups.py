"""Campaign follow-up chains: timing, stop conditions and exactly-once sends.

These are the guarantees an operator is trusting with real phone numbers:

* A follow-up does not go out before its wait time has elapsed.
* It does not go out at all if the contact replied, opted out, or reached a
  lead status the operator chose as a stop.
* It never goes out twice.
* Step 2 only follows the people step 1 actually messaged.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.campaign import Campaign, CampaignContact
from app.models.campaign_followup import CampaignFollowUp, CampaignFollowUpLog
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.models.suppression import SuppressionEntry
from app.models.user import User
from app.security.auth import get_current_user
from app.services.campaign_followup_service import (
    evaluate_contact,
    process_rule,
    within_send_window,
)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db):
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
def sent_gateway(monkeypatch):
    """Pretend the SMS gateway accepts everything, and record the sends."""
    sent = []

    async def fake_send(phone, body, sim=1):
        sent.append({"phone": phone, "body": body})
        return {"success": True, "provider_message_id": f"pm-{len(sent)}", "raw": {}}

    monkeypatch.setattr("app.providers.smsgate.send_sms_direct", fake_send)

    async def no_limits(self):
        return {"allowed": True, "reason": None}

    monkeypatch.setattr("app.services.sending_limits.SendingGate.check", no_limits)
    return sent


async def build_campaign(db, *, contacts=1, hours_ago=48, status="running"):
    """A running campaign whose first message went out `hours_ago`."""
    campaign = Campaign(name="Cold outreach", status=status, message_body="Hello")
    db.add(campaign)
    await db.flush()

    made = []
    sent_at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    for index in range(contacts):
        contact = Contact(phone_number=f"+23480312345{index:02d}", first_name=f"C{index}")
        db.add(contact)
        await db.flush()

        conversation = Conversation(contact_id=contact.id, status="active")
        db.add(conversation)
        await db.flush()

        db.add(
            Message(
                conversation_id=conversation.id,
                contact_id=contact.id,
                campaign_id=campaign.id,
                direction="outgoing",
                body="Hello",
                status="delivered",
                sent_at=sent_at,
                created_at=sent_at,
                idempotency_key=f"seed-{campaign.id}-{contact.id}",
            )
        )
        db.add(
            CampaignContact(
                campaign_id=campaign.id,
                contact_id=contact.id,
                status="sent",
                last_message_at=sent_at,
            )
        )
        made.append(contact)
    await db.flush()
    return campaign, made


async def add_rule(db, campaign, **kw):
    defaults = dict(
        campaign_id=campaign.id,
        step_order=1,
        name="Follow-up 1",
        message_text="Just checking in, {{first_name}}",
        delay_minutes=24 * 60,
    )
    defaults.update(kw)
    rule = CampaignFollowUp(**defaults)
    db.add(rule)
    await db.flush()
    return rule


async def reply_from(db, contact, when=None):
    conversation = (
        await db.execute(select(Conversation).where(Conversation.contact_id == contact.id))
    ).scalars().first()
    db.add(
        Message(
            conversation_id=conversation.id,
            contact_id=contact.id,
            direction="incoming",
            body="Yes please",
            status="delivered",
            created_at=when or datetime.now(timezone.utc),
            idempotency_key=f"in-{contact.id}-{when or 'now'}",
        )
    )
    await db.flush()


class TestTiming:
    @pytest.mark.asyncio
    async def test_waits_until_the_delay_has_elapsed(self, db, sent_gateway):
        campaign, _ = await build_campaign(db, hours_ago=2)
        rule = await add_rule(db, campaign, delay_minutes=24 * 60)

        result = await process_rule(db, rule)
        assert result["sent"] == 0
        assert result["waiting"] == 1
        assert sent_gateway == []

    @pytest.mark.asyncio
    async def test_sends_once_the_delay_has_passed(self, db, sent_gateway):
        campaign, contacts = await build_campaign(db, hours_ago=48)
        rule = await add_rule(db, campaign, delay_minutes=24 * 60)

        result = await process_rule(db, rule)
        assert result["sent"] == 1
        assert len(sent_gateway) == 1
        # The follow-up is personalized like every other message.
        assert "C0" in sent_gateway[0]["body"]

    @pytest.mark.asyncio
    async def test_send_window_defers_out_of_hours(self, db):
        rule = CampaignFollowUp(
            campaign_id=1, send_start_hour=9, send_end_hour=17, delay_minutes=1
        )
        # Inside and outside a normal daytime window.
        assert within_send_window(rule, datetime(2026, 1, 5, 12, tzinfo=timezone.utc)) is True
        assert within_send_window(rule, datetime(2026, 1, 5, 3, tzinfo=timezone.utc)) is False

    @pytest.mark.asyncio
    async def test_window_wrapping_midnight(self, db):
        rule = CampaignFollowUp(campaign_id=1, send_start_hour=22, send_end_hour=6)
        assert within_send_window(rule, datetime(2026, 1, 5, 23, tzinfo=timezone.utc)) is True
        assert within_send_window(rule, datetime(2026, 1, 5, 4, tzinfo=timezone.utc)) is True
        assert within_send_window(rule, datetime(2026, 1, 5, 12, tzinfo=timezone.utc)) is False

    @pytest.mark.asyncio
    async def test_no_window_means_any_time(self):
        assert within_send_window(CampaignFollowUp(campaign_id=1)) is True


class TestStopConditions:
    @pytest.mark.asyncio
    async def test_a_reply_stops_the_followup(self, db, sent_gateway):
        campaign, contacts = await build_campaign(db, hours_ago=48)
        await reply_from(db, contacts[0])
        rule = await add_rule(db, campaign, stop_on_reply=True)

        result = await process_rule(db, rule)
        assert result["sent"] == 0
        assert result["stopped"] == 1
        assert sent_gateway == []

        log = (await db.execute(select(CampaignFollowUpLog))).scalars().first()
        assert log.status == "stopped"
        assert log.reason == "Contact replied"

    @pytest.mark.asyncio
    async def test_reply_before_the_campaign_message_does_not_stop_it(self, db, sent_gateway):
        """Only a reply to THIS message counts, not last month's conversation."""
        campaign, contacts = await build_campaign(db, hours_ago=48)
        await reply_from(db, contacts[0], when=datetime.now(timezone.utc) - timedelta(days=10))
        rule = await add_rule(db, campaign, stop_on_reply=True)

        result = await process_rule(db, rule)
        assert result["sent"] == 1

    @pytest.mark.asyncio
    async def test_stop_on_reply_disabled_still_sends(self, db, sent_gateway):
        campaign, contacts = await build_campaign(db, hours_ago=48)
        await reply_from(db, contacts[0])
        rule = await add_rule(db, campaign, stop_on_reply=False)

        result = await process_rule(db, rule)
        assert result["sent"] == 1

    @pytest.mark.asyncio
    async def test_opted_out_contact_is_never_messaged(self, db, sent_gateway):
        campaign, contacts = await build_campaign(db, hours_ago=48)
        contacts[0].is_opted_out = True
        await db.flush()
        rule = await add_rule(db, campaign)

        result = await process_rule(db, rule)
        assert result["sent"] == 0
        assert sent_gateway == []
        log = (await db.execute(select(CampaignFollowUpLog))).scalars().first()
        assert log.reason == "Contact opted out"

    @pytest.mark.asyncio
    async def test_suppressed_number_is_never_messaged(self, db, sent_gateway):
        campaign, contacts = await build_campaign(db, hours_ago=48)
        db.add(
            SuppressionEntry(
                phone_number=contacts[0].phone_number, reason="manual", source="manual"
            )
        )
        await db.flush()
        rule = await add_rule(db, campaign)

        result = await process_rule(db, rule)
        assert result["sent"] == 0
        assert sent_gateway == []

    @pytest.mark.asyncio
    async def test_lead_status_stop_condition(self, db, sent_gateway):
        campaign, contacts = await build_campaign(db, hours_ago=48)
        contacts[0].lead_status = "interested"
        await db.flush()
        rule = await add_rule(db, campaign, stop_on_lead_status="interested, customer")

        result = await process_rule(db, rule)
        assert result["sent"] == 0
        assert result["stopped"] == 1
        log = (await db.execute(select(CampaignFollowUpLog))).scalars().first()
        assert "interested" in log.reason

    @pytest.mark.asyncio
    async def test_paused_campaign_sends_nothing(self, db, sent_gateway):
        campaign, _ = await build_campaign(db, hours_ago=48, status="paused")
        rule = await add_rule(db, campaign)

        result = await process_rule(db, rule)
        assert result["sent"] == 0
        assert "paused" in result["reason"]
        assert sent_gateway == []

    @pytest.mark.asyncio
    async def test_rule_with_no_message_sends_nothing(self, db, sent_gateway):
        campaign, _ = await build_campaign(db, hours_ago=48)
        rule = await add_rule(db, campaign, message_text=None)

        result = await process_rule(db, rule)
        assert result["sent"] == 0
        assert result["reason"] == "Follow-up has no message"


class TestExactlyOnce:
    @pytest.mark.asyncio
    async def test_a_second_sweep_does_not_resend(self, db, sent_gateway):
        campaign, _ = await build_campaign(db, hours_ago=48)
        rule = await add_rule(db, campaign)

        first = await process_rule(db, rule)
        second = await process_rule(db, rule)

        assert first["sent"] == 1
        assert second["sent"] == 0
        assert len(sent_gateway) == 1

    @pytest.mark.asyncio
    async def test_a_stopped_contact_is_not_reconsidered(self, db, sent_gateway):
        campaign, contacts = await build_campaign(db, hours_ago=48)
        await reply_from(db, contacts[0])
        rule = await add_rule(db, campaign)

        await process_rule(db, rule)
        again = await process_rule(db, rule)
        assert again["sent"] == 0
        assert again["stopped"] == 0  # already logged, not re-counted
        assert sent_gateway == []


class TestChaining:
    @pytest.mark.asyncio
    async def test_step_two_only_follows_people_step_one_messaged(self, db, sent_gateway):
        campaign, contacts = await build_campaign(db, contacts=2, hours_ago=48)
        # One of the two replied, so step 1 stops them.
        await reply_from(db, contacts[1])

        step1 = await add_rule(db, campaign, step_order=1, delay_minutes=60)
        step2 = await add_rule(
            db, campaign, step_order=2, delay_minutes=1, message_text="Second nudge"
        )

        first = await process_rule(db, step1)
        assert first["sent"] == 1 and first["stopped"] == 1

        # Step 2 becomes due relative to the message step 1 just sent.
        message = (
            await db.execute(
                select(Message)
                .where(Message.contact_id == contacts[0].id, Message.direction == "outgoing")
                .order_by(Message.id.desc())
            )
        ).scalars().first()
        message.sent_at = datetime.now(timezone.utc) - timedelta(hours=2)
        message.created_at = message.sent_at
        await db.flush()

        second = await process_rule(db, step2)
        assert second["sent"] == 1
        # Only the contact who never replied got the second nudge.
        assert len(sent_gateway) == 2
        assert sent_gateway[1]["phone"] == contacts[0].phone_number

    @pytest.mark.asyncio
    async def test_step_two_waits_for_step_one(self, db, sent_gateway):
        campaign, _ = await build_campaign(db, hours_ago=48)
        await add_rule(db, campaign, step_order=1, delay_minutes=60)
        step2 = await add_rule(db, campaign, step_order=2, message_text="Second")

        # Step 1 has not run, so step 2 has nobody to follow.
        result = await process_rule(db, step2)
        assert result["sent"] == 0


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_failed_first_message_ends_the_chain(self, db):
        campaign, contacts = await build_campaign(db, hours_ago=48)
        message = (await db.execute(select(Message))).scalars().first()
        message.status = "failed"
        await db.flush()
        rule = await add_rule(db, campaign)
        cc = (await db.execute(select(CampaignContact))).scalars().first()

        decision, reason = await evaluate_contact(
            db, rule, campaign, cc, datetime.now(timezone.utc)
        )
        assert decision == "stop"
        assert "failed" in reason


class TestCampaignFollowUpAPI:
    @pytest.mark.asyncio
    async def test_create_list_and_delete(self, client, db):
        campaign, _ = await build_campaign(db, hours_ago=48)

        created = await client.post(
            f"/api/v1/campaign-followups/campaigns/{campaign.id}",
            json={
                "message_text": "Checking in {{first_name}}",
                "delay_minutes": 120,
                "stop_on_reply": True,
                "stop_on_lead_status": "interested",
            },
        )
        assert created.status_code == 201, created.text
        rule_id = created.json()["id"]
        assert created.json()["step_order"] == 1

        listed = await client.get(f"/api/v1/campaign-followups/campaigns/{campaign.id}")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        deleted = await client.delete(f"/api/v1/campaign-followups/{rule_id}")
        assert deleted.status_code == 204

    @pytest.mark.asyncio
    async def test_followup_without_message_is_rejected(self, client, db):
        campaign, _ = await build_campaign(db, hours_ago=1)
        response = await client.post(
            f"/api/v1/campaign-followups/campaigns/{campaign.id}",
            json={"delay_minutes": 60},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_steps_auto_increment(self, client, db):
        campaign, _ = await build_campaign(db, hours_ago=1)
        for expected in (1, 2, 3):
            response = await client.post(
                f"/api/v1/campaign-followups/campaigns/{campaign.id}",
                json={"message_text": f"Nudge {expected}", "delay_minutes": 60},
            )
            assert response.json()["step_order"] == expected

    @pytest.mark.asyncio
    async def test_dry_run_preview_reports_buckets(self, client, db, sent_gateway):
        campaign, contacts = await build_campaign(db, contacts=3, hours_ago=48)
        await reply_from(db, contacts[0])
        contacts[1].is_opted_out = True
        await db.flush()
        rule = await add_rule(db, campaign, delay_minutes=60)

        response = await client.get(f"/api/v1/campaign-followups/{rule.id}/preview")
        assert response.status_code == 200
        counts = response.json()["counts"]
        assert counts["send"] == 1
        assert counts["stop"] == 2
        # A dry run must not send anything.
        assert sent_gateway == []

    @pytest.mark.asyncio
    async def test_run_now_endpoint_sends(self, client, db, sent_gateway):
        campaign, _ = await build_campaign(db, hours_ago=48)
        rule = await add_rule(db, campaign, delay_minutes=60)

        response = await client.post(f"/api/v1/campaign-followups/{rule.id}/run")
        assert response.status_code == 200
        assert response.json()["sent"] == 1
        assert len(sent_gateway) == 1

    @pytest.mark.asyncio
    async def test_log_endpoint_explains_outcomes(self, client, db, sent_gateway):
        campaign, contacts = await build_campaign(db, contacts=2, hours_ago=48)
        await reply_from(db, contacts[0])
        rule = await add_rule(db, campaign, delay_minutes=60)
        await process_rule(db, rule)

        response = await client.get(f"/api/v1/campaign-followups/{rule.id}/log")
        assert response.status_code == 200
        statuses = {item["status"] for item in response.json()["items"]}
        assert statuses == {"sent", "stopped"}

    @pytest.mark.asyncio
    async def test_campaign_picker_lists_campaigns(self, client, db):
        campaign, _ = await build_campaign(db, hours_ago=1)
        await add_rule(db, campaign)
        response = await client.get("/api/v1/campaign-followups/campaigns")
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["name"] == campaign.name
        assert item["followup_count"] == 1
