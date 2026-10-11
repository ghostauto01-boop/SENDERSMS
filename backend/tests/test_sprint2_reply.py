"""Sprint 2 — C3: ``reply_to_conversation`` always answered 422 "Message body or template is
required".

Cause (found by reading, then reproduced): the endpoint declares ``body`` and ``template_id``
as QUERY parameters, but the tool sends ``body`` in the JSON BODY, which the endpoint ignores.
It also only ever sent SMS, although the tool promises "SMS or email automatically": a reply to an
email conversation texted the contact's phone (or failed). And an SMS reply attached itself to the
contact's FIRST conversation, whatever channel that was.

These are end-to-end: the tool dispatcher -> the real endpoint -> the (stubbed) provider.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.models.template import Template
from tests.campaign_factory import make_account
from tests.mcp_helpers import call_tool


@pytest_asyncio.fixture
async def sms_out(monkeypatch):
    """Every SMS the app tries to send, with the provider stubbed."""
    import app.providers.smsgate as sg

    sent: list[tuple[str, str]] = []

    async def fake_send(phone, body, sim=1):
        sent.append((phone, body))
        return {"success": True, "provider_message_id": f"srv-{len(sent)}", "status": "Pending"}

    monkeypatch.setattr(sg, "send_sms_direct", fake_send)

    async def no_pacing(self, *args, **kwargs):
        # The 30-second spacing between texts is its own, separately tested rule.
        return {"allowed": True, "wait_seconds": 0, "reason": None}

    monkeypatch.setattr("app.services.sending_limits.SendingGate.check", no_pacing)
    return sent


@pytest_asyncio.fixture
async def email_out(monkeypatch):
    """Every email delivery, with Brevo stubbed."""
    from app.services import email_service

    delivered: list[Message] = []

    async def fake_deliver(db, message):
        message.status = "sent"
        message.sent_at = datetime.now(timezone.utc)
        delivered.append(message)
        return {"success": True}

    monkeypatch.setattr(email_service, "deliver", fake_deliver)
    return delivered


async def _contact(db, **kw):
    contact = Contact(first_name="Ada", phone_number="+2348031200001", email="ada@leadco.io",
                      email_verified=True, **kw)
    db.add(contact)
    await db.flush()
    return contact


async def _conversation(db, contact, channel):
    conv = Conversation(contact_id=contact.id, channel=channel, status="active")
    db.add(conv)
    await db.flush()
    return conv


@pytest.mark.asyncio
async def test_the_reply_tool_sends_an_sms(api_client, api_db, sms_out):
    conv = await _conversation(api_db, await _contact(api_db), "sms")

    out = await call_tool("reply_to_conversation", conversation_id=conv.id, body="Thanks, calling you now")

    assert out["ok"] is True, out
    assert out["data"]["success"] is True
    assert sms_out == [("+2348031200001", "Thanks, calling you now")]


@pytest.mark.asyncio
async def test_the_reply_tool_can_answer_with_a_template(api_client, api_db, sms_out):
    conv = await _conversation(api_db, await _contact(api_db), "sms")
    template = Template(name="Callback", body="Hi {{first_name}}, we will call at 3pm", channel="sms")
    api_db.add(template)
    await api_db.flush()

    out = await call_tool("reply_to_conversation", conversation_id=conv.id, template_id=template.id)

    assert out["ok"] is True, out
    assert sms_out[0][1] == "Hi Ada, we will call at 3pm"


@pytest.mark.asyncio
async def test_the_endpoint_takes_a_json_body_and_still_takes_the_old_query_parameters(
        api_client, api_db, sms_out):
    conv = await _conversation(api_db, await _contact(api_db), "sms")
    url = f"/api/v1/inbox/conversations/{conv.id}/reply"

    as_json = await api_client.post(url, json={"body": "from json"})
    as_query = await api_client.post(url, params={"body": "from the query"})   # the dashboard's form

    assert as_json.status_code == 200 and as_query.status_code == 200
    assert [text for _phone, text in sms_out] == ["from json", "from the query"]


@pytest.mark.asyncio
async def test_a_reply_with_nothing_to_say_is_still_a_clear_422(api_client, api_db, sms_out):
    conv = await _conversation(api_db, await _contact(api_db), "sms")

    response = await api_client.post(f"/api/v1/inbox/conversations/{conv.id}/reply", json={})

    assert response.status_code == 422
    assert "Message body or template is required" in response.json()["message"]
    assert sms_out == []


@pytest.mark.asyncio
async def test_a_reply_to_an_email_conversation_goes_out_as_email_not_sms(
        api_client, api_db, sms_out, email_out):
    await make_account(api_db, default=True)
    conv = await _conversation(api_db, await _contact(api_db), "email")
    conv.subject = "Your price list"
    await api_db.flush()

    out = await call_tool("reply_to_conversation", conversation_id=conv.id, body="Happy to help — see attached.")

    assert out["ok"] is True, out
    assert sms_out == [], "an email thread must never be answered by text message"
    assert len(email_out) == 1 and email_out[0].channel == "email"
    assert email_out[0].conversation_id == conv.id
    assert email_out[0].body == "Happy to help — see attached."
    assert out["data"]["success"] is True


@pytest.mark.asyncio
async def test_an_sms_reply_lands_in_the_conversation_it_answers(api_client, api_db, sms_out):
    """A contact with an email thread AND a text thread: the SMS reply belongs in the text thread
    (it used to attach to whichever conversation the contact had first)."""
    contact = await _contact(api_db)
    email_conv = await _conversation(api_db, contact, "email")
    sms_conv = await _conversation(api_db, contact, "sms")
    assert email_conv.id < sms_conv.id

    out = await call_tool("reply_to_conversation", conversation_id=sms_conv.id, body="Got your text")

    assert out["ok"] is True, out
    message = (await api_db.execute(select(Message).where(Message.direction == "outgoing"))).scalar_one()
    assert message.conversation_id == sms_conv.id


@pytest.mark.asyncio
async def test_the_reply_tool_advertises_what_the_endpoint_accepts():
    from app.mcp.registry import TOOLS_BY_NAME

    params = {p.name: p for p in TOOLS_BY_NAME["reply_to_conversation"].params}
    assert {"conversation_id", "body", "template_id", "subject"} <= set(params)
    assert params["body"].required is False, "a template can stand in for the body"


@pytest.mark.asyncio
async def test_a_direct_sms_send_does_not_land_in_the_contacts_email_thread(api_client, api_db, sms_out):
    """Same bug as the reply path, in `POST /send/`: the message used to be attached to the contact's
    first conversation whatever its channel."""
    contact = await _contact(api_db)
    email_conv = await _conversation(api_db, contact, "email")

    response = await api_client.post("/api/v1/send/", params={"contact_id": contact.id, "body": "Hello by text"})

    assert response.status_code == 200, response.text
    assert sms_out == [("+2348031200001", "Hello by text")]
    message = (await api_db.execute(select(Message).where(Message.direction == "outgoing"))).scalar_one()
    assert message.conversation_id != email_conv.id
    conv = (await api_db.execute(select(Conversation).where(Conversation.id == message.conversation_id))).scalar_one()
    assert (conv.channel or "sms") == "sms"


@pytest.mark.asyncio
async def test_an_inbound_text_does_not_mark_the_contacts_email_thread_unread(api_db):
    """The receiving side of the same bug: a text from a contact who also has an email thread used
    to be attached to whichever conversation they had first."""
    from app.services.sms_service import SMSService

    contact = await _contact(api_db)
    email_conv = await _conversation(api_db, contact, "email")

    await SMSService(api_db).process_inbound_message(contact.phone_number, "Yes please, call me")

    await api_db.refresh(email_conv)
    assert email_conv.status == "active" and not email_conv.unread_count, "the email thread is untouched"
    convs = (await api_db.execute(select(Conversation).where(Conversation.contact_id == contact.id))).scalars().all()
    sms_conv = next(c for c in convs if (c.channel or "sms") == "sms")
    assert sms_conv.id != email_conv.id and sms_conv.unread_count == 1
