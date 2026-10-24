"""Calendar invites and reminders over email.

The calendar used to be SMS-only: a contact who had an address but no phone
could never be invited to a meeting, and "remind me" always meant a text. Both
channels are now independent switches on the same booking.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact
from app.models.meeting import Meeting, MeetingAttendee
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


class _Recorder:
    """Records every email the meeting service hands to the provider."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_now(self, db, contact, **kwargs):
        self.sent.append({"contact": contact.email, **kwargs})
        return object(), {"success": True}


def _payload(**overrides):
    starts = datetime.now(timezone.utc) + timedelta(days=1)
    payload = {
        "title": "Site visit",
        "event_type": "meeting",
        "starts_at": starts.isoformat(),
        "ends_at": (starts + timedelta(minutes=30)).isoformat(),
        "send_invite_sms": False,
        "send_sms_reminder": False,
        "send_invite_email": True,
        "invite_subject": "You are booked: {{meeting_title}}",
        "reminder_minutes": [60],
        "tags": [],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_email_only_contact_gets_an_email_invite(client, db, monkeypatch):
    from app.services import email_service

    recorder = _Recorder()
    monkeypatch.setattr(email_service, "send_now", recorder.send_now)

    contact = Contact(
        phone_number="email:ada@acme.example", email="ada@acme.example", first_name="Ada"
    )
    db.add(contact)
    await db.flush()

    response = await client.post(
        "/api/v1/calendar/", json=_payload(contact_ids=[contact.id])
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["send_invite_email"] is True
    assert body["invite"]["email_sent"] == 1

    assert recorder.sent and recorder.sent[0]["contact"] == "ada@acme.example"
    # The subject is rendered with the same shortcodes as the body.
    assert recorder.sent[0]["subject"] == "You are booked: Site visit"
    assert "Ada" in recorder.sent[0]["text_body"]


@pytest.mark.asyncio
async def test_email_reminder_fires_even_when_sms_reminders_are_off(db, monkeypatch):
    from app.services import email_service, meeting_service

    recorder = _Recorder()
    monkeypatch.setattr(email_service, "send_now", recorder.send_now)

    contact = Contact(
        phone_number="email:chidi@acme.example",
        email="chidi@acme.example",
        first_name="Chidi",
    )
    db.add(contact)
    await db.flush()

    starts = datetime.now(timezone.utc) + timedelta(minutes=30)
    meeting = Meeting(
        title="Site visit",
        starts_at=starts,
        ends_at=starts + timedelta(minutes=30),
        contact_id=contact.id,
        send_invite_sms=0,
        send_invite_email=0,
        send_sms_reminder=0,
        send_email_reminder=1,
        reminder_subject="Reminder: {{meeting_title}}",
        reminder_minutes_json="[60]",
    )
    db.add(meeting)
    await db.flush()
    db.add(MeetingAttendee(meeting_id=meeting.id, contact_id=contact.id))
    await db.flush()

    # The due sweep runs on the app's own session factory; point it at this one.
    class _SameSession:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("app.database.async_session_factory", lambda: _SameSession())

    await meeting_service.process_due_meeting_reminders()

    assert recorder.sent, "the email reminder should have been sent"
    assert recorder.sent[0]["subject"] == "Reminder: Site visit"
    refreshed = (
        await db.execute(select(Meeting).where(Meeting.id == meeting.id))
    ).scalar_one()
    assert refreshed.reminders_sent_json
