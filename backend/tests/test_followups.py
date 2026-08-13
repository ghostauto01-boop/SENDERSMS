"""API coverage for manually creating and listing follow-ups."""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.campaign import Campaign, CampaignContact
from app.models.contact import Contact
from app.models.followup import FollowUp
from app.models.user import User
from app.security.auth import get_current_user


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


async def _contact(db, *, opted_out=False):
    contact = Contact(
        phone_number="+2348031234567",
        first_name="Ada",
        last_name="Obi",
        is_opted_out=opted_out,
    )
    db.add(contact)
    await db.flush()
    return contact


@pytest.mark.asyncio
async def test_operator_can_create_manual_followup(client, db):
    contact = await _contact(db)
    scheduled_at = datetime.now(timezone.utc) + timedelta(hours=2)

    response = await client.post(
        "/api/v1/followups/",
        json={
            "contact_id": contact.id,
            "scheduled_at": scheduled_at.isoformat(),
            "message_text": " Hi {{first_name}}, just checking in. ",
            "notify_on_due": True,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["contact_id"] == contact.id
    assert body["contact_name"] == "Ada Obi"
    assert body["contact_phone"] == "+2348031234567"
    assert body["message_text"] == "Hi {{first_name}}, just checking in."
    assert body["status"] == "pending"
    assert body["notify_on_due"] is True

    followup = (await db.execute(select(FollowUp))).scalar_one()
    assert followup.contact_id == contact.id
    assert followup.message_text == "Hi {{first_name}}, just checking in."


@pytest.mark.asyncio
async def test_created_followup_appears_in_all_view(client, db):
    contact = await _contact(db)
    scheduled_at = datetime.now(timezone.utc) + timedelta(days=2)
    create = await client.post(
        "/api/v1/followups/",
        json={
            "contact_id": contact.id,
            "scheduled_at": scheduled_at.isoformat(),
            "message_text": "A message longer than one hundred characters is kept in full when it is listed, so the operator can verify exactly what will be sent.",
        },
    )
    assert create.status_code == 201

    response = await client.get("/api/v1/followups/", params={"view": "all"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["message_text"].endswith("what will be sent.")


@pytest.mark.asyncio
async def test_create_followup_rejects_past_time(client, db):
    contact = await _contact(db)
    response = await client.post(
        "/api/v1/followups/",
        json={
            "contact_id": contact.id,
            "scheduled_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "message_text": "Too late",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Follow-up time must be in the future"


@pytest.mark.asyncio
async def test_create_followup_requires_real_contact(client, db):
    response = await client.post(
        "/api/v1/followups/",
        json={
            "contact_id": 999,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "message_text": "Hello",
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Contact not found"


@pytest.mark.asyncio
async def test_create_followup_rejects_opted_out_contact(client, db):
    contact = await _contact(db, opted_out=True)
    response = await client.post(
        "/api/v1/followups/",
        json={
            "contact_id": contact.id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "message_text": "Hello",
        },
    )
    assert response.status_code == 400
    assert "opted-out" in response.json()["detail"]


@pytest.mark.asyncio
async def test_skipping_sequence_followup_finishes_contact_automation(client, db):
    contact = await _contact(db)
    campaign = Campaign(name="Sequence campaign", status="running")
    db.add(campaign)
    await db.flush()
    campaign_contact = CampaignContact(
        campaign_id=campaign.id,
        contact_id=contact.id,
        status="queued",
        sequence_step=2,
        next_action_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(campaign_contact)
    await db.flush()
    followup = FollowUp(
        contact_id=contact.id,
        campaign_id=campaign.id,
        campaign_contact_id=campaign_contact.id,
        sequence_step_order=2,
        status="pending",
        scheduled_at=campaign_contact.next_action_at,
    )
    db.add(followup)
    await db.flush()

    response = await client.post(f"/api/v1/followups/{followup.id}/skip")
    assert response.status_code == 200
    assert followup.status == "skipped"
    assert campaign_contact.status == "completed"
    assert campaign_contact.next_action_at is None


@pytest.mark.asyncio
async def test_create_followup_requires_timezone(client, db):
    contact = await _contact(db)
    response = await client.post(
        "/api/v1/followups/",
        json={
            "contact_id": contact.id,
            "scheduled_at": "2030-01-01T10:00:00",
            "message_text": "Hello",
        },
    )
    assert response.status_code == 422
    assert "timezone" in str(response.json()["detail"]).lower()
