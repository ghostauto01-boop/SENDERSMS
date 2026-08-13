"""End-to-end sequence builder and sequence-to-follow-up workflow tests."""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.campaign import Campaign, CampaignContact
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.conversation import Message
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


def _sequence_payload(name="Lead follow-up"):
    return {
        "name": name,
        "description": "Initial text, wait, then follow up",
        "steps": [
            {
                "step_order": 0,
                "step_type": "send_sms",
                "config": message_config("Hello {{first_name}}"),
            },
            {
                "step_order": 1,
                "step_type": "wait",
                "wait_duration_hours": 24,
            },
            {
                "step_order": 2,
                "step_type": "send_sms",
                "config": message_config("Hi {{first_name}}, just following up."),
            },
            {"step_order": 3, "step_type": "stop"},
        ],
    }


@pytest.mark.asyncio
async def test_sequence_api_returns_steps_and_supports_versioned_edit(client, db):
    created = await client.post("/api/v1/sequences/", json=_sequence_payload())
    assert created.status_code == 201
    sequence_id = created.json()["id"]

    listing = await client.get("/api/v1/sequences/", params={"per_page": 100})
    assert listing.status_code == 200
    item = listing.json()["items"][0]
    assert item["id"] == sequence_id
    assert item["current_version"] == 1
    assert [step["step_type"] for step in item["steps"]] == [
        "send_sms", "wait", "send_sms", "stop"
    ]
    assert "just following up" in item["steps"][2]["config"]

    updated_payload = _sequence_payload("Updated sequence")
    updated_payload["steps"][2]["config"] = message_config("Second version follow-up")
    updated = await client.put(
        f"/api/v1/sequences/{sequence_id}", json=updated_payload
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    detail = await client.get(f"/api/v1/sequences/{sequence_id}")
    assert detail.json()["name"] == "Updated sequence"
    assert detail.json()["current_version"] == 2
    assert "Second version" in detail.json()["steps"][2]["config"]
    versions = (
        await db.execute(
            select(SequenceVersion).where(SequenceVersion.sequence_id == sequence_id)
        )
    ).scalars().all()
    assert len(versions) == 1
    assert "just following up" in versions[0].snapshot


@pytest.mark.asyncio
async def test_sequence_rejects_send_step_without_message(client):
    payload = _sequence_payload()
    payload["steps"][0]["config"] = None
    response = await client.post("/api/v1/sequences/", json=payload)
    assert response.status_code == 400
    assert "must have a written message or template" in response.json()["detail"]


@pytest.mark.asyncio
async def test_sequence_rejects_backward_condition_branch(client):
    payload = _sequence_payload()
    payload["steps"].insert(
        2,
        {
            "step_order": 2,
            "step_type": "condition",
            "condition_type": "contact_replied",
            "true_branch_step_order": 0,
            "false_branch_step_order": 3,
        },
    )
    payload["steps"][3]["step_order"] = 3
    payload["steps"][4]["step_order"] = 4
    response = await client.post("/api/v1/sequences/", json=payload)
    assert response.status_code == 400
    assert "must point to a later step" in response.json()["detail"]


@pytest.mark.asyncio
async def test_campaign_validation_snapshots_sequence_and_clears_stale_snapshot(db, monkeypatch):
    contact = Contact(phone_number="+2348031234567", first_name="Ada")
    contact_list = ContactList(name="Leads")
    sequence = Sequence(name="Validated sequence")
    db.add_all([contact, contact_list, sequence])
    await db.flush()
    db.add(ContactListMember(list_id=contact_list.id, contact_id=contact.id))
    steps = [
        SequenceStep(
            sequence_id=sequence.id,
            version=1,
            step_order=0,
            step_type="send_sms",
            config=message_config("Sequence hello"),
        ),
        SequenceStep(
            sequence_id=sequence.id,
            version=1,
            step_order=1,
            step_type="stop",
        ),
    ]
    db.add_all(steps)
    campaign = Campaign(
        name="Uses sequence",
        status="draft",
        list_id=contact_list.id,
        sequence_id=sequence.id,
    )
    db.add(campaign)
    await db.flush()

    async def gateway_ok(self, selected_campaign):
        return None

    monkeypatch.setattr(CampaignService, "_check_gateway", gateway_ok)
    service = CampaignService(db)
    await service.validate_and_schedule(campaign.id)
    assert campaign.status == "scheduled"
    assert campaign.sequence_version_id is not None
    snapshot_id = campaign.sequence_version_id
    snapshot = (
        await db.execute(
            select(SequenceVersion).where(SequenceVersion.id == snapshot_id)
        )
    ).scalar_one()
    assert "Sequence hello" in snapshot.snapshot

    # Simulate editing the validated campaign back to a normal one-message
    # campaign, then revalidating. The old snapshot must not run.
    campaign.status = "draft"
    campaign.sequence_id = None
    campaign.message_body = "One message only"
    await service.validate_and_schedule(campaign.id)
    assert campaign.sequence_version_id is None


@pytest.mark.asyncio
async def test_sequence_wait_creates_and_executes_visible_followup(tmp_path, monkeypatch):
    """Initial SMS -> wait -> follow-up SMS works in no-worker deployment mode."""
    import app.providers.smsgate as smsgate
    import app.tasks.campaign_tasks as campaign_tasks

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/sequence.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as setup:
        contact = Contact(phone_number="+2348031234567", first_name="Ada")
        sequence = Sequence(name="Two messages")
        setup.add_all([contact, sequence])
        await setup.flush()
        steps = [
            SequenceStep(
                sequence_id=sequence.id,
                version=1,
                step_order=0,
                step_type="send_sms",
                config=message_config("Hello {{first_name}}"),
            ),
            SequenceStep(
                sequence_id=sequence.id,
                version=1,
                step_order=1,
                step_type="wait",
                wait_duration_hours=1,
            ),
            SequenceStep(
                sequence_id=sequence.id,
                version=1,
                step_order=2,
                step_type="send_sms",
                config=message_config("Hi {{first_name}}, following up."),
            ),
            SequenceStep(
                sequence_id=sequence.id,
                version=1,
                step_order=3,
                step_type="stop",
            ),
        ]
        setup.add_all(steps)
        await setup.flush()
        version = SequenceVersion(
            sequence_id=sequence.id,
            version=1,
            snapshot=snapshot_steps(steps),
        )
        setup.add(version)
        await setup.flush()
        campaign = Campaign(
            name="Sequence campaign",
            status="running",
            sequence_id=sequence.id,
            sequence_version_id=version.id,
        )
        setup.add(campaign)
        await setup.flush()
        campaign_contact = CampaignContact(
            campaign_id=campaign.id,
            contact_id=contact.id,
            status="pending",
            sequence_step=0,
        )
        setup.add(campaign_contact)
        await setup.commit()
        campaign_id = campaign.id

    gateway_calls = []

    async def fake_send(phone, body, sim_number):
        gateway_calls.append((phone, body, sim_number))
        return {
            "success": True,
            "provider_message_id": f"sim-{len(gateway_calls)}",
            "raw": {},
        }

    monkeypatch.setattr(campaign_tasks, "async_session_factory", factory)
    # _send_one imports the same shared factory from sms_tasks.
    import app.tasks.sms_tasks as sms_tasks
    monkeypatch.setattr(sms_tasks, "async_session_factory", factory)
    monkeypatch.setattr(smsgate, "send_sms_direct", fake_send)

    assert await campaign_tasks.process_campaign_batch_async(
        campaign_id, send_inline=True
    ) == 1

    async with factory() as check:
        followup = (await check.execute(select(FollowUp))).scalar_one()
        first_message = (await check.execute(select(Message))).scalar_one()
        assert first_message.status == "sent"
        assert first_message.body == "Hello Ada"
        assert followup.status == "pending"
        assert followup.sequence_step_order == 2
        assert followup.message_text == "Hi {{first_name}}, following up."
        # Make the one-hour wait due without sleeping in the test.
        followup.scheduled_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await check.commit()

    assert await campaign_tasks.process_due_followups_async(
        send_inline=True
    ) != []
    assert await campaign_tasks.process_due_followups_async(send_inline=True) == []

    async with factory() as check:
        messages = (
            await check.execute(select(Message).order_by(Message.id))
        ).scalars().all()
        saved_followup = (await check.execute(select(FollowUp))).scalar_one()
        campaign_contact = (
            await check.execute(select(CampaignContact))
        ).scalar_one()
        saved_campaign = (await check.execute(select(Campaign))).scalar_one()
        assert [message.body for message in messages] == [
            "Hello Ada", "Hi Ada, following up."
        ]
        assert all(message.status == "sent" for message in messages)
        assert saved_followup.status == "sent"
        assert campaign_contact.sequence_step == 3
        assert saved_campaign.messages_sent == 2

    assert [call[1] for call in gateway_calls] == [
        "Hello Ada", "Hi Ada, following up."
    ]
    await engine.dispose()
