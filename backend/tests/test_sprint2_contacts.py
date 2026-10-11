"""Sprint 2 — C5: ``update_contact`` (and ``get_contact``) showed an INVENTED phone number.

Reproduced: emailing a typed address (``POST /email/send``) created a contact whose
``phone_number`` was ``"email:bola@acme-lead"`` -- the address, prefixed and cut to 20
characters, kept there because the column used to be NOT NULL. It is not a phone number; an
assistant reading it, or updating that contact, was handed a number that does not exist. Two other
paths (scheduled sends, inbound mail from an unknown sender) did the same.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.contact import Contact
from tests.campaign_factory import make_account
from tests.mcp_helpers import call_tool


@pytest_asyncio.fixture
async def delivered(monkeypatch):
    from app.services import email_service

    async def ok(db, message):
        message.status = "sent"
        message.sent_at = datetime.now(timezone.utc)
        return {"success": True}

    monkeypatch.setattr(email_service, "deliver", ok)


@pytest.mark.asyncio
async def test_emailing_a_typed_address_does_not_invent_a_phone_number(api_client, api_db, delivered):
    await make_account(api_db, default=True)

    sent = await api_client.post("/api/v1/email/send", json={
        "email": "bola@acme-leads.io", "subject": "Hi", "body": "Hello"})

    assert sent.status_code == 200 and sent.json()["sent"] == 1
    contact = (await api_db.execute(select(Contact).where(Contact.email == "bola@acme-leads.io"))).scalar_one()
    assert contact.phone_number is None
    assert contact.email_lower == "bola@acme-leads.io"


@pytest.mark.asyncio
async def test_mail_from_an_unknown_sender_does_not_invent_a_phone_number(api_client, api_db):
    account = await make_account(api_db, default=True)
    from app.services import email_service

    token = await email_service.ensure_webhook_token(api_db, account)
    await api_db.flush()

    response = await api_client.post(f"/api/v1/webhooks/brevo/{account.id}", params={"token": token}, json={
        "items": [{"From": {"Address": "stranger@elsewhere.io", "Name": "Sam"}, "To": {"Address": account.from_email},
                   "Subject": "Question", "TextBody": "Do you ship to Kano?",
                   "MessageId": "<q1@elsewhere.io>"}]})

    assert response.status_code == 200, response.text
    contact = (await api_db.execute(select(Contact).where(Contact.email == "stranger@elsewhere.io"))).scalar_one()
    assert contact.phone_number is None
    assert contact.country is None, "a stranger's country is not known, so it is not stored as Nigeria"


@pytest.mark.asyncio
async def test_a_scheduled_email_to_a_new_address_does_not_invent_a_phone_number_or_a_country(
        api_db, delivered):
    from app.main import _process_scheduled_email
    from app.models.scheduled import ScheduledMessage

    await make_account(api_db, default=True)
    scheduled = ScheduledMessage(to_address="dayo@acme-leads.io", channel="email", subject="Hi",
                                 body="Hello", schedule_at=datetime.now(timezone.utc))
    api_db.add(scheduled)
    await api_db.flush()

    await _process_scheduled_email(api_db, scheduled)

    assert scheduled.status == "sent", scheduled.error
    contact = (await api_db.execute(select(Contact).where(Contact.email == "dayo@acme-leads.io"))).scalar_one()
    assert contact.phone_number is None and contact.country is None
    assert contact.source == "scheduled_email"


@pytest.mark.asyncio
async def test_update_contact_returns_what_the_contact_has_and_nothing_more(api_client, api_db, delivered):
    from app.mcp.registry import TOOLS_BY_NAME  # noqa: F401

    created = await api_client.post("/api/v1/contacts/", json={
        "email": "ada@zenith.io", "first_name": "Ada", "country": "Ghana"})
    contact_id = created.json()["id"]

    out = await call_tool("update_contact", contact_id=contact_id, city="Accra")

    assert out["ok"] is True, out
    data = out["data"]
    assert data["phone_number"] is None and data["email"] == "ada@zenith.io"
    assert data["country"] == "Ghana", "an update changes only what it was given"
    assert data["city"] == "Accra"


@pytest.mark.asyncio
async def test_the_update_tool_advertises_no_defaults_for_optional_fields():
    """A schema default is a value a client may fill in for you: `country: Nigeria` on an UPDATE
    would overwrite a contact's real country. Defaults belong on create only."""
    from app.mcp.registry import TOOLS_BY_NAME

    update_defaults = [p.name for p in TOOLS_BY_NAME["update_contact"].params if p.default is not None]
    create_defaults = [p.name for p in TOOLS_BY_NAME["create_contact"].params if p.default is not None]

    assert update_defaults == []
    assert create_defaults == [], "the API does not default a country any more; neither may the tool"


@pytest.mark.asyncio
async def test_existing_placeholder_phones_are_repaired_without_touching_real_ones(api_db):
    from app.services.contact_repair import repair_placeholder_phones

    fake = Contact(email="bola@acme-leads.io", phone_number="email:bola@acme-lead", source="email_send")
    fake_no_email = Contact(phone_number="email:ghost@nowhere.i")   # no address to fall back on: left alone
    real = Contact(first_name="Ada", phone_number="+2348031200001", email="ada@zenith.io")
    api_db.add_all([fake, fake_no_email, real])
    await api_db.flush()

    first = await repair_placeholder_phones(api_db)
    second = await repair_placeholder_phones(api_db)

    assert first == 1 and second == 0, "idempotent"
    await api_db.refresh(fake)
    await api_db.refresh(real)
    await api_db.refresh(fake_no_email)
    assert fake.phone_number is None and fake.email == "bola@acme-leads.io"
    assert real.phone_number == "+2348031200001"
    assert fake_no_email.phone_number == "email:ghost@nowhere.i"
