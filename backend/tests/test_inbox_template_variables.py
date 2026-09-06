"""Selecting a template in the chat must personalize with imported variables.

The reported bug: the inbox says a template "can use variables from the
contact", but picking one inserted text that still contained the raw short
codes for anything beyond the handful of built-in fields — so imported columns
like Pain Point never resolved, and a wrong short code went out verbatim.
"""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.template import Template
from app.models.user import User
from app.security.auth import get_current_user
from app.services.variable_service import sync_variables_from_contacts


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


@pytest_asyncio.fixture
async def scenario(db):
    contact = Contact(
        phone_number="+2348031234567",
        first_name="Ada",
        business_name="Gwarinpa Kitchen",
        custom_fields=json.dumps({"Pain Point": "no online orders", "Niche": "Restaurant"}),
    )
    db.add(contact)
    await db.flush()

    conversation = Conversation(contact_id=contact.id, status="active")
    db.add(conversation)
    await db.flush()
    await sync_variables_from_contacts(db)
    return contact, conversation


class TestChatTemplatePreview:
    @pytest.mark.asyncio
    async def test_imported_variables_resolve_in_the_chat_preview(self, client, db, scenario):
        contact, conversation = scenario
        template = Template(
            name="Pain point pitch",
            body="Hi {{first_name}}, is {{Pain Point}} still an issue at {{business_name}}?",
            is_active=True,
        )
        db.add(template)
        await db.flush()

        response = await client.get(
            f"/api/v1/inbox/conversations/{conversation.id}/templates/{template.id}/preview"
        )
        assert response.status_code == 200, response.text
        body = response.json()["body"]

        assert body == "Hi Ada, is no online orders still an issue at Gwarinpa Kitchen?"
        assert "{{" not in body

    @pytest.mark.asyncio
    async def test_a_wrong_shortcode_in_a_template_is_stripped(self, client, db, scenario):
        contact, conversation = scenario
        template = Template(
            name="Typo template",
            body="Hi {{first_name}}, does {{Business_nam}} need {{made_up_field}}?",
            is_active=True,
        )
        db.add(template)
        await db.flush()

        response = await client.get(
            f"/api/v1/inbox/conversations/{conversation.id}/templates/{template.id}/preview"
        )
        body = response.json()["body"]

        assert "{{" not in body and "}}" not in body
        assert "Business_nam" not in body
        assert "made_up_field" not in body
        assert "Ada" in body

    @pytest.mark.asyncio
    async def test_renamed_shortcode_works_in_the_chat(self, client, db, scenario):
        from sqlalchemy import select

        from app.models.variable import ContactVariable

        contact, conversation = scenario
        niche = (
            await db.execute(
                select(ContactVariable).where(ContactVariable.field_key == "niche")
            )
        ).scalar_one()
        niche.shortcode = "sector"
        await db.flush()

        template = Template(name="Sector", body="Great for {{sector}} owners.", is_active=True)
        db.add(template)
        await db.flush()

        response = await client.get(
            f"/api/v1/inbox/conversations/{conversation.id}/templates/{template.id}/preview"
        )
        assert response.json()["body"] == "Great for Restaurant owners."

    @pytest.mark.asyncio
    async def test_reply_sent_from_a_template_is_personalized(self, client, db, scenario, monkeypatch):
        sent = []

        async def fake_send(phone, body, sim=1):
            sent.append(body)
            return {"success": True, "provider_message_id": "x", "raw": {}}

        monkeypatch.setattr("app.providers.smsgate.send_sms_direct", fake_send)

        async def no_limits(self):
            return {"allowed": True, "reason": None}

        monkeypatch.setattr("app.services.sending_limits.SendingGate.check", no_limits)

        contact, conversation = scenario
        template = Template(
            name="Pitch",
            body="Hi {{first_name}}, about {{Pain Point}} and {{bogus_code}}.",
            is_active=True,
        )
        db.add(template)
        await db.flush()

        response = await client.post(
            f"/api/v1/inbox/conversations/{conversation.id}/reply",
            params={"template_id": template.id},
        )
        assert response.status_code == 200, response.text
        assert len(sent) == 1
        assert "no online orders" in sent[0]
        assert "{{" not in sent[0]
        assert "bogus_code" not in sent[0]
