"""Email threading, attachments, attachments caps and one-click unsubscribe.

These run against a fake Brevo that captures the *exact JSON payload* our
provider would have posted, which is the only way to prove the wire contract:
threading headers, List-Unsubscribe, and attachment encoding.
"""

import base64
import json

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.models.email import EmailAccount, EmailSuppression
from app.security.encryption import encrypt_value
from app.services import email_service


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


class _FakeResponse:
    def __init__(self, status_code=201, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"messageId": "<fake@brevo>"}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def _fake_brevo(monkeypatch, sent: list):
    """Capture every outbound Brevo payload; always accept."""

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, *, headers=None, json=None, **kwargs):
            sent.append({"url": url, "payload": json or {}, "headers": headers or {}})
            return _FakeResponse()

        async def get(self, url, **kwargs):  # domains / account checks
            return _FakeResponse(200, {"domains": []})

    monkeypatch.setattr("app.providers.brevo.httpx.AsyncClient", FakeClient)


async def _account(db, **overrides):
    account = EmailAccount(
        name="Main",
        from_name="Acme",
        from_email="hello@acme-leads.io",
        reply_to="replies@acme-leads.io",
        api_key_encrypted=encrypt_value("key-main"),
        is_default=True,
        is_active=True,
        **overrides,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


@pytest.mark.asyncio
async def test_first_email_has_message_id_and_no_thread_headers(db, monkeypatch):
    sent: list = []
    _fake_brevo(monkeypatch, sent)
    account = await _account(db)
    db.add(Contact(phone_number="+15550000101", email="lead@acme-leads.io"))
    await db.commit()
    contact = (await db.execute(select(Contact))).scalar_one()

    message, result = await email_service.send_now(
        db, contact, subject="First touch", text_body="Hello there", account=account,
    )
    assert result["success"] is True
    assert message.rfc_message_id and message.rfc_message_id.startswith("<")

    payload = sent[0]["payload"]
    assert "In-Reply-To" not in payload.get("headers", {})
    assert payload["subject"] == "First touch"


@pytest.mark.asyncio
async def test_reply_stays_in_the_same_thread(db, monkeypatch):
    """The second email must quote the first one's Message-ID."""
    sent: list = []
    _fake_brevo(monkeypatch, sent)
    account = await _account(db)
    db.add(Contact(phone_number="+15550000102", email="lead2@acme-leads.io"))
    await db.commit()
    contact = (await db.execute(select(Contact))).scalar_one()

    first, _ = await email_service.send_now(
        db, contact, subject="Pricing", text_body="Here is the pricing", account=account,
    )
    second, _ = await email_service.send_now(
        db, contact, subject="Re: Pricing", text_body="Following up", account=account,
    )

    assert first.conversation_id == second.conversation_id
    headers = sent[1]["payload"]["headers"]
    assert headers["In-Reply-To"] == first.rfc_message_id
    assert first.rfc_message_id in headers["References"]


@pytest.mark.asyncio
async def test_inbound_reply_lands_in_the_thread_it_answered(db, monkeypatch):
    """An inbound 'Re:' carrying In-Reply-To must join the original thread."""
    sent: list = []
    _fake_brevo(monkeypatch, sent)
    account = await _account(db)
    await email_service.ensure_webhook_token(db, account)
    db.add(Contact(phone_number="+15550000103", email="lead3@acme-leads.io"))
    await db.commit()
    contact = (await db.execute(select(Contact))).scalar_one()

    outbound, _ = await email_service.send_now(
        db, contact, subject="Your quote", text_body="Quote attached", account=account,
    )
    original_thread = outbound.conversation_id

    result = await email_service.process_inbound_email(db, {
        "items": [{
            "From": {"Address": "lead3@acme-leads.io", "Name": "Lead Three"},
            "To": {"Address": "hello@acme-leads.io"},
            # The subject is deliberately UNRELATED: only the message-id chain
            # identifies the thread, which is what a real mail client relies on
            # when someone changes the subject mid-conversation.
            "Subject": "Completely different subject",
            "TextBody": "Looks good, send the invoice please.",
            "MessageId": "<inbound-1@mail.example>",
            "Headers": {
                "In-Reply-To": outbound.rfc_message_id,
                "References": outbound.rfc_message_id,
            },
        }]
    })
    await db.commit()
    assert result["stored"] == 1

    inbound = (
        await db.execute(
            select(Message).where(Message.direction == "incoming", Message.channel == "email")
        )
    ).scalar_one()
    assert inbound.conversation_id == original_thread
    assert inbound.in_reply_to == outbound.rfc_message_id
    # …and it is the newest message in that thread, so the inbox shows it there.
    assert inbound.subject == "Completely different subject"


@pytest.mark.asyncio
async def test_inbound_reply_without_headers_falls_back_to_subject(db, monkeypatch):
    sent: list = []
    _fake_brevo(monkeypatch, sent)
    account = await _account(db)
    db.add(Contact(phone_number="+15550000104", email="lead4@acme-leads.io"))
    await db.commit()
    contact = (await db.execute(select(Contact))).scalar_one()

    outbound, _ = await email_service.send_now(
        db, contact, subject="Invoice 4021", text_body="Please find it here", account=account,
    )

    await email_service.process_inbound_email(db, {
        "items": [{
            "From": "lead4@acme-leads.io",
            "Subject": "RE: Invoice 4021",
            "TextBody": "Paid today, thanks.",
        }]
    })
    await db.commit()

    inbound = (
        await db.execute(
            select(Message).where(Message.direction == "incoming", Message.channel == "email")
        )
    ).scalar_one()
    assert inbound.conversation_id == outbound.conversation_id


@pytest.mark.asyncio
async def test_campaign_mail_carries_list_unsubscribe_headers(db, monkeypatch):
    sent: list = []
    _fake_brevo(monkeypatch, sent)
    account = await _account(db)
    db.add(Contact(phone_number="+15550000105", email="bulk@acme-leads.io"))
    await db.commit()
    contact = (await db.execute(select(Contact))).scalar_one()

    await email_service.send_now(
        db, contact, subject="Bulk offer", text_body="Hello", account=account, bulk=True,
    )
    headers = sent[0]["payload"]["headers"]
    assert "List-Unsubscribe" in headers
    assert "List-Unsubscribe-Post" in headers
    assert "mailto:replies@acme-leads.io" in headers["List-Unsubscribe"]


@pytest.mark.asyncio
async def test_attachments_reach_brevo_encoded(db, monkeypatch):
    sent: list = []
    _fake_brevo(monkeypatch, sent)
    account = await _account(db)
    db.add(Contact(phone_number="+15550000106", email="files@acme-leads.io"))
    await db.commit()
    contact = (await db.execute(select(Contact))).scalar_one()

    payload_b64 = base64.b64encode(b"price list contents").decode()
    message, result = await email_service.send_now(
        db, contact, subject="Quote", text_body="See attached", account=account,
        attachments=[{"name": "quote.pdf", "content_base64": payload_b64,
                      "content_type": "application/pdf"}],
    )
    assert result["success"] is True
    attachment = sent[0]["payload"]["attachment"][0]
    assert attachment["name"] == "quote.pdf"
    assert base64.b64decode(attachment["content"]) == b"price list contents"

    # The stored message keeps the payload (so a retry can resend it) but the
    # API-facing summary never exposes it.
    summary = email_service.attachment_summary(message.attachments)
    assert summary == [{"name": "quote.pdf", "content_type": "application/pdf",
                        "size": len(b"price list contents")}]
    assert "content" not in summary[0]


@pytest.mark.asyncio
async def test_attachment_caps_drop_oversized_files_not_the_send(db):
    huge = base64.b64encode(b"x" * (email_service.MAX_ATTACHMENT_BYTES + 1)).decode()
    small = base64.b64encode(b"ok").decode()
    cleaned = email_service.clean_attachments([
        {"name": "huge.bin", "content_base64": huge},
        {"name": "fine.txt", "content_base64": small},
        {"name": "", "content_base64": small},
        {"name": "bad.txt", "content_base64": "not base64 at all !!!"},
    ])
    assert [c["name"] for c in cleaned] == ["fine.txt"]


@pytest.mark.asyncio
async def test_one_click_unsubscribe_marks_contact_and_suppresses(db):
    from app.api.v1.email import unsubscribe_post

    await _account(db)
    db.add(Contact(phone_number="+15550000107", email="optout@acme-leads.io"))
    await db.commit()
    contact = (await db.execute(select(Contact))).scalar_one()

    token = email_service._unsubscribe_token("optout@acme-leads.io")
    result = await unsubscribe_post(e="optout@acme-leads.io", t=token, db=db)
    assert result["success"] is True

    await db.refresh(contact)
    assert contact.is_email_opted_out is True
    assert contact.email_status == "unsubscribed"
    assert await email_service.get_suppression(db, "optout@acme-leads.io") is not None

    # …and the address can no longer be emailed.
    assert await email_service.contact_email_problem(db, contact) == "email_opted_out"


@pytest.mark.asyncio
async def test_unsubscribe_rejects_a_forged_token(db):
    from fastapi import HTTPException

    from app.api.v1.email import unsubscribe_post

    await _account(db)
    with pytest.raises(HTTPException) as exc:
        await unsubscribe_post(e="victim@acme-leads.io", t="deadbeef", db=db)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_html_body_round_trips_and_images_are_allowed(db, monkeypatch):
    """Rich HTML (images, links) must survive verbatim to Brevo."""
    sent: list = []
    _fake_brevo(monkeypatch, sent)
    account = await _account(db)
    db.add(Contact(phone_number="+15550000108", email="rich@acme-leads.io"))
    await db.commit()
    contact = (await db.execute(select(Contact))).scalar_one()

    html = (
        '<p>Hi {{first_name}},</p>'
        '<p><img src="https://cdn.example.com/hero.png" alt="hero"></p>'
        '<p><a href="https://acme-leads.io/pricing">See pricing</a></p>'
    )
    await email_service.send_now(
        db, contact, subject="Rich", text_body="plain fallback", html_body=html,
        account=account,
    )
    payload = sent[0]["payload"]
    assert "img src=" in payload["htmlContent"]
    assert "acme-leads.io/pricing" in payload["htmlContent"]
    assert payload["textContent"].startswith("plain fallback")


@pytest.mark.asyncio
async def test_html_only_email_gets_a_text_fallback(db, monkeypatch):
    """A body written purely as HTML must still go out with a text part."""
    sent: list = []
    _fake_brevo(monkeypatch, sent)
    account = await _account(db)
    db.add(Contact(phone_number="+15550000109", email="htmlonly@acme-leads.io"))
    await db.commit()
    contact = (await db.execute(select(Contact))).scalar_one()

    message, result = await email_service.send_now(
        db, contact, subject="HTML only", text_body="",
        html_body="<p>Hi <b>Ada</b>,</p><p>Plain fallback please.</p>",
        account=account,
    )
    assert result["success"] is True
    assert "Hi Ada" in sent[0]["payload"]["textContent"]


def test_html_to_text_strips_markup_and_entities():
    from app.services.email_service import html_to_text

    text = html_to_text(
        "<p>Hi&nbsp;<b>Ada</b>,</p><p>See <a href=\"x\">pricing</a>.</p>"
        "<style>p{color:red}</style>"
    )
    assert "Hi Ada" in text
    assert "See pricing" in text
    assert "<" not in text and "color:red" not in text
