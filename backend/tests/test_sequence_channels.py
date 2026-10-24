"""Email sequences — the channel parity the SMS builder used to have alone.

A sequence is channel-scoped like a campaign, an automation and an auto-reply
rule. These tests pin the three things that matter:

* the API stores and returns an email sequence with its per-step subject;
* a step from the other channel is refused, so an email sequence can never
  text somebody (and vice versa);
* an email campaign actually *runs* its sequence — before this, only the SMS
  branch consulted the snapshot, so an email sequence sent step one and stopped.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.campaign import Campaign, CampaignContact
from app.models.email import EmailAccount
from app.security.encryption import encrypt_value
from app.models.contact import Contact
from app.models.followup import FollowUp
from app.models.sequence import Sequence, SequenceStep, SequenceVersion
from app.models.user import User
from app.security.auth import get_current_user
from app.services.campaign_service import CampaignService
from app.services.sequence_service import message_config, snapshot_steps


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
        id=1,
        username="tester",
        email="tester@example.com",
        password_hash="x",
        role="admin",
        is_active=True,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _email_sequence_payload(name="Email intro sequence"):
    return {
        "name": name,
        "description": "First email, wait, then follow up",
        "channel": "email",
        "steps": [
            {
                "step_order": 0,
                "step_type": "send_email",
                "subject": "Quick question about {{business_name}}",
                "config": message_config("Hi {{first_name}}, I wanted to reach out."),
            },
            {"step_order": 1, "step_type": "wait", "wait_duration_hours": 48},
            {
                "step_order": 2,
                "step_type": "send_email",
                "subject": "Following up",
                "config": message_config("Hi {{first_name}}, just following up on my note."),
            },
            {"step_order": 3, "step_type": "stop"},
        ],
    }


@pytest.mark.asyncio
async def test_email_sequence_round_trips_with_subjects(client):
    created = await client.post("/api/v1/sequences/", json=_email_sequence_payload())
    assert created.status_code == 201, created.text

    detail = await client.get(f"/api/v1/sequences/{created.json()['id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["channel"] == "email"
    assert [step["step_type"] for step in body["steps"]] == [
        "send_email",
        "wait",
        "send_email",
        "stop",
    ]
    assert body["steps"][0]["subject"] == "Quick question about {{business_name}}"


@pytest.mark.asyncio
async def test_sms_and_email_sequences_are_listed_per_channel(client):
    await client.post("/api/v1/sequences/", json=_email_sequence_payload("Email one"))
    sms = await client.post(
        "/api/v1/sequences/",
        json={
            "name": "SMS one",
            "steps": [
                {
                    "step_order": 0,
                    "step_type": "send_sms",
                    "config": message_config("Hello"),
                },
                {"step_order": 1, "step_type": "stop"},
            ],
        },
    )
    assert sms.status_code == 201
    # The default is SMS, so existing callers and old rows keep working.
    assert (await client.get(f"/api/v1/sequences/{sms.json()['id']}")).json()["channel"] == "sms"

    email_list = await client.get("/api/v1/sequences/", params={"channel": "email"})
    sms_list = await client.get("/api/v1/sequences/", params={"channel": "sms"})
    assert [item["name"] for item in email_list.json()["items"]] == ["Email one"]
    assert [item["name"] for item in sms_list.json()["items"]] == ["SMS one"]


@pytest.mark.asyncio
async def test_email_sequence_refuses_an_sms_step(client):
    payload = _email_sequence_payload()
    payload["steps"][0]["step_type"] = "send_sms"
    response = await client.post("/api/v1/sequences/", json=payload)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "email sequence" in detail
    assert "send_email" in detail


@pytest.mark.asyncio
async def test_email_step_without_a_subject_is_refused(client):
    payload = _email_sequence_payload()
    payload["steps"][0]["subject"] = ""
    response = await client.post("/api/v1/sequences/", json=payload)
    # The schema rejects it before it can reach the database.
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_campaign_channel_must_match_the_sequence(db, monkeypatch):
    contact = Contact(phone_number="+2348030000001", email="lead@acme.example")
    sequence = Sequence(name="SMS only", channel="sms")
    db.add_all([contact, sequence])
    await db.flush()
    db.add_all(
        [
            SequenceStep(
                sequence_id=sequence.id,
                version=1,
                step_order=0,
                step_type="send_sms",
                config=message_config("Hello"),
            ),
            SequenceStep(
                sequence_id=sequence.id, version=1, step_order=1, step_type="stop"
            ),
        ]
    )
    campaign = Campaign(
        name="Email campaign with an SMS sequence",
        status="draft",
        channel="email",
        sequence_id=sequence.id,
        subject="Subject",
        message_body="Body",
    )
    db.add(campaign)
    await db.flush()

    async def gateway_ok(self, selected_campaign):
        return None

    monkeypatch.setattr(CampaignService, "_check_gateway", gateway_ok)
    with pytest.raises(ValueError) as excinfo:
        await CampaignService(db).validate_and_schedule(campaign.id)
    assert "SMS sequence" in str(excinfo.value)


@pytest.mark.asyncio
async def test_email_campaign_runs_its_sequence_and_schedules_the_next_step(db, monkeypatch):
    """An email campaign with a sequence must advance, not stop after step one."""
    from app.tasks import campaign_tasks

    contact = Contact(
        phone_number="+2348030000002", email="ada@acme.example", first_name="Ada"
    )
    account = EmailAccount(
        name="main",
        from_email="sender@acme.example",
        from_name="Acme",
        api_key_encrypted=encrypt_value("key-main"),
        is_default=True,
        is_active=True,
    )
    sequence = Sequence(name="Email follow-ups", channel="email")
    db.add_all([contact, account, sequence])
    await db.flush()
    steps = [
        SequenceStep(
            sequence_id=sequence.id,
            version=1,
            step_order=0,
            step_type="send_email",
            subject="Step one",
            config=message_config("Hi {{first_name}}, first note."),
        ),
        SequenceStep(
            sequence_id=sequence.id,
            version=1,
            step_order=1,
            step_type="wait",
            wait_duration_hours=24,
        ),
        SequenceStep(
            sequence_id=sequence.id,
            version=1,
            step_order=2,
            step_type="send_email",
            subject="Step two",
            config=message_config("Hi {{first_name}}, second note."),
        ),
        SequenceStep(
            sequence_id=sequence.id, version=1, step_order=3, step_type="stop"
        ),
    ]
    db.add_all(steps)
    await db.flush()
    version = SequenceVersion(
        sequence_id=sequence.id, version=1, snapshot=snapshot_steps(steps)
    )
    db.add(version)
    await db.flush()

    campaign = Campaign(
        name="Email campaign",
        status="running",
        channel="email",
        sequence_id=sequence.id,
        sequence_version_id=version.id,
        subject="Campaign subject",
        message_body="Campaign body",
    )
    db.add(campaign)
    await db.flush()
    cc = CampaignContact(
        campaign_id=campaign.id, contact_id=contact.id, status="pending", sequence_step=0
    )
    db.add(cc)
    await db.flush()

    outbox: list[int] = []
    await campaign_tasks._process_campaign_contact(db, campaign, cc, outbox)
    await db.flush()
    assert outbox, "the first step produced a message"

    # The step's own subject and body were used, not the campaign's letter.
    from app.models.conversation import Message

    message = (
        await db.execute(select(Message).where(Message.campaign_id == campaign.id))
    ).scalars().first()
    assert message is not None
    assert message.channel == "email"
    assert message.subject == "Step one"
    assert "first note" in (message.body or "")

    # And the sequence continued: a visible, channel-aware follow-up is queued.
    followup = (
        await db.execute(select(FollowUp).where(FollowUp.campaign_contact_id == cc.id))
    ).scalars().first()
    assert followup is not None
    assert followup.channel == "email"
    assert followup.subject == "Step two"
    assert "second note" in (followup.message_text or "")
