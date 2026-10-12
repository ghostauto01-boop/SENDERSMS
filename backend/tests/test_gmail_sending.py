"""Connected Gmail mailboxes are first-class selectable email senders."""

from itertools import count
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.models.campaign import Campaign, CampaignContact
from app.models.contact import Contact
from app.models.conversation import Message
from app.providers import gmail as gmail_provider
from app.services import email_service, mailbox_service
from app.tasks.campaign_tasks import _send_template_message

_contact_numbers = count(1)


async def _mailbox(db, address: str):
    return await mailbox_service.create_mailbox(
        db,
        name=f"Gmail — {address}",
        email_address=address,
        provider="gmail_api",
        credential=f"refresh-token-{address}",
        send_replies=True,
    )


async def _contact(db, address: str, *, first_name: str = "Ada"):
    contact = Contact(
        phone_number=f"+234803{next(_contact_numbers):07d}",
        first_name=first_name,
        email=address,
        email_verified=True,
    )
    db.add(contact)
    await db.flush()
    return contact


def _mock_gmail_api(monkeypatch, calls: list[dict], *, fail: bool = False):
    async def access_token(db, mailbox):
        return f"access-token-{mailbox.id}", None

    async def send(token, raw):
        calls.append({"token": token, "raw": raw})
        if fail:
            return {
                "success": False,
                "status_code": 403,
                "error": "Gmail API rejected the selected mailbox credential",
            }
        return {"success": True, "provider_message_id": f"gmail-{len(calls)}"}

    monkeypatch.setattr(mailbox_service, "access_token_for", access_token)
    monkeypatch.setattr(gmail_provider, "gmail_send", send)


@pytest.mark.asyncio
async def test_every_connected_gmail_mailbox_is_selectable_and_can_send_direct_mail(
    api_db, monkeypatch
):
    first = await _mailbox(api_db, "first@gmail.com")
    second = await _mailbox(api_db, "second@gmail.com")
    senders = await email_service.list_senders(api_db)

    gmail_senders = [item for item in senders if item["sender_type"] == "gmail"]
    assert {item["id"] for item in gmail_senders} == {-first.id, -second.id}

    calls: list[dict] = []
    _mock_gmail_api(monkeypatch, calls)
    for index, mailbox in enumerate((first, second), start=1):
        sender = await email_service.resolve_sender(api_db, -mailbox.id)
        assert sender.id == mailbox.id
        contact = await _contact(api_db, f"lead{index}@example.net")
        message, result = await email_service.send_now(
            api_db,
            contact,
            subject=f"Direct message {index}",
            text_body="Hello from Gmail",
            account=sender,
        )

        assert result["success"] is True
        assert message.email_account_id == -mailbox.id
        assert message.provider == "gmail_api"
        assert message.from_address == mailbox.email_address
        assert message.status == "sent"

    assert [call["token"] for call in calls] == [
        f"access-token-{first.id}", f"access-token-{second.id}"
    ]
    assert "Subject: Direct message 1" in calls[0]["raw"]
    assert "Subject: Direct message 2" in calls[1]["raw"]


@pytest.mark.asyncio
async def test_classic_email_campaign_can_send_from_a_selected_gmail_mailbox(
    api_db, monkeypatch
):
    mailbox = await _mailbox(api_db, "campaign@gmail.com")
    contact = await _contact(api_db, "campaign-lead@example.net")
    campaign = Campaign(
        name="Gmail campaign",
        channel="email",
        status="running",
        email_account_id=-mailbox.id,
        subject="Hello {{first_name}}",
        message_body="A campaign message for {{first_name}}",
    )
    api_db.add(campaign)
    await api_db.flush()
    campaign_contact = CampaignContact(campaign_id=campaign.id, contact_id=contact.id)
    api_db.add(campaign_contact)
    await api_db.flush()

    outbox: list[int] = []
    await _send_template_message(
        api_db, campaign, campaign_contact, contact, outbox=outbox
    )
    message = (
        await api_db.execute(
            select(Message).where(Message.campaign_id == campaign.id)
        )
    ).scalar_one()

    assert outbox == [message.id]
    assert message.email_account_id == -mailbox.id
    assert message.provider == "gmail_api"
    assert message.bulk_send is True
    assert message.subject == "Hello Ada"
    assert message.body == "A campaign message for Ada"

    calls: list[dict] = []
    _mock_gmail_api(monkeypatch, calls)
    result = await email_service.deliver(api_db, message)

    assert result["success"] is True
    assert message.status == "sent"
    assert "List-Unsubscribe" in calls[0]["raw"]
    assert "campaign@gmail.com" in calls[0]["raw"]


@pytest.mark.asyncio
async def test_gmail_failure_is_visible_in_failed_email_history_with_a_reason(
    api_db, api_client, monkeypatch
):
    mailbox = await _mailbox(api_db, "failed@gmail.com")
    contact = await _contact(api_db, "failed-lead@example.net")
    calls: list[dict] = []
    _mock_gmail_api(monkeypatch, calls, fail=True)

    message, result = await email_service.send_now(
        api_db,
        contact,
        subject="This will fail",
        text_body="Please retain the reason",
        account=mailbox,
    )

    assert result["success"] is False
    assert message.status == "failed"
    assert "Gmail API rejected" in message.last_error
    assert message.failed_at is not None

    history = await api_client.get(
        "/api/v1/email/history",
        params={"status": "failed", "direction": "outgoing"},
    )
    assert history.status_code == 200, history.text
    item = next(row for row in history.json()["items"] if row["id"] == message.id)
    assert item["email_account_id"] == -mailbox.id
    assert item["email_account_name"] == mailbox.name
    assert item["last_error"] == message.last_error


def test_gmail_oauth_requests_the_full_mail_google_scope():
    url = gmail_provider.google_authorize_url(
        "client-id", "https://app.example.test/callback", "state"
    )
    scope = parse_qs(urlparse(url).query)["scope"]
    assert scope == ["https://mail.google.com/"]
