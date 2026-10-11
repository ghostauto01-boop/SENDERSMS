"""Follow-up steps: the API contract and the sending engine behind it.

Covers the P0 fixes (delay_hours / action / subject / body were ignored, no
PATCH, no DELETE, no validation, and ``process_followups`` raised NameError so
NO follow-up ever went out) and the P1 features (subject line, chained steps,
conditions beyond ``no_reply``, ``{{token}}`` personalization and the reusable
template library).

The engine tests use the same fixtures/helpers as ``test_ads_manager``.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.ads import (
    AdsAssignment,
    AdsCampaign,
    AdsFollowUpStep,
    AdsFollowUpTask,
    AdsFollowUpTemplate,
)
from app.models.conversation import Conversation, Message
from app.services import ads_service as svc
from tests.campaign_factory import make_account
from tests.test_ads_manager import (  # noqa: F401 - helpers reused on purpose
    make_campaign,
    make_contacts,
    make_creatives,
    make_list,
    make_set,
)


@pytest_asyncio.fixture
async def db():
    """Engine-level session (mirrors test_ads_manager's fixture)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def make_api_campaign(api_db, *, channel="email", subject="Quick question", **kw):
    campaign = AdsCampaign(
        name=kw.pop("name", f"{channel} campaign"),
        channel=channel,
        status=kw.pop("status", "draft"),
        subject=subject,
        **kw,
    )
    api_db.add(campaign)
    await api_db.commit()
    await api_db.refresh(campaign)
    return campaign


# ==========================================================================
# Bug 1 / 2 / 3 / 6 -- creation honours the request and validates it
# ==========================================================================


@pytest.mark.asyncio
async def test_create_uses_the_requested_delay_hours(api_client, api_db):
    campaign = await make_api_campaign(api_db)

    resp = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps",
        json={
            "campaign_id": campaign.id,
            "step_order": 1,
            "name": "24h follow-up",
            "delay_hours": 24,
            "condition": "no_reply",
            "action": "send_email",
            "subject": "Re: {{business_name}} — quick follow-up",
            "body": "Hi {{contact}}, just following up on my note yesterday.",
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["wait_hours"] == 24
    assert body["delay_hours"] == 24
    assert body["name"] == "24h follow-up"
    assert body["step_order"] == 1
    assert body["is_active"] is True

    # ...and the row itself says 24, not the old hardcoded 48.
    step = (await api_db.execute(select(AdsFollowUpStep))).scalars().one()
    assert step.wait_hours == 24


@pytest.mark.asyncio
async def test_create_stores_action_subject_and_body(api_client, api_db):
    campaign = await make_api_campaign(api_db)

    resp = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps",
        json={
            "campaign_id": campaign.id,
            "action": "send_email",
            "subject": "Quick question",
            "body": "Hi {{contact}}, still interested?",
        },
    )

    assert resp.status_code == 200, resp.text
    step = (await api_db.execute(select(AdsFollowUpStep))).scalars().one()
    assert step.action == "send_email"
    assert step.subject == "Quick question"
    assert step.body == "Hi {{contact}}, still interested?"
    assert resp.json()["body"] == step.body


@pytest.mark.asyncio
async def test_default_action_follows_the_campaign_channel(api_client, api_db):
    email_campaign = await make_api_campaign(api_db, channel="email")
    sms_campaign = await make_api_campaign(api_db, channel="sms")

    email_step = await api_client.post(
        f"/api/v1/ads/campaigns/{email_campaign.id}/followup-steps",
        json={"body": "email nudge"},
    )
    sms_step = await api_client.post(
        f"/api/v1/ads/campaigns/{sms_campaign.id}/followup-steps",
        json={"body": "sms nudge"},
    )

    assert email_step.json()["action"] == "send_email"
    assert sms_step.json()["action"] == "send_sms"


@pytest.mark.asyncio
async def test_default_delay_is_two_days_when_not_sent(api_client, api_db):
    campaign = await make_api_campaign(api_db)
    resp = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps", json={"body": "hi"}
    )
    assert resp.json()["delay_hours"] == 48


@pytest.mark.asyncio
async def test_wait_hours_is_accepted_as_an_alias(api_client, api_db):
    campaign = await make_api_campaign(api_db)
    resp = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps",
        json={"wait_hours": 6, "body": "hi"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["wait_hours"] == 6
    assert resp.json()["delay_hours"] == 6


@pytest.mark.asyncio
async def test_conflicting_wait_spellings_are_rejected(api_client, api_db):
    campaign = await make_api_campaign(api_db)
    resp = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps",
        json={"delay_hours": 24, "wait_hours": 48, "body": "hi"},
    )
    assert resp.status_code == 422
    assert "disagree" in resp.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"action": "send_whatsapp"},          # not an allowed action
        {"action": "delete_contact"},
        {"condition": "whenever"},            # not an allowed condition
        {"delay_hours": -1},                  # negative
        {"delay_hours": 999_999},             # absurdly large
        {"wait_hours": -3},
        {"step_order": 0},
    ],
)
async def test_invalid_fields_are_a_422(api_client, api_db, payload):
    campaign = await make_api_campaign(api_db)
    resp = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps", json=payload
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    # The message names the offending field so the caller can fix it.
    assert any(name in body["detail"] for name in payload)
    # Nothing was written.
    assert (await api_db.execute(select(AdsFollowUpStep))).scalars().first() is None


# ==========================================================================
# Bug 4 / 5 -- edit and delete after creation
# ==========================================================================


@pytest.mark.asyncio
async def test_patch_edits_a_step(api_client, api_db):
    campaign = await make_api_campaign(api_db)
    created = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps",
        json={"delay_hours": 24, "body": "first", "subject": "one", "condition": "no_reply"},
    )
    step_id = created.json()["id"]

    resp = await api_client.patch(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps/{step_id}",
        json={
            "delay_hours": 48,
            "subject": "Quick question",
            "body": "Hi {{contact}}, ...",
            "condition": "opened_no_click",
            "is_active": False,
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["delay_hours"] == 48 and body["wait_hours"] == 48
    assert body["subject"] == "Quick question"
    assert body["body"] == "Hi {{contact}}, ..."
    assert body["condition"] == "opened_no_click"
    assert body["is_active"] is False

    step = (await api_db.execute(select(AdsFollowUpStep))).scalars().one()
    assert step.wait_hours == 48
    assert step.is_active is False


@pytest.mark.asyncio
async def test_patch_rejects_a_step_from_another_campaign(api_client, api_db):
    campaign = await make_api_campaign(api_db)
    other = await make_api_campaign(api_db, name="Other")
    created = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps", json={"body": "hi"}
    )

    resp = await api_client.patch(
        f"/api/v1/ads/campaigns/{other.id}/followup-steps/{created.json()['id']}",
        json={"delay_hours": 5},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_soft_deletes_and_cancels_pending_tasks(api_client, api_db):
    campaign = await make_api_campaign(api_db)
    created = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps", json={"body": "hi"}
    )
    step_id = created.json()["id"]
    api_db.add(
        AdsFollowUpTask(
            campaign_id=campaign.id, step_id=step_id, contact_id=7,
            due_at=datetime.now(timezone.utc), status="pending",
        )
    )
    await api_db.commit()

    resp = await api_client.delete(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps/{step_id}"
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "ok": True,
        "deleted": True,
        "step_id": step_id,
        "campaign_id": campaign.id,
        "soft_deleted": True,
        "is_active": False,
        "pending_tasks_cancelled": 1,
    }

    step = (await api_db.execute(select(AdsFollowUpStep))).scalars().one()
    assert step.is_active is False and step.deleted_at is not None

    task = (await api_db.execute(select(AdsFollowUpTask))).scalars().one()
    assert task.status == "cancelled"

    # The removed step is gone from the campaign, but its history remains.
    listing = await api_client.get(f"/api/v1/ads/campaigns/{campaign.id}/followup-steps")
    assert listing.json()["total"] == 0
    detail = await api_client.get(f"/api/v1/ads/followup-steps/{step_id}")
    assert detail.status_code == 200
    assert detail.json()["is_active"] is False


@pytest.mark.asyncio
async def test_deleted_step_disappears_from_the_campaign_detail(api_client, api_db):
    campaign = await make_api_campaign(api_db)
    created = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps", json={"body": "hi"}
    )
    await api_client.delete(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps/{created.json()['id']}"
    )
    detail = await api_client.get(f"/api/v1/ads/campaigns/{campaign.id}")
    assert detail.json()["followup_steps"] == []


# ==========================================================================
# List / detail endpoints
# ==========================================================================


@pytest.mark.asyncio
async def test_list_returns_every_step_in_order(api_client, api_db):
    campaign = await make_api_campaign(api_db)
    for order in (2, 1, 3):
        await api_client.post(
            f"/api/v1/ads/campaigns/{campaign.id}/followup-steps",
            json={"step_order": order, "body": f"step {order}"},
        )

    resp = await api_client.get(f"/api/v1/ads/campaigns/{campaign.id}/followup-steps")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert [s["step_order"] for s in body["items"]] == [1, 2, 3]
    assert [s["body"] for s in body["items"]] == ["step 1", "step 2", "step 3"]
    # Every step carries both spellings of the wait.
    assert all(s["delay_hours"] == s["wait_hours"] for s in body["items"])


@pytest.mark.asyncio
async def test_step_order_is_appended_when_omitted(api_client, api_db):
    campaign = await make_api_campaign(api_db)
    await api_client.post(f"/api/v1/ads/campaigns/{campaign.id}/followup-steps", json={"body": "a"})
    second = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps", json={"body": "b"}
    )
    assert second.json()["step_order"] == 2


@pytest.mark.asyncio
async def test_get_one_step_returns_full_details(api_client, api_db):
    campaign = await make_api_campaign(api_db)
    created = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps",
        json={"body": "hi {{contact}}", "subject": "s", "delay_hours": 12},
    )
    resp = await api_client.get(f"/api/v1/ads/followup-steps/{created.json()['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["delay_hours"] == 12
    assert body["body"] == "hi {{contact}}"
    assert body["subject"] == "s"
    assert body["campaign_id"] == campaign.id
    assert body["pending_tasks"] == 0

    missing = await api_client.get("/api/v1/ads/followup-steps/424242")
    assert missing.status_code == 404


# ==========================================================================
# Feature 5 -- reusable template library
# ==========================================================================


@pytest.mark.asyncio
async def test_template_library_crud_and_use(api_client, api_db):
    create = await api_client.post(
        "/api/v1/ads/followup-templates",
        json={
            "name": "Gentle 24h nudge",
            "channel": "email",
            "subject": "Re: {{business_name}}",
            "body": "Hi {{contact}}, did my last note land?",
            "condition": "no_reply",
            "action": "send_email",
            "delay_hours": 24,
        },
    )
    assert create.status_code == 201, create.text
    template = create.json()
    assert template["delay_hours"] == 24 and template["usage_count"] == 0

    listing = await api_client.get("/api/v1/ads/followup-templates")
    assert listing.json()["total"] == 1

    campaign = await make_api_campaign(api_db)
    step = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps",
        json={"template_id": template["id"]},
    )
    assert step.status_code == 200, step.text
    body = step.json()
    assert body["template_id"] == template["id"]
    assert body["delay_hours"] == 24
    assert body["action"] == "send_email"
    assert body["subject"] == "Re: {{business_name}}"
    assert body["body"] == "Hi {{contact}}, did my last note land?"

    # An explicit field on the request still wins over the template.
    override = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps",
        json={"template_id": template["id"], "delay_hours": 72, "condition": "always"},
    )
    assert override.json()["delay_hours"] == 72
    assert override.json()["condition"] == "always"

    # The template learned it was used.
    refreshed = await api_client.get(f"/api/v1/ads/followup-templates/{template['id']}")
    assert refreshed.json()["usage_count"] == 2

    await api_client.patch(
        f"/api/v1/ads/followup-templates/{template['id']}", json={"body": "updated body"}
    )
    after = await api_client.get(f"/api/v1/ads/followup-templates/{template['id']}")
    assert after.json()["body"] == "updated body"

    deleted = await api_client.delete(f"/api/v1/ads/followup-templates/{template['id']}")
    assert deleted.status_code == 200
    gone = await api_client.get("/api/v1/ads/followup-templates")
    assert gone.json()["total"] == 0


@pytest.mark.asyncio
async def test_unknown_template_id_is_a_422(api_client, api_db):
    campaign = await make_api_campaign(api_db)
    resp = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps", json={"template_id": 999}
    )
    assert resp.status_code == 422
    assert "999" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_invalid_template_is_a_422(api_client, api_db):
    resp = await api_client.post(
        "/api/v1/ads/followup-templates",
        json={"name": "x", "body": "y", "condition": "nonsense"},
    )
    assert resp.status_code == 422


# ==========================================================================
# The engine: sending, chaining, conditions, personalization
# ==========================================================================


async def _campaign_with_steps(db, steps, *, channel="sms", test_mode=False):
    contacts = await make_contacts(db, 1)
    lst = await make_list(db, contacts)
    campaign = await make_campaign(db, test_mode=test_mode, status="active", channel=channel)
    ads_set = await make_set(db, campaign, lst)
    await make_creatives(db, ads_set, ["A"])
    for spec in steps:
        db.add(AdsFollowUpStep(campaign_id=campaign.id, **spec))
    await db.flush()
    await svc.build_audience(db, campaign)
    await svc.dispatch_campaign(db, campaign, limit=10)
    return campaign, contacts[0]


@pytest.mark.asyncio
async def test_a_due_followup_actually_sends(db):
    """The old engine raised NameError here, so no follow-up ever went out."""
    campaign, contact = await _campaign_with_steps(
        db,
        [dict(step_order=1, wait_hours=0, condition="always", action="send_sms", body="Ping")],
    )

    totals = await svc.process_followups(db)

    assert totals["sent"] == 1
    message = (
        await db.execute(
            select(Message).where(
                Message.contact_id == contact.id, Message.direction == "outgoing"
            )
        )
    ).scalars().all()
    assert any(m.body == "Ping" for m in message)
    task = (await db.execute(select(AdsFollowUpTask))).scalars().one()
    assert task.status == "completed"


@pytest.mark.asyncio
async def test_two_steps_chain_in_order_on_their_own_conditions(db):
    campaign, contact = await _campaign_with_steps(
        db,
        [
            dict(step_order=1, wait_hours=0, condition="always", action="send_sms", body="Step one"),
            dict(step_order=2, wait_hours=0, condition="always", action="send_sms", body="Step two"),
        ],
    )

    # Only the first step is queued up front.
    tasks = list((await db.execute(select(AdsFollowUpTask))).scalars().all())
    assert len(tasks) == 1
    assert tasks[0].body == "Step one"

    first = await svc.process_followups(db)
    assert first["sent"] == 1

    # Step two is queued only now that step one is out.
    tasks = list(
        (await db.execute(select(AdsFollowUpTask).order_by(AdsFollowUpTask.id))).scalars().all()
    )
    assert [t.status for t in tasks] == ["completed", "pending"]

    second = await svc.process_followups(db)
    assert second["sent"] == 1

    sent = (
        await db.execute(
            select(Message.body)
            .where(Message.contact_id == contact.id, Message.direction == "outgoing")
            .order_by(Message.id)
        )
    ).scalars().all()
    assert list(sent)[-2:] == ["Step one", "Step two"]


@pytest.mark.asyncio
async def test_a_reply_ends_the_chain(db):
    campaign, contact = await _campaign_with_steps(
        db,
        [
            dict(step_order=1, wait_hours=0, condition="always", action="send_sms", body="Step one"),
            dict(step_order=2, wait_hours=0, condition="no_reply", action="send_sms", body="Step two"),
        ],
    )
    await svc.process_followups(db)

    conversation = await _sms_thread(db, contact)
    db.add(
        Message(
            conversation_id=conversation.id, contact_id=contact.id, direction="incoming",
            body="No thanks", status="delivered", idempotency_key="in-1",
        )
    )
    await db.flush()

    totals = await svc.process_followups(db)
    assert totals["sent"] == 0
    assert totals["cancelled"] == 1
    task = (
        await db.execute(select(AdsFollowUpTask).where(AdsFollowUpTask.status == "cancelled"))
    ).scalars().first()
    assert "no_reply" in (task.note or "")


@pytest.mark.asyncio
async def test_deleted_step_never_sends(db):
    campaign, contact = await _campaign_with_steps(
        db,
        [dict(step_order=1, wait_hours=0, condition="always", action="send_sms", body="Ping")],
    )
    step = (await db.execute(select(AdsFollowUpStep))).scalars().one()
    step.is_active = False
    step.deleted_at = datetime.now(timezone.utc)
    await db.flush()

    totals = await svc.process_followups(db)
    assert totals["sent"] == 0
    task = (await db.execute(select(AdsFollowUpTask))).scalars().one()
    assert task.status == "cancelled"
    assert task.note == "Step removed"


@pytest.mark.asyncio
async def test_send_email_action_uses_the_email_channel(db):
    """A send_email step on an SMS campaign still goes out by email."""
    account = await make_account(db, default=True)
    contacts = await make_contacts(db, 1)
    contacts[0].email = "ada@adafoods.ng"
    contacts[0].first_name = "Ada"
    contacts[0].business_name = "Ada Foods"
    await db.flush()

    campaign = await make_campaign(db, test_mode=False, status="active", channel="sms")
    campaign.email_account_id = account.id
    await db.flush()
    message_id = await svc.send_adhoc_sms(
        db,
        contacts[0],
        "Hi {{contact}}, still keen?",
        campaign_id=campaign.id,
        subject="Re: {{business_name}}",
        channel="email",
    )
    message = (await db.execute(select(Message).where(Message.id == message_id))).scalar_one()
    assert message.channel == "email"
    assert message.subject == "Re: Ada Foods"
    assert message.body == "Hi Ada, still keen?"


@pytest.mark.asyncio
async def test_sms_followup_personalizes_tokens(db):
    campaign, contact = await _campaign_with_steps(
        db,
        [
            dict(
                step_order=1, wait_hours=0, condition="always", action="send_sms",
                body="Hi {{contact}} at {{business_name}}!",
            )
        ],
    )
    contact.first_name = "Ada"
    contact.business_name = "Ada Foods"
    await db.flush()

    await svc.process_followups(db)

    message = (
        await db.execute(
            select(Message)
            .where(Message.contact_id == contact.id, Message.direction == "outgoing")
            .order_by(Message.id)
        )
    ).scalars().all()
    assert message[-1].body == "Hi Ada at Ada Foods!"


async def _sms_thread(db, contact):
    """The contact's existing SMS thread (conversations are unique per channel)."""
    conversation = (
        await db.execute(
            select(Conversation).where(
                Conversation.contact_id == contact.id, Conversation.channel == "sms"
            )
        )
    ).scalars().first()
    if conversation is None:
        conversation = Conversation(contact_id=contact.id, status="active", channel="sms")
        db.add(conversation)
        await db.flush()
    return conversation


async def _step_message(db, campaign, contact, *, opened=0, clicked=0):
    """Put an outgoing message with engagement counters before a step."""
    conversation = await _sms_thread(db, contact)
    message = Message(
        conversation_id=conversation.id, contact_id=contact.id, direction="outgoing",
        body="previous", status="delivered", ads_campaign_id=campaign.id,
        open_count=opened, click_count=clicked,
        opened_at=datetime.now(timezone.utc) if opened else None,
        clicked_at=datetime.now(timezone.utc) if clicked else None,
        idempotency_key=f"prev-{opened}-{clicked}",
    )
    db.add(message)
    await db.flush()
    return message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "condition,opened,clicked,expected",
    [
        ("opened_no_click", 1, 0, True),
        ("opened_no_click", 1, 1, False),
        ("opened_no_click", 0, 0, False),
        ("clicked_no_reply", 0, 1, True),
        ("clicked_no_reply", 1, 0, False),
        ("link_clicked", 0, 1, True),
        ("link_not_clicked", 0, 0, True),
        ("always", 0, 0, True),
        ("no_reply", 0, 0, True),
    ],
)
async def test_engagement_conditions(db, condition, opened, clicked, expected):
    campaign, contact = await _campaign_with_steps(
        db,
        [dict(step_order=1, wait_hours=0, condition=condition, action="send_sms", body="x")],
    )
    await _step_message(db, campaign, contact, opened=opened, clicked=clicked)
    step = (await db.execute(select(AdsFollowUpStep))).scalars().one()
    assignment = (await db.execute(select(AdsAssignment))).scalars().first()

    decision = await svc._condition_met(
        db, step, assignment, contact, campaign_id=campaign.id
    )
    assert decision is expected


@pytest.mark.asyncio
async def test_negative_reply_condition_uses_the_classifier(db):
    campaign, contact = await _campaign_with_steps(
        db,
        [dict(step_order=1, wait_hours=0, condition="negative_reply", action="send_sms", body="x")],
    )
    await _step_message(db, campaign, contact)
    step = (await db.execute(select(AdsFollowUpStep))).scalars().one()
    assignment = (await db.execute(select(AdsAssignment))).scalars().first()

    # Nothing said yet -> the "not interested" branch is not taken.
    assert not await svc._condition_met(db, step, assignment, contact, campaign_id=campaign.id)

    conversation = await _sms_thread(db, contact)
    db.add(
        Message(
            conversation_id=conversation.id, contact_id=contact.id, direction="incoming",
            body="Not interested, please remove me", status="delivered",
            ai_sentiment="negative", idempotency_key="neg-1",
        )
    )
    await db.flush()

    assert await svc._condition_met(db, step, assignment, contact, campaign_id=campaign.id)

    step.condition = "positive_reply"
    await db.flush()
    assert not await svc._condition_met(db, step, assignment, contact, campaign_id=campaign.id)


@pytest.mark.asyncio
async def test_step_without_a_body_is_cancelled_not_silently_dropped(db):
    campaign, contact = await _campaign_with_steps(
        db,
        [dict(step_order=1, wait_hours=0, condition="always", action="send_sms", body=None)],
    )
    totals = await svc.process_followups(db)
    assert totals["sent"] == 0
    task = (await db.execute(select(AdsFollowUpTask))).scalars().one()
    assert task.status == "cancelled"
    assert task.note == "Step has no message body"


@pytest.mark.asyncio
async def test_a_24h_step_is_not_due_yet(db):
    campaign, contact = await _campaign_with_steps(
        db,
        [dict(step_order=1, wait_hours=24, condition="always", action="send_sms", body="Ping")],
    )
    task = (await db.execute(select(AdsFollowUpTask))).scalars().one()
    assert svc.as_utc(task.due_at) > svc.now_utc() + timedelta(hours=20)
    totals = await svc.process_followups(db)
    assert totals["sent"] == 0  # waiting, not sent


@pytest.mark.asyncio
async def test_an_accidental_delete_can_be_undone(api_client, api_db):
    campaign = await make_api_campaign(api_db)
    created = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps", json={"body": "hi"}
    )
    step_id = created.json()["id"]
    url = f"/api/v1/ads/campaigns/{campaign.id}/followup-steps/{step_id}"
    await api_client.delete(url)

    # Editing a removed step is a 404; asking for it back restores it.
    assert (await api_client.patch(url, json={"delay_hours": 5})).status_code == 404
    restored = await api_client.patch(url, json={"is_active": True})
    assert restored.status_code == 200, restored.text
    assert restored.json()["is_active"] is True
    step = (await api_db.execute(select(AdsFollowUpStep))).scalars().one()
    assert step.deleted_at is None and step.is_active is True
    listing = await api_client.get(f"/api/v1/ads/campaigns/{campaign.id}/followup-steps")
    assert listing.json()["total"] == 1


@pytest.mark.asyncio
async def test_patch_moves_the_followups_already_queued(api_client, api_db):
    """Editing the wait takes effect on the pending work, not just the row."""
    campaign = await make_api_campaign(api_db, channel="sms")
    created = await api_client.post(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps",
        json={"delay_hours": 24, "body": "Ping"},
    )
    step_id = created.json()["id"]
    api_db.add(
        AdsFollowUpTask(
            campaign_id=campaign.id, step_id=step_id, contact_id=5,
            due_at=datetime.now(timezone.utc) + timedelta(hours=24), status="pending",
        )
    )
    await api_db.commit()

    resp = await api_client.patch(
        f"/api/v1/ads/campaigns/{campaign.id}/followup-steps/{step_id}",
        json={"delay_hours": 2},
    )
    assert resp.status_code == 200, resp.text

    api_db.expire_all()
    task = (await api_db.execute(select(AdsFollowUpTask))).scalars().one()
    assert svc.as_utc(task.due_at) < svc.now_utc() + timedelta(hours=3)
