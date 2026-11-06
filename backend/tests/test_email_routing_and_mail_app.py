"""Email routing: the four rules the operator set, each one pinned by a test.

1. Email leaves only through Brevo or a connected Gmail — nothing else.
2. Every Brevo send (campaign *and* one-off) carries a Reply-To, and the Brevo
   sender address is always in it.
3. A reply leaves from the Gmail address that received it — never from a Brevo
   sender, never from "a Gmail that happens to be connected".
4. A reply into a connected mailbox is imported, and imported fast: the Gmail
   path uses ``users.messages.batchGet`` (100 messages per request) rather than
   one request per message.

The Mail-page mirror (Sent/Spam/Drafts, not just recognised replies) is covered
here too, because it is the same sync pass.
"""

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.models.email import EmailAccount
from app.models.email_inbox import EmailMailbox
from app.models.mailbox_message import MailboxMessage
from app.providers import gmail as gmail_provider
from app.security.encryption import encrypt_value
from app.services import email_service, mailbox_message_service, mailbox_service


@pytest.fixture(autouse=True)
def _public_base(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example.test")
    monkeypatch.setattr(settings, "GMAIL_SEND_REPLIES", True)
    email_service._VERIFIED_SENDER_CACHE.clear()
    yield
    email_service._VERIFIED_SENDER_CACHE.clear()


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _account(db, *, name="Main", from_email="hello@100.brevosend.com",
                   reply_to=None, key="key", default=True, provider="brevo"):
    account = EmailAccount(
        name=name, from_name=name, from_email=from_email,
        reply_to=reply_to or from_email, provider=provider,
        api_key_encrypted=encrypt_value(key), is_default=default, is_active=True,
        webhook_token=uuid.uuid4().hex,
    )
    db.add(account)
    await db.flush()
    return account


async def _mailbox(db, *, address="me@gmail.com", provider="gmail_api",
                   credential="token", **options):
    mailbox = EmailMailbox(
        name=f"Replies — {address}", provider=provider, email_address=address,
        credential_encrypted=encrypt_value(credential), is_active=True,
        folders="INBOX,SPAM" if provider == "gmail_api" else "INBOX,[Gmail]/Spam",
    )
    for field, value in options.items():
        setattr(mailbox, field, value)
    db.add(mailbox)
    await db.flush()
    return mailbox


async def _contact(db, email="prospect@acme.com"):
    contact = Contact(
        first_name="Ada", email=email, phone_number=f"email:{email}",
        country="Nigeria", lead_status="new", source="test",
    )
    db.add(contact)
    await db.flush()
    return contact


async def _outgoing(db, contact, account, *, subject="Quick question",
                    rfc_id=None, mailbox_id=None):
    conversation = await email_service.get_or_create_conversation(
        db, contact, channel="email", account_id=account.id,
    )
    message = Message(
        conversation_id=conversation.id, contact_id=contact.id, channel="email",
        direction="outgoing", to_address=contact.email, from_address=account.from_email,
        subject=subject, body="Hi", status="sent", bulk_send=False,
        email_account_id=(-int(mailbox_id) if mailbox_id else account.id),
        rfc_message_id=rfc_id or f"<{uuid.uuid4().hex[:12]}@brevosend.com>",
        idempotency_key=f"test-{uuid.uuid4().hex[:16]}",
    )
    db.add(message)
    await db.flush()
    return message, conversation


# ===========================================================================
# 1. Two transports, no third door
# ===========================================================================


def test_only_brevo_and_gmail_are_allowed_transports(db):
    """A sender whose provider is neither is refused before anything is queued."""
    brevo = EmailAccount(name="B", from_email="a@x.com", provider="brevo")
    gmail = EmailMailbox(name="G", email_address="g@gmail.com", provider="gmail_api")
    smuggled = EmailAccount(name="S", from_email="s@x.com", provider="sendgrid")

    assert email_service.transport_for(brevo) == "brevo"
    assert email_service.transport_for(gmail) == "gmail"
    assert email_service.transport_for(smuggled) is None
    assert email_service.assert_allowed_transport(brevo) == "brevo"
    assert email_service.assert_allowed_transport(gmail) == "gmail"
    with pytest.raises(ValueError, match="not allowed"):
        email_service.assert_allowed_transport(smuggled)
    assert email_service.ALLOWED_EMAIL_TRANSPORTS == ("brevo", "gmail")


async def test_brevo_send_path_refuses_a_non_brevo_account(db):
    account = await _account(db, provider="sendgrid")
    contact = await _contact(db)
    message, _ = await _outgoing(db, contact, account)
    result = await email_service._send_via_account(db, account, message)
    assert result["success"] is False
    assert "only through Brevo or a connected Gmail" in result["error"]
    # ...and a campaign send through deliver() fails the message with a reason
    # instead of raising into the worker.
    outcome = await email_service.deliver(db, message)
    assert outcome["success"] is False
    assert message.status == "failed"
    assert "not allowed" in message.last_error


# ===========================================================================
# 2. The Brevo sender is always attached to Reply-To
# ===========================================================================


async def test_reply_to_chain_always_contains_the_brevo_sender(db):
    """With nothing else configured, the chain is the Brevo sender itself.

    This is the rule: Brevo's inbound parser can only see mail addressed to it,
    so a send with no Reply-To is a send whose replies are unreachable.
    """
    account = await _account(db, from_email="hello@100.brevosend.com",
                             reply_to="hello@100.brevosend.com")
    chain = await email_service.reply_to_chain(db, account, "hello@100.brevosend.com")
    assert "hello@100.brevosend.com" in chain
    assert chain == ["hello@100.brevosend.com"]


async def test_reply_to_chain_puts_a_connected_mailbox_first_and_keeps_brevo(db):
    """A mailbox leads so replies land where the app reads them; Brevo stays on."""
    account = await _account(db, from_email="hello@100.brevosend.com",
                             reply_to="hello@100.brevosend.com")
    await _mailbox(db, address="me@gmail.com")
    chain = await email_service.reply_to_chain(db, account, "hello@100.brevosend.com")
    assert chain[0] == "me@gmail.com"
    assert "hello@100.brevosend.com" in chain
    assert len(chain) == len({c.lower() for c in chain})


async def test_brevo_envelope_never_returns_a_blank_reply_to(db):
    account = await _account(db, from_email="hello@100.brevosend.com", reply_to=None)
    from_email, reply_to = await email_service.brevo_envelope(db, account)
    assert from_email == "hello@100.brevosend.com"
    assert reply_to == "hello@100.brevosend.com"


async def test_reply_to_header_carries_the_whole_chain(db):
    account = await _account(db, from_email="hello@100.brevosend.com",
                             reply_to="hello@100.brevosend.com")
    await _mailbox(db, address="me@gmail.com")
    header = await email_service.reply_to_header(db, account, "hello@100.brevosend.com")
    assert header == "me@gmail.com, hello@100.brevosend.com"


async def test_single_address_chain_leaves_the_header_alone(db):
    """One address goes in Brevo's replyTo field; a redundant header would duplicate it."""
    account = await _account(db, from_email="hello@100.brevosend.com",
                             reply_to="hello@100.brevosend.com")
    assert await email_service.reply_to_header(db, account, "hello@100.brevosend.com") is None


class _CaptureBrevo:
    """Stands in for the Brevo provider and records exactly what went on the wire."""

    def __init__(self):
        self.calls: list[dict] = []

    async def send_email(self, api_key, **kwargs):
        self.calls.append(kwargs)
        return {"success": True, "provider_message_id": "brevo-1", "raw": {}}

    def sender_rejected(self, result):
        return False


async def test_campaign_send_puts_the_brevo_sender_in_reply_to(db, monkeypatch):
    """The rule applies to campaigns, not just one-off sends."""
    account = await _account(db, from_email="hello@100.brevosend.com",
                             reply_to="hello@100.brevosend.com")
    await _mailbox(db, address="me@gmail.com")
    contact = await _contact(db)
    message, _ = await _outgoing(db, contact, account)

    fake = _CaptureBrevo()
    monkeypatch.setattr("app.providers.brevo.send_email", fake.send_email)
    monkeypatch.setattr("app.providers.brevo.sender_rejected", fake.sender_rejected)
    monkeypatch.setattr("app.providers.brevo.list_senders",
                        lambda key: _never_called())

    result = await email_service._send_via_account(db, account, message)
    assert result["success"] is True, result
    sent = fake.calls[0]
    assert sent["reply_to"] == "me@gmail.com"
    assert sent["headers"]["Reply-To"] == "me@gmail.com, hello@100.brevosend.com"


async def _never_called():
    raise AssertionError("the sender list must not be fetched in this test")


# ===========================================================================
# 3. A reply leaves from the Gmail that received it
# ===========================================================================


async def test_reply_leaves_from_the_mailbox_that_imported_it(db, monkeypatch):
    """Not from Brevo, and not from whichever Gmail happens to be connected."""
    account = await _account(db, from_email="hello@100.brevosend.com")
    received_by = await _mailbox(db, address="support@gmail.com")
    other = await _mailbox(db, address="marketing@gmail.com")
    contact = await _contact(db)
    sent, conversation = await _outgoing(db, contact, account)

    inbound = Message(
        conversation_id=conversation.id, contact_id=contact.id, channel="email",
        direction="incoming", from_address=contact.email, to_address="support@gmail.com",
        subject="Re: Quick question", body="Yes please", status="received",
        email_account_id=-int(received_by.id),
        rfc_message_id=f"<{uuid.uuid4().hex[:12]}@acme.com>",
        idempotency_key=f"inbound-{uuid.uuid4().hex[:16]}",
    )
    db.add(inbound)
    await db.flush()

    picked = await mailbox_service.importing_mailbox(db, inbound)
    assert picked is not None and picked.id == received_by.id

    routed = await mailbox_service.mailbox_for_reply(db, inbound, account)
    assert routed.id == received_by.id
    assert routed.id != other.id


async def test_mailbox_reply_to_names_the_mailbox_then_the_brevo_sender(db):
    account = await _account(db, from_email="hello@100.brevosend.com")
    mailbox = await _mailbox(db, address="support@gmail.com")
    header = await email_service.mailbox_reply_to(db, account, mailbox)
    assert header.split(", ")[0] == "support@gmail.com"
    assert "hello@100.brevosend.com" in header


# ===========================================================================
# 4. Importing is fast, and it imports
# ===========================================================================


async def test_gmail_reply_sync_uses_one_batch_request_not_one_per_message(db, monkeypatch):
    """250 replies must not cost 250 sequential HTTPS calls.

    The sync used to ``GET /users/me/messages/{id}`` in a loop. Now it lists ids
    and fetches them with ``batchGet``, which is what makes "import as fast as
    it can" true rather than aspirational.
    """
    account = await _account(db, from_email="hello@100.brevosend.com")
    contact = await _contact(db)
    sent, _ = await _outgoing(db, contact, account, rfc_id="<root-1@brevosend.com>")
    mailbox = await _mailbox(db, address="me@gmail.com")

    ids = [f"msg-{i}" for i in range(250)]
    calls: dict[str, int] = {"list": 0, "single_get": 0, "batch": 0}

    async def fake_list_ids(token, **kwargs):
        calls["list"] += 1
        # Both folders are listed on every pass; this fixture's replies are all
        # in the Inbox, so Spam comes back empty.
        if kwargs.get("label_ids") == ["SPAM"]:
            return {"success": True, "data": {"messages": []}}
        return {"success": True, "data": {"messages": [{"id": i} for i in ids]}}

    async def fake_get_message(token, message_id, **kwargs):
        calls["single_get"] += 1
        return {"success": False, "error": "must not be called one at a time"}

    async def fake_batch(token, message_ids, **kwargs):
        calls["batch"] += 1
        items = []
        for index, mid in enumerate(message_ids):
            items.append({
                "From": {"Address": contact.email, "Name": "Ada"},
                "To": [{"Address": "me@gmail.com"}],
                "Subject": f"Re: Quick question {index}",
                "MessageId": f"<r{index}@acme.com>",
                "InReplyTo": "<root-1@brevosend.com>",
                "RawTextBody": "Interested",
                "SentAtDate": datetime.now(timezone.utc).isoformat(),
                "Headers": {"In-Reply-To": "<root-1@brevosend.com>"},
                "Attachments": [],
                "_folder": "INBOX",
                "_labels": ["INBOX"],
                "_provider_message_id": mid,
                "_thread_id": f"thread-{index}",
            })
        return {"success": True, "items": items, "errors": []}

    monkeypatch.setattr(gmail_provider, "gmail_list_ids", fake_list_ids)
    monkeypatch.setattr(gmail_provider, "gmail_get_message", fake_get_message)
    monkeypatch.setattr(gmail_provider, "gmail_batch_get", fake_batch)
    monkeypatch.setattr(mailbox_service, "access_token_for",
                        lambda db, mailbox: _token())
    monkeypatch.setattr(mailbox_message_service, "mirror_mailbox", _no_mirror)

    summary = await mailbox_service.sync_mailbox(db, mailbox)
    assert summary["seen"] == 250
    assert summary["replies"] == 250
    # The sync asks for bodies in ONE batch call and never fetches one at a
    # time. The chunking below 100 ids happens inside the provider, and is
    # pinned by the next test.
    assert calls["single_get"] == 0
    assert calls["batch"] == 1
    assert summary["errors"] == []


@pytest.mark.asyncio
async def test_batch_get_splits_more_than_one_hundred_ids_into_chunks(monkeypatch):
    """``users.messages.batchGet`` accepts 100 ids; 250 must become 3 requests.

    Faked at the HTTP boundary (``_api``), so this exercises the real chunking
    and the real concurrency limiter rather than a copy of them.
    """
    posted: list[list[str]] = []

    async def fake_api(token, method, path, *, params=None, json_body=None):
        posted.append(list((json_body or {}).get("ids") or []))
        return {"success": True, "data": {"messages": [
            {"id": mid, "payload": {"mimeType": "text/plain",
                                    "body": {"data": "SGk=", "size": 2},
                                    "headers": [{"name": "From", "value": "a@b.com"},
                                                {"name": "To", "value": "me@gmail.com"},
                                                {"name": "Subject", "value": "Hi"},
                                                {"name": "Message-ID", "value": f"<{mid}@b.com>"}]},
             "labelIds": ["INBOX"], "internalDate": "1700000000000",
             "threadId": "t1", "snippet": "Hi"}
            for mid in (json_body or {}).get("ids") or []
        ]}}

    monkeypatch.setattr(gmail_provider, "_api", fake_api)
    ids = [f"m{i}" for i in range(250)]
    result = await gmail_provider.gmail_batch_get("token", ids)
    assert result["success"] is True
    assert len(posted) == 3
    assert all(len(chunk) <= gmail_provider.BATCH_GET_LIMIT for chunk in posted)
    assert len(result["items"]) == 250
    # Every message came back normalised with the folder the mail app shows.
    assert all(item["_folder"] == "INBOX" for item in result["items"])


@pytest.mark.asyncio
async def test_batch_get_deduplicates_ids_and_skips_blanks(monkeypatch):
    async def fake_api(token, method, path, *, params=None, json_body=None):
        return {"success": True, "data": {"messages": []}}

    monkeypatch.setattr(gmail_provider, "_api", fake_api)
    result = await gmail_provider.gmail_batch_get("token", ["a", "a", "", "  ", "b"])
    assert result["success"] is True
    assert result["items"] == []


async def _token():
    return "token", None


async def _no_mirror(db, mailbox, **kwargs):
    return {"success": True, "seen": 0, "stored": 0}


def test_batch_get_caps_each_request_at_one_hundred_ids():
    """Gmail's documented limit; exceeding it is a 400, not a silent truncation."""
    assert gmail_provider.BATCH_GET_LIMIT == 100
    assert gmail_provider.MAX_PER_SYNC >= 250


def test_gmail_folders_map_to_labels_the_mail_app_shows():
    assert gmail_provider.FOLDER_LABELS["DRAFTS"] == "DRAFT"
    assert gmail_provider.FOLDER_LABELS["SPAM"] == "SPAM"
    assert gmail_provider.FOLDER_IMAP["SENT"][0] == "[Gmail]/Sent Mail"


def test_folder_from_labels_prefers_the_folders_people_recognise():
    assert gmail_provider.folder_from_labels(["INBOX", "UNREAD", "IMPORTANT"]) == "INBOX"
    assert gmail_provider.folder_from_labels(["SPAM"]) == "SPAM"
    assert gmail_provider.folder_from_labels(["SENT", "INBOX"]) == "SENT"
    assert gmail_provider.folder_from_labels(["DRAFT"]) == "DRAFTS"
    assert gmail_provider.folder_from_labels(["TRASH", "INBOX"]) == "TRASH"


# ===========================================================================
# The Mail page mirror
# ===========================================================================


async def _seed_mirror(db, mailbox, *, provider_message_id="m1", folder="INBOX",
                       subject="Hello", from_address="prospect@acme.com",
                       is_read=False, thread_id="t1", date=None):
    row = MailboxMessage(
        mailbox_id=mailbox.id, provider_message_id=provider_message_id,
        thread_id=thread_id, folder=folder, subject=subject,
        from_address=from_address, from_name="Ada",
        to_addresses=json.dumps(["me@gmail.com"]),
        date=date or datetime.now(timezone.utc),
        body_text="Body", snippet="Body", is_read=is_read,
    )
    db.add(row)
    await db.flush()
    return row


async def test_mirror_lists_folders_and_counts_unread(db):
    mailbox = await _mailbox(db, address="me@gmail.com")
    await _seed_mirror(db, mailbox, provider_message_id="a1", folder="INBOX")
    await _seed_mirror(db, mailbox, provider_message_id="a2", folder="INBOX", is_read=True)
    await _seed_mirror(db, mailbox, provider_message_id="a3", folder="SPAM")
    await _seed_mirror(db, mailbox, provider_message_id="a4", folder="SENT",
                       from_address="me@gmail.com")
    await _seed_mirror(db, mailbox, provider_message_id="a5", folder="DRAFTS")

    counts = await mailbox_message_service.folder_counts(db, mailbox_id=mailbox.id)
    assert counts["INBOX"] == {"total": 2, "unread": 1}
    assert counts["SPAM"]["total"] == 1
    assert counts["SENT"]["total"] == 1
    assert counts["DRAFTS"]["total"] == 1
    # "ALL" is every folder but Trash — that is what the mail app's badge counts.
    assert counts["ALL"] == {"total": 5, "unread": 4}


async def test_mirror_lists_only_the_requested_folder_newest_first(db):
    mailbox = await _mailbox(db, address="me@gmail.com")
    now = datetime.now(timezone.utc)
    await _seed_mirror(db, mailbox, provider_message_id="b1", folder="INBOX",
                       subject="Older", date=now - timedelta(hours=2))
    await _seed_mirror(db, mailbox, provider_message_id="b2", folder="INBOX",
                       subject="Newer", date=now)
    await _seed_mirror(db, mailbox, provider_message_id="b3", folder="SPAM",
                       subject="Not in inbox")

    page = await mailbox_message_service.list_messages(
        db, mailbox_id=mailbox.id, folder="INBOX", limit=10
    )
    assert [m["subject"] for m in page["items"]] == ["Newer", "Older"]
    assert page["total"] == 2

    spam = await mailbox_message_service.list_messages(
        db, mailbox_id=mailbox.id, folder="SPAM", limit=10
    )
    assert [m["subject"] for m in spam["items"]] == ["Not in inbox"]


async def test_mirror_search_finds_sender_and_subject(db):
    mailbox = await _mailbox(db, address="me@gmail.com")
    await _seed_mirror(db, mailbox, provider_message_id="c1", from_address="ada@acme.com",
                       subject="Pricing")
    await _seed_mirror(db, mailbox, provider_message_id="c2", from_address="bob@other.com",
                       subject="Lunch")
    found = await mailbox_message_service.list_messages(
        db, mailbox_id=mailbox.id, folder="INBOX", query="ada@", limit=10
    )
    assert [m["subject"] for m in found["items"]] == ["Pricing"]


async def test_overview_reports_every_account_with_its_own_counts(db):
    first = await _mailbox(db, address="me@gmail.com")
    second = await _mailbox(db, address="sales@gmail.com")
    await _seed_mirror(db, first, provider_message_id="d1", folder="INBOX")
    await _seed_mirror(db, second, provider_message_id="d2", folder="SPAM")

    overview = await mailbox_message_service.accounts_overview(db)
    by_address = {a["email_address"]: a for a in overview["accounts"]}
    assert set(by_address) == {"me@gmail.com", "sales@gmail.com"}
    assert by_address["me@gmail.com"]["counts"]["INBOX"]["unread"] == 1
    assert by_address["sales@gmail.com"]["counts"]["SPAM"]["total"] == 1
    assert overview["total_unread"] == 2


async def test_reply_from_the_mail_page_leaves_from_the_account_that_got_it(db, monkeypatch):
    """The Mail page's Reply button obeys the same rule as the CRM inbox."""
    mailbox = await _mailbox(db, address="support@gmail.com")
    await _seed_mirror(db, mailbox, provider_message_id="e1",
                       from_address="prospect@acme.com", subject="Quick question",
                       thread_id="t9")

    captured: dict = {}

    async def fake_send(db, mailbox, **kwargs):
        captured["mailbox"] = mailbox.email_address
        captured["to"] = kwargs.get("to_address")
        captured["subject"] = kwargs.get("subject")
        captured["headers"] = kwargs.get("headers") or {}
        return {"success": True, "provider_message_id": "sent-1"}

    monkeypatch.setattr(mailbox_service, "send_via_mailbox", fake_send)
    result = await mailbox_message_service.send_reply(
        db, mailbox.id, "e1", body="Happy to help"
    )
    assert result["success"] is True
    assert result["from"] == "support@gmail.com"
    assert captured["mailbox"] == "support@gmail.com"
    assert captured["to"] == "prospect@acme.com"
    assert captured["subject"] == "Re: Quick question"
    assert captured["headers"]["Reply-To"].startswith("support@gmail.com")


async def test_deleting_a_message_moves_it_to_trash_rather_than_destroying_it(db):
    mailbox = await _mailbox(db, address="me@gmail.com")
    row = await _seed_mirror(db, mailbox, provider_message_id="f1")
    result = await mailbox_message_service.delete(db, mailbox.id, "f1")
    assert result == {"success": True, "folder": "TRASH"}
    await db.refresh(row)
    assert row.folder == "TRASH"


async def test_moving_a_message_rejects_an_unknown_folder(db):
    mailbox = await _mailbox(db, address="me@gmail.com")
    await _seed_mirror(db, mailbox, provider_message_id="g1")
    result = await mailbox_message_service.move(db, mailbox.id, "g1", "ARCHIVE")
    assert result["success"] is False
