"""Tests for the calendar: booking, attendees, previews, reminders, day agenda."""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact
from app.models.meeting import Meeting  # noqa: F401 — registers tables before create_all
from app.models.template import Template
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


async def _contact(db, phone="+2348031111111", first_name="Ada", business="Acme"):
    contact = Contact(phone_number=phone, first_name=first_name, business_name=business)
    db.add(contact)
    await db.flush()
    return contact


async def _template(db, name="Invite", body="Hi {{first_name}}, see you {{meeting_date}}"):
    template = Template(name=name, body=body, char_count=len(body), segment_count=1)
    db.add(template)
    await db.flush()
    return template


def _payload(**overrides):
    starts = datetime.now(timezone.utc) + timedelta(days=1)
    ends = starts + timedelta(minutes=30)
    payload = {
        "title": "Site visit",
        "event_type": "meeting",
        "starts_at": starts.isoformat(),
        "ends_at": ends.isoformat(),
        "location": "12 Allen Avenue",
        "send_invite_sms": False,
        "send_sms_reminder": True,
        "reminder_minutes": [60, 15],
        "tags": ["vip"],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_create_meeting_with_attendees_moves_contact_to_meeting_stage(client, db):
    contact = await _contact(db)
    other = await _contact(db, phone="+2348032222222", first_name="Chidi")

    response = await client.post(
        "/api/v1/calendar/", json=_payload(contact_ids=[contact.id, other.id])
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["title"] == "Site visit"
    assert data["status"] == "scheduled"
    assert data["contact_id"] == contact.id
    assert {a["contact_id"] for a in data["attendees"]} == {contact.id, other.id}
    assert data["tags"] == ["vip"]
    assert data["reminder_minutes"] == [60, 15]

    await db.refresh(contact)
    assert contact.lead_status == "meeting"


@pytest.mark.asyncio
async def test_create_meeting_rejects_end_before_start(client, db):
    starts = datetime.now(timezone.utc) + timedelta(days=1)
    response = await client.post(
        "/api/v1/calendar/",
        json=_payload(starts_at=starts.isoformat(), ends_at=(starts - timedelta(minutes=5)).isoformat()),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_meeting_preview_renders_contact_and_meeting_shortcodes(client, db):
    contact = await _contact(db)
    template = await _template(db)
    created = await client.post(
        "/api/v1/calendar/",
        json=_payload(contact_ids=[contact.id], invite_template_id=template.id),
    )
    assert created.status_code == 201
    meeting_id = created.json()["id"]

    preview = await client.get(
        f"/api/v1/calendar/{meeting_id}/preview",
        params={"kind": "invite", "contact_id": contact.id},
    )
    assert preview.status_code == 200
    body = preview.json()["body"]
    assert "Ada" in body  # {{first_name}} from the contact
    assert "{{" not in body  # {{meeting_date}} resolved, nothing leaks


@pytest.mark.asyncio
async def test_list_filters_by_range_contact_and_tag(client, db):
    contact = await _contact(db)
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    next_week = datetime.now(timezone.utc) + timedelta(days=8)
    await client.post("/api/v1/calendar/", json=_payload(contact_ids=[contact.id], tags=["vip"]))
    await client.post(
        "/api/v1/calendar/",
        json=_payload(
            title="Next week call",
            contact_ids=[contact.id],
            starts_at=next_week.isoformat(),
            ends_at=(next_week + timedelta(minutes=30)).isoformat(),
            tags=["followup"],
        ),
    )

    window = await client.get(
        "/api/v1/calendar/",
        params={
            "date_from": (tomorrow - timedelta(hours=12)).isoformat(),
            "date_to": (tomorrow + timedelta(hours=12)).isoformat(),
        },
    )
    assert window.json()["total"] == 1

    tagged = await client.get("/api/v1/calendar/", params={"tag": "followup"})
    assert tagged.json()["total"] == 1
    assert tagged.json()["items"][0]["title"] == "Next week call"

    by_contact = await client.get("/api/v1/calendar/", params={"contact_id": contact.id})
    assert by_contact.json()["total"] == 2


@pytest.mark.asyncio
async def test_day_agenda_and_upcoming(client, db):
    contact = await _contact(db)
    starts = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    await client.post(
        "/api/v1/calendar/",
        json=_payload(
            contact_ids=[contact.id],
            starts_at=starts.isoformat(),
            ends_at=(starts + timedelta(minutes=30)).isoformat(),
        ),
    )

    agenda = await client.get("/api/v1/calendar/day", params={"date": starts.strftime("%Y-%m-%d")})
    assert agenda.status_code == 200
    assert agenda.json()["count"] == 1

    upcoming = await client.get("/api/v1/calendar/upcoming", params={"contact_id": contact.id})
    assert upcoming.json()["total"] == 1

    bad_day = await client.get("/api/v1/calendar/day", params={"date": "not-a-date"})
    assert bad_day.status_code == 422


@pytest.mark.asyncio
async def test_status_transitions_and_reminder_requires_active_meeting(client, db, monkeypatch):
    contact = await _contact(db)
    created = await client.post("/api/v1/calendar/", json=_payload(contact_ids=[contact.id]))
    meeting_id = created.json()["id"]

    confirmed = await client.post(f"/api/v1/calendar/{meeting_id}/confirm")
    assert confirmed.json()["status"] == "confirmed"

    # Reminders go through the SMS service; stub the gateway call so no network
    # is attempted, then verify the personalized text was queued.
    sent_bodies: list[str] = []

    async def _fake_send(self, contact_id, body, campaign_id=None):
        sent_bodies.append(body)
        from types import SimpleNamespace
        return SimpleNamespace(id=1, status="sending")

    from app.services.sms_service import SMSService
    monkeypatch.setattr(SMSService, "send_message", _fake_send)

    reminder = await client.post(f"/api/v1/calendar/{meeting_id}/send-reminder")
    assert reminder.status_code == 200, reminder.text
    assert reminder.json()["sent"] == 1
    assert "Ada" in sent_bodies[0]

    cancelled = await client.post(f"/api/v1/calendar/{meeting_id}/cancel")
    assert cancelled.json()["status"] == "cancelled"

    blocked = await client.post(f"/api/v1/calendar/{meeting_id}/send-reminder")
    assert blocked.status_code == 409


@pytest.mark.asyncio
async def test_update_reschedules_and_resets_reminders(client, db):
    from app.models.meeting import Meeting

    contact = await _contact(db)
    created = await client.post("/api/v1/calendar/", json=_payload(contact_ids=[contact.id]))
    meeting_id = created.json()["id"]

    meeting = (await db.execute(
        __import__("sqlalchemy").select(Meeting).where(Meeting.id == meeting_id)
    )).scalar_one()
    meeting.reminders_sent_json = '{"60": "2026-01-01T00:00:00+00:00"}'
    await db.flush()

    # Moving the start keeps the 30-minute length and clears fired reminders.
    starts = datetime.now(timezone.utc) + timedelta(days=3)
    updated = await client.put(
        f"/api/v1/calendar/{meeting_id}", json={"starts_at": starts.isoformat()}
    )
    assert updated.status_code == 200
    assert updated.json()["reminders_sent"] == {}
    assert updated.json()["starts_at"].startswith(starts.strftime("%Y-%m-%d"))


@pytest.mark.asyncio
async def test_import_csv_creates_list_inline(client, db):
    csv_bytes = b"phone,first_name\n08031111111,Ada\n08032222222,Chidi\n"
    response = await client.post(
        "/api/v1/contacts/import/csv",
        files={"file": ("contacts.csv", csv_bytes, "text/csv")},
        data={"new_list_name": "Fresh Leads", "tags": "restaurant, cold"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["imported"] == 2
    assert data["list"]["name"] == "Fresh Leads"

    members = await client.get(f"/api/v1/lists/{data['list']['id']}/contacts")
    assert members.json()["total"] == 2


@pytest.mark.asyncio
async def test_contact_tags_endpoint_lists_tags_with_counts(client, db):
    await _contact(db)
    csv_bytes = b"phone,first_name\n08033333333,Efe\n"
    await client.post(
        "/api/v1/contacts/import/csv",
        files={"file": ("one.csv", csv_bytes, "text/csv")},
        data={"tags": "vip"},
    )
    response = await client.get("/api/v1/contacts/tags/")
    assert response.status_code == 200
    names = {item["name"]: item["count"] for item in response.json()["items"]}
    assert names.get("vip") == 1
