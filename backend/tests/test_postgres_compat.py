"""PostgreSQL compatibility: queries that SQLite accepts but Postgres rejects.

``Template.is_active`` (and the meeting flags) are Integer-backed booleans.
Spelling a filter ``== True`` renders ``WHERE is_active = true``, which fails
on PostgreSQL with ``operator does not exist: integer = boolean`` while every
SQLite test stays green — this silently broke the inbox template preview in
production (500 -> "Could not personalize this template").

These tests boot a real PostgreSQL (via pgserver) and run the affected code
paths end to end. They skip automatically when pgserver is not installed.
"""

from datetime import datetime, timedelta, timezone

import pytest

pgserver = pytest.importorskip("pgserver")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.meeting import Meeting  # noqa: F401 — registers tables
from app.models.template import Template
from app.models.user import User
from app.security.auth import get_current_user


@pytest.fixture(scope="module")
def pg_url(tmp_path_factory):
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata")))
    yield server.get_uri().replace("postgresql://", "postgresql+asyncpg://", 1)
    server.cleanup()


@pytest_asyncio.fixture
async def pg_db(pg_url):
    engine = create_async_engine(pg_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        # Roll back the test's rows; tables persist for the module.
        await session.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_client(pg_db):
    from app.main import app

    async def _get_db():
        yield pg_db

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="tester", email="t@example.com", password_hash="x",
        role="admin", is_active=True,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()


async def _seed_thread(pg_db, *, active=True):
    contact = Contact(phone_number="+2348031111111", first_name="Ada")
    template = Template(
        name="Invite", body="Hi {{first_name}}, see you soon!",
        char_count=10, segment_count=1, is_active=active,
    )
    pg_db.add_all([contact, template])
    await pg_db.flush()
    conv = Conversation(contact_id=contact.id, status="active")
    pg_db.add(conv)
    await pg_db.flush()
    return contact, template, conv


@pytest.mark.asyncio
async def test_inbox_template_preview_on_postgres(pg_client, pg_db):
    """The exact user flow: pick a template in the inbox, get-Body back."""
    _contact, template, conv = await _seed_thread(pg_db)
    response = await pg_client.get(
        f"/api/v1/inbox/conversations/{conv.id}/templates/{template.id}/preview"
    )
    assert response.status_code == 200, response.text
    assert response.json()["body"] == "Hi Ada, see you soon!"


@pytest.mark.asyncio
async def test_inactive_template_still_404s_on_postgres(pg_client, pg_db):
    _contact, template, conv = await _seed_thread(pg_db, active=False)
    response = await pg_client.get(
        f"/api/v1/inbox/conversations/{conv.id}/templates/{template.id}/preview"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reply_with_template_on_postgres(pg_client, pg_db, monkeypatch):
    async def _fake_send(self, contact_id, body, campaign_id=None):
        from types import SimpleNamespace
        _fake_send.bodies.append(body)
        return SimpleNamespace(id=1, status="sending", provider_message_id="test-1")

    _fake_send.bodies = []
    from app.services.sms_service import SMSService
    monkeypatch.setattr(SMSService, "send_message", _fake_send)

    _contact, template, conv = await _seed_thread(pg_db)
    response = await pg_client.post(
        f"/api/v1/inbox/conversations/{conv.id}/reply",
        params={"template_id": template.id},
    )
    assert response.status_code == 200, response.text
    assert _fake_send.bodies == ["Hi Ada, see you soon!"]


@pytest.mark.asyncio
async def test_meeting_reminder_sweep_on_postgres(pg_db, monkeypatch):
    """The sweep's send_sms_reminder filter must run on Postgres too."""
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    sent: list[str] = []

    async def _fake_send(self, contact_id, body, campaign_id=None):
        from types import SimpleNamespace
        sent.append(body)
        return SimpleNamespace(id=1, status="sending")

    from app.services.sms_service import SMSService
    monkeypatch.setattr(SMSService, "send_message", _fake_send)

    contact = Contact(phone_number="+2348032222222", first_name="Chidi")
    pg_db.add(contact)
    await pg_db.flush()

    from app.services import meeting_service
    from app.models.meeting import MeetingAttendee

    starts = datetime.now(timezone.utc) + timedelta(minutes=10)
    meeting = Meeting(
        title="Call", status="scheduled",
        starts_at=starts, ends_at=starts + timedelta(minutes=30),
        send_sms_reminder=True, reminder_minutes_json="[60]",
        contact_id=contact.id,
    )
    pg_db.add(meeting)
    await pg_db.flush()
    pg_db.add(MeetingAttendee(meeting_id=meeting.id, contact_id=contact.id))
    await pg_db.commit()

    # Point the sweep at this Postgres instead of the app's default engine.
    engine = pg_db.bind
    monkeypatch.setattr(
        "app.database.async_session_factory",
        _factory(engine, class_=AsyncSession, expire_on_commit=False),
    )
    from app.services.meeting_service import process_due_meeting_reminders
    totals = await process_due_meeting_reminders()
    assert totals["sent"] == 1
    assert "Chidi" in sent[0]
    assert totals["checked"] >= 1
    assert meeting_service  # keep the import visibly used
