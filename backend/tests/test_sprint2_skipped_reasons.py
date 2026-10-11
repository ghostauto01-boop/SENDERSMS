"""Sprint 2 — C9: a refused send says WHY.

Reproduced: ``POST /email/send`` to a contact the app refuses answers
``{"success": false, "sent": 0, "skipped": 1, "failed": 0}`` and nothing else -- no reason,
whether it was an opt-out, a bounce, the suppression list or an unverified address. An SMS list send
that hit the rate limit part-way ``break``-ed out of its loop and the recipients it never reached
vanished from the response. The one contract for every send response now is::

    skipped            how many targets were not sent to
    skipped_reason     the reason code of the first of them (or null)
    skipped_reasons    {code: count}
    skipped_details    [{contact_id, reason, message}]   (the first 50)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from tests.campaign_factory import make_account


@pytest_asyncio.fixture
async def delivered(monkeypatch):
    from app.services import email_service

    async def ok(db, message):
        message.status = "sent"
        message.sent_at = datetime.now(timezone.utc)
        return {"success": True}

    monkeypatch.setattr(email_service, "deliver", ok)


async def _list(db, contacts):
    lst = ContactList(name=f"L{len(contacts)}-{id(contacts)}")
    db.add(lst)
    await db.flush()
    for contact in contacts:
        db.add(ContactListMember(list_id=lst.id, contact_id=contact.id))
    await db.flush()
    return lst


def _email_contact(n, **kw):
    return Contact(first_name=f"P{n}", email=f"p{n}@leadco{n}.io", **kw)


# --------------------------------------------------------------------------
# email
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refused_one_to_one_email_says_why(api_client, api_db, delivered):
    await make_account(api_db, default=True)
    contact = _email_contact(1, is_email_opted_out=True)
    api_db.add(contact)
    await api_db.flush()

    body = (await api_client.post("/api/v1/email/send", json={
        "contact_id": contact.id, "subject": "Hi", "body": "Hello"})).json()

    assert body["success"] is False and body["sent"] == 0 and body["skipped"] == 1
    assert body["skipped_reason"] == "email_opted_out"
    assert body["skipped_reasons"] == {"email_opted_out": 1}
    assert body["skipped_details"] == [{
        "contact_id": contact.id, "reason": "email_opted_out",
        "message": "the contact opted out of email"}]
    assert "opted out" in body["message"], "a plain sentence for whoever reads only the message"


@pytest.mark.asyncio
async def test_a_list_send_counts_each_reason(api_client, api_db, delivered, monkeypatch):
    from app.config import settings

    # The verification gate is part of this behaviour: switch it on for this test.
    monkeypatch.setattr(settings, "EMAIL_REQUIRE_VERIFIED", True)
    await make_account(api_db, default=True)
    good = _email_contact(1, email_verified=True, email_verdict="deliverable")
    opted_out = _email_contact(2, is_email_opted_out=True, email_verified=True)
    unverified = _email_contact(3)
    bounced = _email_contact(4, email_status="bounced", email_verified=True)
    api_db.add_all([good, opted_out, unverified, bounced])
    await api_db.flush()
    lst = await _list(api_db, [good, opted_out, unverified, bounced])
    body = (await api_client.post("/api/v1/email/send", json={
        "list_id": lst.id, "subject": "Hi", "body": "Hello"})).json()

    assert body["sent"] == 2 and body["skipped"] == 2
    assert body["skipped_reasons"] == {"email_opted_out": 1, "email_bounced": 1}
    assert {d["contact_id"]: d["reason"] for d in body["skipped_details"]} == {
        opted_out.id: "email_opted_out", bounced.id: "email_bounced"}
    assert body["skipped_reason"] in body["skipped_reasons"]
    assert all(d["contact_id"] != unverified.id for d in body["skipped_details"])


@pytest.mark.asyncio
async def test_a_send_with_nothing_skipped_reports_no_reason(api_client, api_db, delivered):
    await make_account(api_db, default=True)
    contact = _email_contact(1)
    api_db.add(contact)
    await api_db.flush()

    body = (await api_client.post("/api/v1/email/send", json={
        "contact_id": contact.id, "subject": "Hi", "body": "Hello"})).json()

    assert body["success"] is True and body["skipped"] == 0
    assert body["skipped_reason"] is None and body["skipped_reasons"] == {} and body["skipped_details"] == []


@pytest.mark.asyncio
async def test_send_now_reports_the_reason_it_would_not_queue(api_db):
    from app.services import email_service

    contact = _email_contact(1, is_email_opted_out=True)
    api_db.add(contact)
    await api_db.flush()
    account = await make_account(api_db, default=True)

    message, result = await email_service.send_now(
        api_db, contact, subject="Hi", text_body="Hello", account=account)

    assert message is None
    assert result == {"success": False, "error": "contact_not_emailable",
                      "skipped_reason": "email_opted_out"}


# --------------------------------------------------------------------------
# SMS
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sms_out(monkeypatch):
    import app.providers.smsgate as sg

    sent: list[str] = []

    async def fake_send(phone, body, sim=1):
        sent.append(phone)
        return {"success": True, "provider_message_id": f"srv-{len(sent)}", "status": "Pending"}

    monkeypatch.setattr(sg, "send_sms_direct", fake_send)
    return sent


@pytest.mark.asyncio
async def test_an_sms_list_send_reports_what_it_filtered_out_and_why(api_client, api_db, sms_out, monkeypatch):
    from app.services import sending_limits

    async def open_slot(db, cap_seconds=8, channel=None):
        return {"allowed": True, "wait_seconds": 0, "reason": None}

    monkeypatch.setattr(sending_limits, "await_slot", open_slot)
    good = Contact(first_name="Ada", phone_number="+2348031200001")
    opted_out = Contact(first_name="Bayo", phone_number="+2348031200002", is_opted_out=True)
    landline = Contact(first_name="Cy", phone_number="+23412345678")
    api_db.add_all([good, opted_out, landline])
    await api_db.flush()
    lst = await _list(api_db, [good, opted_out, landline])

    body = (await api_client.post("/api/v1/send/", params={"list_id": lst.id, "body": "Hello"})).json()

    assert body["sent"] == 1 and sms_out == ["+2348031200001"]
    assert body["skipped"] == 2
    assert body["skipped_reasons"] == {"opted_out": 1, "foreign_number": 1}
    assert {d["contact_id"] for d in body["skipped_details"]} == {opted_out.id, landline.id}
    assert body["skipped_reason"] in body["skipped_reasons"]
    assert body["filtered_count"] == 2, "the older keys stay"


@pytest.mark.asyncio
async def test_recipients_a_rate_limit_stopped_the_send_before_reaching_are_not_silently_dropped(
        api_client, api_db, sms_out, monkeypatch):
    from app.services import sending_limits

    calls = {"n": 0}

    async def one_then_limited(db, cap_seconds=8, channel=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"allowed": True, "wait_seconds": 0, "reason": None}
        return {"allowed": False, "wait_seconds": 25, "reason": "wait 30 seconds between messages"}

    monkeypatch.setattr(sending_limits, "await_slot", one_then_limited)
    contacts = [Contact(first_name=f"P{i}", phone_number=f"+23480312{i:05d}") for i in range(1, 5)]
    api_db.add_all(contacts)
    await api_db.flush()
    lst = await _list(api_db, contacts)

    body = (await api_client.post("/api/v1/send/", params={"list_id": lst.id, "body": "Hello"})).json()

    assert body["sent"] == 1 and len(sms_out) == 1
    assert body["skipped"] == 3
    assert body["skipped_reasons"] == {"rate_limited": 3}
    assert body["skipped_reason"] == "rate_limited"
    assert "rate limited" in body["message"] and "3" in body["message"]
