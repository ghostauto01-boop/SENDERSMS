"""Email replies: the connected-mailbox pipeline, and the unsubscribe button.

Two problems are covered here, because they share one code path.

**"My prospect's reply lands in my spam folder and never shows up in the app."**
A reply reaches the app through exactly one of two doors: Brevo inbound parsing
(which only sees domains whose MX points at Brevo — impossible without owning a
domain), or a connected mailbox the app reads itself. These tests pin the second
door down end to end: Gmail/IMAP bytes → a normalized item → the same
``process_inbound_email`` the Brevo webhook uses → a message on the right
conversation, rescued out of Spam on the way. They also pin the Brevo door's
field names, which were wrong (``TextBody``/``Date``/``inReplyTo`` instead of
``RawTextBody``/``SentAtDate``/``InReplyTo``) and so silently stored empty
bodies on a fresh thread.

**"Just add a button."** The unsubscribe footer used to be a two-sentence
paragraph stapled onto every email, including one-to-one replies. It is now a
single small button, emitted on bulk mail only.

Everything runs offline: IMAP/SMTP/Gmail REST are monkeypatched at the provider
boundary, so the assertions are about our behaviour, not Google's.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base, get_db
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.models.email import EmailAccount
from app.models.email_inbox import EmailContactAddress, EmailMailbox
from app.models.user import User
from app.models.notification import NotificationEvent
from app.api.v1 import mailbox as mailbox_api
from app.providers import gmail as gmail_provider
from app.security.auth import get_current_user
from app.security.encryption import encrypt_value
from app.services import email_service, mailbox_service


# ==========================================================================
# Fixtures
# ==========================================================================


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def public_base(monkeypatch):
    """Unsubscribe links, the routing report and OAuth redirects all need this."""
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example.test")
    monkeypatch.setattr(settings, "GMAIL_SEND_REPLIES", True)
    yield


async def _account(db, *, from_email="me@gmail.com", reply_to=None, name="Main") -> EmailAccount:
    account = EmailAccount(
        name=name,
        from_name="Me",
        from_email=from_email,
        reply_to=reply_to or from_email,
        api_key_encrypted=encrypt_value("key-main"),
        is_default=True,
        is_active=True,
    )
    db.add(account)
    await db.flush()
    return account


async def _contact(db, *, email="prospect@acme.com", name="Ada") -> Contact:
    contact = Contact(
        first_name=name, email=email, phone_number=f"email:{email}"[:20],
        country="Nigeria", lead_status="new", source="test",
    )
    db.add(contact)
    await db.flush()
    return contact


async def _mailbox(db, *, provider="imap", address="me@gmail.com", **options) -> EmailMailbox:
    mailbox = await mailbox_service.create_mailbox(
        db, name=f"Replies — {address}", email_address=address, provider=provider,
        credential="abcd efgh ijkl mnop", **options,
    )
    await db.flush()
    return mailbox


async def _outgoing(db, contact, *, rfc_id="<sent-1@app.example.test>", subject="Quick question",
                    campaign_id=None, bulk=False) -> Message:
    conversation = await email_service.get_or_create_conversation(db, contact, channel="email")
    message = Message(
        conversation_id=conversation.id, contact_id=contact.id, channel="email",
        direction="outgoing", subject=subject, body="Hi Ada, are you open to a chat?",
        status="sent", provider="brevo", rfc_message_id=rfc_id, bulk_send=bulk,
        campaign_id=campaign_id, idempotency_key=f"test-{uuid.uuid4().hex[:16]}",
    )
    db.add(message)
    await db.flush()
    conversation.message_count = (conversation.message_count or 0) + 1
    return message


# ==========================================================================
# 1. The unsubscribe button (and nothing else)
# ==========================================================================


@pytest.mark.asyncio
async def test_bulk_email_carries_a_button_not_a_paragraph(db):
    contact = await _contact(db)
    account = await _account(db, from_email="hello@acme-leads.io")
    rendered = await email_service.render_email(
        db, contact, subject="Hi {{first_name}}", text_body="Hello", html_body="<p>Hello</p>",
        account=account, append_unsubscribe=True,
    )
    assert rendered["html"].count(">Unsubscribe</a>") == 1
    assert "/api/v1/email/unsubscribe?e=hello%40acme-leads.io" in rendered["html"]
    assert rendered["text"].strip().endswith("Unsubscribe: https://app.example.test/api/v1/email/unsubscribe?e=hello%40acme-leads.io&t=" +
                                             email_service._unsubscribe_token("hello@acme-leads.io"))
    # The old wording must be gone for good — it read as a legal paragraph.
    for phrase in ("click here", "no longer wish", "will no longer", "If you'd prefer"):
        assert phrase.lower() not in rendered["html"].lower()
        assert phrase.lower() not in rendered["text"].lower()


@pytest.mark.asyncio
async def test_one_to_one_email_carries_no_visible_unsubscribe(db):
    """A personal reply that says "Unsubscribe" is how cold mail gets reported."""
    contact = await _contact(db)
    account = await _account(db)
    rendered = await email_service.render_email(
        db, contact, subject="Re: Quick question", text_body="Thanks Ada",
        html_body="<p>Thanks Ada</p>", account=account, append_unsubscribe=False,
    )
    assert "Unsubscribe" not in rendered["html"]
    assert "Unsubscribe" not in rendered["text"]


@pytest.mark.asyncio
async def test_list_unsubscribe_header_still_present_for_bulk_only(db):
    """Gmail/Yahoo bulk-sender rules require the header even with a button."""
    contact = await _contact(db)
    account = await _account(db, from_email="hello@acme-leads.io")
    conversation = await email_service.get_or_create_conversation(db, contact, channel="email")
    message = Message(conversation_id=conversation.id, contact_id=contact.id, channel="email",
                      direction="outgoing", subject="Bulk", body="x", to_address=contact.email,
                      idempotency_key=f"test-{uuid.uuid4().hex[:16]}")
    db.add(message)
    await db.flush()

    bulk_headers = await email_service.build_headers(db, message, account, bulk=True)
    assert "List-Unsubscribe" in bulk_headers
    assert "List-Unsubscribe-Post" in bulk_headers

    personal = await email_service.build_headers(db, message, account, bulk=False)
    assert "List-Unsubscribe" not in personal


def test_apply_unsubscribe_leaves_the_body_alone_without_a_base_url(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    text, html = email_service.apply_unsubscribe("Hello", "<p>Hello</p>", "me@acme.io")
    assert text == "Hello"
    assert html == "<p>Hello</p>"


# ==========================================================================
# 2. Brevo inbound parsing: the real field names
# ==========================================================================


BREVO_REPLY = {
    "Uuid": ["2f1c9a4e-6b7d-4c1a-9f0e-8d7c6b5a4321"],
    "MessageId": "<reply-1@acme.com>",
    "InReplyTo": "<sent-1@app.example.test>",
    "References": "<sent-1@app.example.test>",
    "From": {"Address": "prospect@acme.com", "Name": "Ada Lovelace"},
    "ReplyTo": {"Address": "prospect@acme.com", "Name": "Ada Lovelace"},
    "To": [{"Address": "me@gmail.com", "Name": "Me"}],
    "Recipients": [{"Address": "me@gmail.com"}],
    "SentAtDate": "Mon, 06 Oct 2025 09:14:22 +0100",
    "Subject": "Re: Quick question",
    "RawTextBody": "Yes, let's talk Thursday.\n\nOn Mon, 6 Oct 2025, Me wrote:\n> Hi Ada, are you open to a chat?\n",
    "RawHtmlBody": "<p>Yes, let's talk Thursday.</p>",
    "ExtractedMarkdownMessage": "",
    "Spam": {"Score": 0.1},
    "Attachments": [{"Name": "brief.pdf", "ContentType": "application/pdf",
                     "ContentLength": 2048, "DownloadToken": "tok-abc123"}],
}


@pytest.mark.asyncio
async def test_brevo_reply_is_read_threaded_and_dated(db):
    """Regression: `TextBody`/`Date`/`inReplyTo` were never what Brevo sends."""
    contact = await _contact(db)
    await _outgoing(db, contact)

    result = await email_service.process_inbound_email(db, {"items": [BREVO_REPLY]})
    assert result["stored"] == 1

    stored = (await db.execute(
        select(Message).where(Message.direction == "incoming")
    )).scalars().first()
    assert stored is not None
    assert stored.body.startswith("Yes, let's talk Thursday.")
    assert "On Mon, 6 Oct 2025" not in stored.body        # quoted history stripped
    assert stored.subject == "Re: Quick question"
    assert stored.in_reply_to == "<sent-1@app.example.test>"
    assert stored.provider_message_id == "<reply-1@acme.com>"
    assert stored.from_address == "prospect@acme.com"
    assert stored.to_address == "me@gmail.com"
    assert stored.created_at.year == 2025 and stored.created_at.month == 10

    # Threading: the reply joins the conversation the campaign started.
    outgoing = (await db.execute(
        select(Message).where(Message.direction == "outgoing")
    )).scalars().first()
    assert stored.conversation_id == outgoing.conversation_id

    # The contact is no longer "new" — they replied.
    refreshed = (await db.execute(select(Contact).where(Contact.id == contact.id))).scalar_one()
    assert refreshed.lead_status == "replied"
    assert refreshed.last_email_reply_at is not None


@pytest.mark.asyncio
async def test_brevo_reply_is_idempotent(db):
    contact = await _contact(db)
    await _outgoing(db, contact)
    first = await email_service.process_inbound_email(db, {"items": [BREVO_REPLY]})
    second = await email_service.process_inbound_email(db, {"items": [BREVO_REPLY]})
    assert first["stored"] == 1
    assert second["stored"] == 0
    count = (await db.execute(
        select(Message).where(Message.direction == "incoming")
    )).scalars().all()
    assert len(count) == 1


@pytest.mark.asyncio
async def test_attachment_download_token_becomes_a_fetchable_url():
    parsed = email_service.parse_inbound_attachments(BREVO_REPLY)
    assert parsed[0]["name"] == "brief.pdf"
    assert parsed[0]["download_token"] == "tok-abc123"
    assert parsed[0]["url"].endswith("/v3/inbound/attachments/tok-abc123")
    assert parsed[0]["requires_credentials"] is True


@pytest.mark.asyncio
async def test_extracted_markdown_wins_over_the_raw_body(db):
    """Brevo's parser already removes the quote — when it answers, use it."""
    item = dict(BREVO_REPLY, ExtractedMarkdownMessage="Yes, let's talk Thursday.",
                MessageId="<reply-2@acme.com>")
    text, html = email_service._inbound_body(item)
    assert text == "Yes, let's talk Thursday."
    assert html == "<p>Yes, let's talk Thursday.</p>"


# ==========================================================================
# 3. Gmail/IMAP bytes → the same pipeline
# ==========================================================================


def test_rfc822_roundtrip_produces_the_brevo_shape():
    raw, recipients = gmail_provider.build_rfc822(
        from_address="me@gmail.com", from_name="Me", to_address="prospect@acme.com",
        to_name="Ada", subject="Re: Quick question", text="Thursday works.", html=None,
        in_reply_to="<sent-1@app.example.test>", references="<sent-1@app.example.test>",
        message_id="<sent-2@gmail.com>",
    )
    assert recipients == ["prospect@acme.com"]
    item = gmail_provider.parse_mime(raw, folder="[Gmail]/Sent Mail")
    assert item["From"]["Address"] == "me@gmail.com"
    assert item["To"] == [{"Address": "prospect@acme.com"}]
    assert item["Subject"] == "Re: Quick question"
    assert item["RawTextBody"] == "Thursday works."
    assert item["InReplyTo"] == "<sent-1@app.example.test>"
    assert item["_folder"] == "[Gmail]/Sent Mail"
    assert item["_provider"] == "gmail"


REPLY_RAW = (
    b"From: Ada Lovelace <prospect@acme.com>\r\n"
    b"To: Me <me@gmail.com>\r\n"
    b"Subject: Re: Quick question\r\n"
    b"Date: Mon, 06 Oct 2025 09:14:22 +0100\r\n"
    b"Message-ID: <reply-9@acme.com>\r\n"
    b"In-Reply-To: <sent-1@app.example.test>\r\n"
    b"References: <sent-1@app.example.test>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Thursday works for me.\r\n"
)


@pytest.mark.asyncio
async def test_gmail_reply_in_spam_is_rescued_and_imported(db, monkeypatch):
    """The whole complaint in one test: filed in Spam, invisible in the app."""
    contact = await _contact(db)
    account = await _account(db, from_email="me@gmail.com")
    await _outgoing(db, contact)
    mailbox = await _mailbox(db, address="me@gmail.com", rescue_from_spam=True)

    moves: list[dict] = []

    async def fake_imap_sync(address, password, folders, cursors, **kwargs):
        item = gmail_provider.parse_mime(REPLY_RAW, folder="[Gmail]/Spam",
                                         provider_message_id="9")
        item["_uid"] = 9
        return {"items": [item], "cursors": {"[Gmail]/Spam": 10}, "errors": []}

    async def fake_rescue(address, password, uid, folder):
        moves.append({"uid": uid, "folder": folder})
        return {"success": True}

    monkeypatch.setattr(gmail_provider, "imap_sync", fake_imap_sync)
    monkeypatch.setattr(gmail_provider, "imap_rescue_from_spam", fake_rescue)

    summary = await mailbox_service.sync_mailbox(db, mailbox)

    assert summary["seen"] == 1
    assert summary["replies"] == 1
    assert summary["rescued"] == 1
    assert summary["stored"] == 1
    assert moves == [{"uid": 9, "folder": "[Gmail]/Spam"}]

    stored = (await db.execute(
        select(Message).where(Message.direction == "incoming")
    )).scalars().first()
    assert stored.body.startswith("Thursday works for me.")
    assert stored.provider == "gmail"
    outgoing = (await db.execute(
        select(Message).where(Message.direction == "outgoing")
    )).scalars().first()
    assert stored.conversation_id == outgoing.conversation_id
    assert mailbox.total_rescued == 1
    assert mailbox.last_sync_status == "ok"


@pytest.mark.asyncio
async def test_personal_sender_keeps_contact_threads_and_replies_to_that_address(
    db, client, monkeypatch
):
    """A changed/personal sender stays on the campaign contact and is replyable."""
    contact = await _contact(db)
    account = await _account(db, from_email="me@gmail.com")
    outgoing = await _outgoing(db, contact, subject="Quick question")
    mailbox = await _mailbox(db, address="me@gmail.com")
    personal = (
        b"From: Ada <ada.personal@gmail.com>\r\n"
        b"To: Me <me@gmail.com>\r\n"
        b"Subject: Re: Quick question\r\n"
        b"Date: Mon, 06 Oct 2025 09:14:22 +0100\r\n"
        b"Message-ID: <reply-personal@gmail.com>\r\n"
        b"In-Reply-To: <sent-1@app.example.test>\r\n"
        b"References: <sent-1@app.example.test>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Thursday works for me.\r\n"
    )
    async def fake_imap_sync(*args, **kwargs):
        item = gmail_provider.parse_mime(
            personal, folder="INBOX", provider_message_id="personal-reply-1"
        )
        return {"items": [item], "cursors": {}, "errors": []}

    monkeypatch.setattr(gmail_provider, "imap_sync", fake_imap_sync)
    summary = await mailbox_service.sync_mailbox(db, mailbox)
    assert summary["replies"] == 1 and summary["stored"] == 1

    inbound = (await db.execute(
        select(Message).where(Message.direction == "incoming", Message.channel == "email")
    )).scalars().one()
    assert inbound.contact_id == contact.id
    assert inbound.conversation_id == outgoing.conversation_id
    assert inbound.from_address == "ada.personal@gmail.com"
    assert contact.email == "prospect@acme.com"  # primary address is not overwritten
    assert await email_service.contact_email_aliases(db, contact.id) == ["ada.personal@gmail.com"]
    assert (await db.execute(
        select(NotificationEvent).where(
            NotificationEvent.event_type == "email_reply",
            NotificationEvent.reference_id == outgoing.conversation_id,
        )
    )).scalar_one_or_none() is not None

    delivered_to: list[str] = []

    async def fake_deliver(db_session, message):
        delivered_to.append(message.to_address)
        message.status = "sent"
        return {"success": True, "provider_message_id": "<simulated-reply@gmail.com>"}

    monkeypatch.setattr(email_service, "deliver", fake_deliver)
    response = await client.post(
        f"/api/v1/email/inbox/conversations/{outgoing.conversation_id}/reply",
        json={"body": "Thanks, Thursday works for me too."},
    )
    assert response.status_code == 200, response.text
    assert delivered_to == ["ada.personal@gmail.com"]
    assert response.json()["message"]["to_address"] == "ada.personal@gmail.com"

    # The detail endpoint exposes the complete contact record and the retained alias.
    detail = await client.get(
        f"/api/v1/email/inbox/conversations/{outgoing.conversation_id}"
    )
    assert detail.status_code == 200
    assert detail.json()["contact"]["email"] == "prospect@acme.com"
    assert detail.json()["contact"]["email_aliases"] == ["ada.personal@gmail.com"]


@pytest.mark.asyncio
async def test_unrelated_mail_is_not_imported(db, monkeypatch):
    """Importing a whole mailbox into a CRM inbox is never what was meant."""
    await _account(db, from_email="me@gmail.com")
    mailbox = await _mailbox(db, address="me@gmail.com")

    async def fake_imap_sync(*args, **kwargs):
        newsletter = gmail_provider.parse_mime(
            b"From: News <news@shop.com>\r\nTo: me@gmail.com\r\nSubject: 50% off\r\n"
            b"Message-ID: <n1@shop.com>\r\nContent-Type: text/plain\r\n\r\nBuy now\r\n",
            folder="INBOX",
        )
        return {"items": [newsletter], "cursors": {}, "errors": []}

    monkeypatch.setattr(gmail_provider, "imap_sync", fake_imap_sync)
    summary = await mailbox_service.sync_mailbox(db, mailbox)
    assert summary["seen"] == 1 and summary["replies"] == 0 and summary["stored"] == 0
    assert summary["skipped"] == 1


@pytest.mark.asyncio
async def test_import_all_switch_overrides_the_classifier(db, monkeypatch):
    await _account(db, from_email="me@gmail.com")
    mailbox = await _mailbox(db, address="me@gmail.com", import_all=True)

    async def fake_imap_sync(*args, **kwargs):
        item = gmail_provider.parse_mime(
            b"From: News <news@shop.com>\r\nTo: me@gmail.com\r\nSubject: 50% off\r\n"
            b"Message-ID: <n2@shop.com>\r\nContent-Type: text/plain\r\n\r\nBuy now\r\n",
            folder="INBOX",
        )
        return {"items": [item], "cursors": {}, "errors": []}

    monkeypatch.setattr(gmail_provider, "imap_sync", fake_imap_sync)
    summary = await mailbox_service.sync_mailbox(db, mailbox)
    assert summary["stored"] == 1


@pytest.mark.asyncio
async def test_our_own_sent_copy_is_never_imported(db):
    """Otherwise every reply we send loops back into the inbox as a new message."""
    mailbox = await _mailbox(db, address="me@gmail.com")
    item = gmail_provider.parse_mime(
        b"From: Me <me@gmail.com>\r\nTo: prospect@acme.com\r\nSubject: Re: x\r\n"
        b"Message-ID: <s9@gmail.com>\r\nContent-Type: text/plain\r\n\r\nMine\r\n"
    )
    wanted, reason = await mailbox_service.classify_inbound(db, item, mailbox, {"me@gmail.com"})
    assert wanted is False
    assert reason == "from you"


@pytest.mark.asyncio
async def test_known_contact_promotional_mail_is_not_accepted(db):
    """A contact match alone must not import their unrelated newsletter."""
    contact = await _contact(db)
    await _outgoing(db, contact, subject="Quick question")
    mailbox = await _mailbox(db, address="me@gmail.com")
    item = gmail_provider.parse_mime(
        b"From: Ada <prospect@acme.com>\r\nTo: me@gmail.com\r\nSubject: 50% off today\r\n"
        b"Message-ID: <promo1@acme.com>\r\nContent-Type: text/plain\r\n\r\nBuy now\r\n"
    )
    wanted, reason = await mailbox_service.classify_inbound(db, item, mailbox, {"me@gmail.com"})
    assert wanted is False
    assert reason == "known contact, but no matching sent thread"
    assert contact.email == "prospect@acme.com"


@pytest.mark.asyncio
async def test_reply_subject_without_headers_matches_only_mail_sent_to_contact(db):
    """Some clients drop References, but an actual matching reply still lands."""
    contact = await _contact(db)
    await _outgoing(db, contact, subject="Quick question")
    mailbox = await _mailbox(db, address="me@gmail.com")
    item = gmail_provider.parse_mime(
        b"From: Ada <prospect@acme.com>\r\nTo: me@gmail.com\r\nSubject: Re: Quick question\r\n"
        b"Message-ID: <reply-subject@acme.com>\r\nContent-Type: text/plain\r\n\r\nThursday works\r\n"
    )
    wanted, reason = await mailbox_service.classify_inbound(db, item, mailbox, {"me@gmail.com"})
    assert wanted is True
    assert reason == "reply subject matches mail sent to this contact"


@pytest.mark.asyncio
async def test_personal_sender_can_match_an_unambiguous_reply_subject(db):
    """An unlisted Gmail address can still be recognized without thread headers."""
    contact = await _contact(db)
    await _outgoing(db, contact, subject="Quick question")
    mailbox = await _mailbox(db, address="me@gmail.com")
    item = gmail_provider.parse_mime(
        b"From: Ada <ada.personal@gmail.com>\r\nTo: me@gmail.com\r\nSubject: Re: Quick question\r\n"
        b"Message-ID: <personal-subject@gmail.com>\r\nContent-Type: text/plain\r\n\r\nThursday works\r\n"
    )
    wanted, reason = await mailbox_service.classify_inbound(db, item, mailbox, {"me@gmail.com"})
    assert wanted is True
    assert reason == "reply subject uniquely matches mail you sent"


# ==========================================================================
# 4. Gmail filters — "never send it to Spam"
# ==========================================================================


@pytest.mark.asyncio
async def test_never_spam_filter_uses_remove_label_ids(db, monkeypatch):
    """There is no `neverSpam` field: `removeLabelIds:["SPAM"]` IS the checkbox."""
    await _contact(db)
    contact = (await db.execute(select(Contact))).scalars().first()
    contact.last_email_reply_at = datetime.now(timezone.utc)
    mailbox = await _mailbox(db, provider="gmail_api", address="me@gmail.com")
    mailbox.credential_encrypted = encrypt_value("refresh-token")
    mailbox.client_id = "client-id"
    mailbox.client_secret_encrypted = encrypt_value("client-secret")
    mailbox.label_id = "Label_42"

    created: list[dict] = []

    async def fake_refresh(client_id, client_secret, refresh_token):
        return {"success": True, "access_token": "ya29.fake"}

    async def fake_list(token):
        return {"success": True, "data": {"filter": []}}

    async def fake_create(token, criteria, action):
        created.append({"criteria": criteria, "action": action})
        return {"success": True, "data": {"id": f"filter-{len(created)}"}}

    monkeypatch.setattr(gmail_provider, "google_refresh_access_token", fake_refresh)
    monkeypatch.setattr(gmail_provider, "gmail_list_filters", fake_list)
    monkeypatch.setattr(gmail_provider, "gmail_create_filter", fake_create)

    result = await mailbox_service.ensure_never_spam_filters(db, mailbox)
    assert result["success"] is True
    assert result["created"] == 1
    assert created[0]["criteria"] == {"from": "prospect@acme.com", "to": "me@gmail.com"}
    assert created[0]["action"]["removeLabelIds"] == ["SPAM"]
    assert created[0]["action"]["addLabelIds"] == ["Label_42"]
    assert "neverSpam" not in created[0]["action"]


@pytest.mark.asyncio
async def test_imap_mailbox_cannot_create_filters_and_says_why(db):
    mailbox = await _mailbox(db, provider="imap", address="me@gmail.com")
    result = await mailbox_service.ensure_never_spam_filters(db, mailbox)
    assert result["success"] is False
    assert "IMAP" in result["error"]


# ==========================================================================
# 5. Replies leave through the mailbox, campaigns stay on Brevo
# ==========================================================================


@pytest.mark.asyncio
async def test_one_to_one_reply_leaves_through_the_connected_mailbox(db, monkeypatch):
    contact = await _contact(db)
    account = await _account(db, from_email="hello@acme-leads.io", reply_to="me@gmail.com")
    conversation = await email_service.get_or_create_conversation(db, contact, channel="email")
    reply = Message(
        conversation_id=conversation.id, contact_id=contact.id, channel="email",
        direction="outgoing", to_address=contact.email, from_address=account.reply_to,
        subject="Re: Quick question", body="Thursday works.", status="queued",
        bulk_send=False, idempotency_key=f"test-{uuid.uuid4().hex[:16]}",
    )
    db.add(reply)
    await db.flush()
    await _mailbox(db, provider="imap", address="me@gmail.com", send_replies=True)

    sent: list[dict] = []

    async def fake_smtp(address, password, raw, recipients):
        sent.append({"address": address, "raw": raw, "recipients": recipients})
        return {"success": True, "provider_message_id": "smtp-1"}

    monkeypatch.setattr(gmail_provider, "smtp_send", fake_smtp)

    result = await email_service.deliver(db, reply)
    assert result["success"] is True
    assert result["provider"] == "imap"
    assert sent[0]["address"] == "me@gmail.com"
    assert sent[0]["recipients"] == ["prospect@acme.com"]
    assert "Subject: Re: Quick question" in sent[0]["raw"]
    assert reply.status == "sent"
    assert reply.provider == "imap"
    assert reply.sent_at is not None
    # The row must say where the mail actually left from, not the Brevo sender.
    assert reply.from_address == "me@gmail.com"
    # The Brevo daily counter is untouched: this message never went near Brevo.
    assert (account.sent_today or 0) == 0


@pytest.mark.asyncio
async def test_bulk_campaign_mail_stays_on_brevo(db, monkeypatch):
    """Campaigns need Brevo's quotas, tracking and List-Unsubscribe handling."""
    contact = await _contact(db)
    account = await _account(db, from_email="me@gmail.com")
    conversation = await email_service.get_or_create_conversation(db, contact, channel="email")
    bulk = Message(
        conversation_id=conversation.id, contact_id=contact.id, channel="email",
        direction="outgoing", to_address=contact.email, subject="Campaign", body="Hi",
        status="queued", bulk_send=True, idempotency_key=f"test-{uuid.uuid4().hex[:16]}",
    )
    db.add(bulk)
    await db.flush()
    await _mailbox(db, provider="imap", address="me@gmail.com")

    mailbox_choice = await mailbox_service.mailbox_for_outgoing(db, bulk, account)
    assert mailbox_choice is None

    # And the send itself must not reach SMTP.
    called = []

    async def fake_smtp(*args, **kwargs):
        called.append(args)
        return {"success": True}

    monkeypatch.setattr(gmail_provider, "smtp_send", fake_smtp)
    await email_service.deliver(db, bulk)
    assert called == []


@pytest.mark.asyncio
async def test_a_broken_mailbox_falls_back_to_brevo(db, monkeypatch):
    contact = await _contact(db)
    account = await _account(db, from_email="me@gmail.com")
    conversation = await email_service.get_or_create_conversation(db, contact, channel="email")
    reply = Message(
        conversation_id=conversation.id, contact_id=contact.id, channel="email",
        direction="outgoing", to_address=contact.email, subject="Re: x", body="Hi",
        status="queued", bulk_send=False, idempotency_key=f"test-{uuid.uuid4().hex[:16]}",
    )
    db.add(reply)
    await db.flush()
    await _mailbox(db, provider="imap", address="me@gmail.com")

    async def failing_smtp(*args, **kwargs):
        return {"success": False, "error": "535 authentication failed"}

    brevo_calls: list[dict] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, *, headers=None, json=None, **kwargs):
            brevo_calls.append({"url": url, "payload": json or {}})

            class Response:
                status_code = 201
                text = json.dumps({"messageId": "brevo-1"})

                @staticmethod
                def json():
                    return {"messageId": "brevo-1"}

            return Response()

    monkeypatch.setattr(gmail_provider, "smtp_send", failing_smtp)
    monkeypatch.setattr("app.providers.brevo.httpx.AsyncClient", FakeClient)

    result = await email_service.deliver(db, reply)
    assert result["success"] is True
    assert brevo_calls, "the message should have fallen through to Brevo"
    assert reply.provider == "brevo"


# ==========================================================================
# 6. The reply-routing report the UI shows
# ==========================================================================


@pytest.mark.asyncio
async def test_report_names_the_freemail_reply_to_as_the_spam_cause(db):
    await _account(db, from_email="hello@acme-leads.io", reply_to="me@gmail.com")
    report = await mailbox_service.reply_routing_report(db)
    assert report["healthy"] is False
    problems = " ".join(f["where"] for f in report["findings"])
    assert "Reply-To" in problems
    high = [f for f in report["findings"] if f["severity"] == "high"]
    assert any("DMARC" in f["problem"] for f in high)
    assert any("Spam" in f["problem"] for f in high)
    assert report["connected"] == 0
    assert {p["id"] for p in report["paths"]} == {"brevo", "gmail_api", "imap"}
    assert next(p for p in report["paths"] if p["id"] == "gmail_api")["status"] == "not connected"


@pytest.mark.asyncio
async def test_report_goes_quiet_once_a_mailbox_is_connected(db):
    account = await _account(db, from_email="me@gmail.com")
    account.webhook_token = "tok"
    await _mailbox(db, provider="gmail_api", address="me@gmail.com")
    report = await mailbox_service.reply_routing_report(db)
    assert report["connected"] == 1
    assert report["brevo_inbound_ready"] is True
    assert not any(f["where"] == "Reply inbox" for f in report["findings"])
    assert next(p for p in report["paths"] if p["id"] == "gmail_api")["status"] == "connected"


def test_freemail_detection():
    assert mailbox_service.is_freemail("me@gmail.com") is True
    assert mailbox_service.is_freemail("me@GOOGLEMAIL.com") is True
    assert mailbox_service.is_freemail("me@yahoo.com") is True
    assert mailbox_service.is_freemail("hello@acme-leads.io") is False
    assert mailbox_service.is_freemail(None) is False


# ==========================================================================
# 7. The mailbox API
# ==========================================================================


@pytest_asyncio.fixture
async def client(db):
    from app.main import app

    async def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="tester", email="tester@example.com",
        password_hash="x", role="admin", is_active=True,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           follow_redirects=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_api_connects_checks_and_deletes_a_mailbox(client, db, monkeypatch):
    async def fake_imap_check(address, password):
        assert password == "abcdefghijklmnop"
        return {"success": True, "folders": ["INBOX", "[Gmail]/Spam"], "mailbox": "me@gmail.com"}

    async def fake_imap_sync(*args, **kwargs):
        return {"items": [], "cursors": {"INBOX": 4}, "errors": []}

    monkeypatch.setattr(gmail_provider, "imap_check", fake_imap_check)
    monkeypatch.setattr(gmail_provider, "imap_sync", fake_imap_sync)

    created = await client.post("/api/v1/mailbox", json={
        "email_address": "Me@Gmail.com", "app_password": "abcd efgh ijkl mnop",
    })
    assert created.status_code == 200, created.text
    payload = created.json()
    mailbox_id = payload["mailbox"]["id"]
    assert payload["mailbox"]["email_address"] == "me@gmail.com"
    assert payload["mailbox"]["has_credential"] is True
    # The credential must never come back, in any field.
    assert "mnop" not in json.dumps(payload)
    assert "password" not in json.dumps(payload["mailbox"])

    listed = await client.get("/api/v1/mailbox")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    duplicate = await client.post("/api/v1/mailbox", json={
        "email_address": "me@gmail.com", "app_password": "abcdefghijklmnop"})
    assert duplicate.status_code == 409

    checked = await client.post(f"/api/v1/mailbox/{mailbox_id}/check")
    assert checked.status_code == 200 and checked.json()["success"] is True

    patched = await client.patch(f"/api/v1/mailbox/{mailbox_id}",
                                 json={"poll_interval": 120, "import_all": True})
    assert patched.status_code == 200
    assert patched.json()["mailbox"]["poll_interval"] == 120
    assert patched.json()["mailbox"]["import_all"] is True

    removed = await client.delete(f"/api/v1/mailbox/{mailbox_id}")
    assert removed.status_code == 200 and removed.json()["success"] is True
    assert (await client.get(f"/api/v1/mailbox/{mailbox_id}")).status_code == 404


@pytest.mark.asyncio
async def test_api_rejects_a_gmail_password_that_is_not_an_app_password(client, monkeypatch):
    async def never_called(*args, **kwargs):  # pragma: no cover
        raise AssertionError("should not have tried to log in")

    monkeypatch.setattr(gmail_provider, "imap_check", never_called)
    response = await client.post("/api/v1/mailbox", json={
        "email_address": "me@gmail.com", "app_password": "myRealPassword1"})
    assert response.status_code == 400
    assert "app password" in response.json()["detail"]


@pytest.mark.asyncio
async def test_api_reports_missing_google_oauth_configuration(client, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "")
    response = await client.get("/api/v1/mailbox/google/start")
    assert response.status_code == 400
    assert "GOOGLE_CLIENT_ID" in response.json()["detail"]


@pytest.mark.asyncio
async def test_api_builds_a_google_consent_url(client, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "client-secret")
    response = await client.get("/api/v1/mailbox/google/start")
    assert response.status_code == 200
    url = response.json()["url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "access_type=offline" in url          # without this there is no refresh token
    assert "prompt=consent" in url
    assert "redirect_uri=https%3A%2F%2Fapp.example.test%2Fapi%2Fv1%2Fmailbox%2Fgoogle%2Fcallback" in url
    assert "mail.google.com" in url


@pytest.mark.asyncio
async def test_google_callback_rejects_a_forged_state(client):
    user_id, error = mailbox_api._read_state("abc.def")
    assert user_id is None and "malformed" in error

    user_id, error = mailbox_api._read_state(None)
    assert user_id is None and "expired" in error

    good = mailbox_api._make_state(7)
    assert mailbox_api._read_state(good) == (7, None)
    tampered = good[:-2] + ("aa" if not good.endswith("aa") else "bb")
    assert mailbox_api._read_state(tampered)[1] is not None

    response = await client.get("/api/v1/mailbox/google/callback",
                                params={"code": "x", "state": "abc.def"})
    assert response.status_code == 200            # an HTML explainer, never a stack trace
    assert "no longer valid" in response.text


@pytest.mark.asyncio
async def test_google_callback_reports_a_denied_consent(client):
    response = await client.get("/api/v1/mailbox/google/callback", params={
        "error": "access_denied", "state": mailbox_api._make_state(1)})
    assert response.status_code == 200
    assert "cancelled" in response.text


@pytest.mark.asyncio
async def test_report_endpoint_is_reachable(client):
    response = await client.get("/api/v1/mailbox/report")
    assert response.status_code == 200
    assert "findings" in response.json() and "paths" in response.json()


# ==========================================================================
# 8. Polling
# ==========================================================================


@pytest.mark.asyncio
async def test_sync_due_mailboxes_respects_the_poll_interval(db, monkeypatch):
    calls: list[str] = []

    async def fake_imap_sync(*args, **kwargs):
        calls.append("sync")
        return {"items": [], "cursors": {}, "errors": []}

    monkeypatch.setattr(gmail_provider, "imap_sync", fake_imap_sync)
    mailbox = await _mailbox(db, address="me@gmail.com", poll_interval=300)

    await mailbox_service.sync_due_mailboxes(db)      # never synced → due
    assert len(calls) == 1

    await mailbox_service.sync_due_mailboxes(db)      # synced seconds ago → not due
    assert len(calls) == 1

    mailbox.last_sync_at = datetime.now(timezone.utc) - timedelta(seconds=301)
    await mailbox_service.sync_due_mailboxes(db)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_failing_sync_is_recorded_not_raised(db, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("IMAP is unreachable")

    monkeypatch.setattr(gmail_provider, "imap_sync", boom)
    mailbox = await _mailbox(db, address="me@gmail.com")
    summary = await mailbox_service.sync_mailbox(db, mailbox)
    assert summary["success"] is False
    assert mailbox.last_sync_status == "error"
    assert "IMAP is unreachable" in (mailbox.last_error or "")
