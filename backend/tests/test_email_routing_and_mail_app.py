"""Email routing: the four rules the operator set, each one pinned by a test.

1. Email leaves only through Brevo or a connected Gmail — nothing else.
2. Brevo receives one valid Reply-To in its dedicated `replyTo` object; the
   unsupported standard header is never put in the custom headers map.
3. A reply leaves from the Gmail address that received it — never from a Brevo
   sender, never from "a Gmail that happens to be connected".
4. A reply into a connected mailbox is imported through the documented
   `users.messages.get` method with bounded concurrency, not a nonexistent
   batchGet endpoint.

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


async def test_campaign_send_uses_dedicated_reply_to_without_custom_header(db, monkeypatch):
    """Brevo gets one address in its dedicated field, never a Reply-To header."""
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
    assert "Reply-To" not in sent["headers"]


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


async def test_gmail_reply_sync_uses_bounded_message_fetch_for_new_ids(db, monkeypatch):
    """A reply sync lists IDs, then delegates body fetches to the provider helper."""
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

    async def fake_profile(token):
        return {"success": True, "history_id": "history-1"}

    monkeypatch.setattr(gmail_provider, "gmail_profile", fake_profile)
    monkeypatch.setattr(gmail_provider, "gmail_list_all_ids", fake_list_ids)
    monkeypatch.setattr(gmail_provider, "gmail_get_message", fake_get_message)
    monkeypatch.setattr(gmail_provider, "gmail_batch_get", fake_batch)
    monkeypatch.setattr(mailbox_service, "access_token_for",
                        lambda db, mailbox: _token())
    monkeypatch.setattr(mailbox_message_service, "mirror_mailbox", _no_mirror)

    summary = await mailbox_service.sync_mailbox(db, mailbox)
    assert summary["seen"] == 250
    assert summary["replies"] == 250
    # The service hands the complete ID set to the provider's bounded-fetch
    # helper once; that helper issues documented messages.get calls internally.
    assert calls["single_get"] == 0
    assert calls["batch"] == 1
    assert summary["errors"] == []


@pytest.mark.asyncio
async def test_gmail_reply_fetch_does_not_advance_cursor_after_partial_failure(db, monkeypatch):
    mailbox = await _mailbox(db, address="me@gmail.com")
    old_state = {"reply_history_id": "history-old", "since": "2026-11-01T00:00:00+00:00"}
    mailbox.cursor_state = json.dumps(old_state)

    async def fake_access_token(db, mailbox):
        return "access-token", None

    async def fake_history(token, history_id):
        return {"success": True, "data": {
            "history": [{"messagesAdded": [{"message": {"id": "new-message"}}]}],
            "historyId": "history-new",
        }}

    async def fake_batch(token, ids, **kwargs):
        assert ids == ["new-message"]
        return {"success": False, "items": [], "errors": ["new-message: temporary error"]}

    monkeypatch.setattr(mailbox_service, "access_token_for", fake_access_token)
    monkeypatch.setattr(gmail_provider, "gmail_history_changes", fake_history)
    monkeypatch.setattr(gmail_provider, "gmail_batch_get", fake_batch)
    _items, next_state, errors = await mailbox_service._fetch_gmail_api(db, mailbox)
    assert next_state == old_state
    assert errors == ["new-message: temporary error"]


@pytest.mark.asyncio
async def test_gmail_message_fetch_uses_documented_get_with_bounded_concurrency(monkeypatch):
    """Gmail has no batchGet endpoint; 250 IDs become concurrent messages.get calls."""
    import asyncio

    requests: list[tuple[str, str]] = []
    active = 0
    max_active = 0

    async def fake_api(token, method, path, *, params=None, json_body=None, _client=None):
        nonlocal active, max_active
        requests.append((method, path))
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.001)
        active -= 1
        message_id = path.rsplit("/", 1)[-1]
        return {"success": True, "data": {
            "id": message_id,
            "payload": {"mimeType": "text/plain", "body": {"data": "SGk=", "size": 2},
                        "headers": [{"name": "From", "value": "a@b.com"},
                                    {"name": "To", "value": "me@gmail.com"},
                                    {"name": "Subject", "value": "Hi"},
                                    {"name": "Message-ID", "value": f"<{message_id}@b.com>"}]},
            "labelIds": ["INBOX"], "internalDate": "1700000000000",
            "threadId": "t1", "snippet": "Hi",
        }}

    monkeypatch.setattr(gmail_provider, "_api", fake_api)
    ids = [f"m{i}" for i in range(250)]
    result = await gmail_provider.gmail_batch_get("token", ids)
    assert result["success"] is True
    assert len(requests) == 250
    assert all(method == "GET" for method, _path in requests)
    assert all("/users/me/messages/" in path and "batchGet" not in path
               for _method, path in requests)
    assert max_active == gmail_provider.BATCH_CONCURRENCY
    assert len(result["items"]) == 250
    assert all(item["_folder"] == "INBOX" for item in result["items"])


@pytest.mark.asyncio
async def test_gmail_message_get_deduplicates_ids_and_skips_blanks(monkeypatch):
    requested = []

    async def fake_api(token, method, path, *, params=None, json_body=None, _client=None):
        requested.append(path)
        message_id = path.rsplit("/", 1)[-1]
        return {"success": True, "data": {
            "id": message_id,
            "payload": {"headers": [], "body": {"data": "SGk="}},
            "labelIds": [],
        }}

    monkeypatch.setattr(gmail_provider, "_api", fake_api)
    result = await gmail_provider.gmail_batch_get("token", ["a", "a", "", "  ", "b"])
    assert result["success"] is True
    assert len(result["items"]) == 2
    assert len(requested) == 2


@pytest.mark.asyncio
async def test_gmail_list_all_ids_follows_pages_and_deduplicates(monkeypatch):
    calls = []

    async def fake_api(token, method, path, *, params=None, json_body=None, _client=None):
        calls.append((method, params or {}))
        if (params or {}).get("pageToken") == "page-2":
            return {"success": True, "data": {
                "messages": [{"id": "b"}, {"id": "c"}],
            }}
        return {"success": True, "data": {
            "messages": [{"id": "a"}, {"id": "b"}],
            "nextPageToken": "page-2",
        }}

    monkeypatch.setattr(gmail_provider, "_api", fake_api)
    listed = await gmail_provider.gmail_list_all_ids(
        "token", label_ids=["INBOX"], query="after:2026/11/01",
    )
    assert listed["success"] is True
    assert [item["id"] for item in listed["data"]["messages"]] == ["a", "b", "c"]
    assert len(calls) == 2
    assert calls[1][1]["pageToken"] == "page-2"


@pytest.mark.asyncio
async def test_gmail_list_after_uses_search_query_not_history_parameter(monkeypatch):
    seen = {}

    async def fake_api(token, method, path, *, params=None, json_body=None, _client=None):
        seen.update(params or {})
        return {"success": True, "data": {"messages": []}}

    monkeypatch.setattr(gmail_provider, "_api", fake_api)
    result = await gmail_provider.gmail_list(
        "token", query="in:inbox", after="2026-11-05", max_results=50,
    )
    assert result["success"] is True
    assert seen["q"] == "in:inbox after:2026/11/05"
    assert "startHistoryId" not in seen


@pytest.mark.asyncio
async def test_gmail_history_uses_real_plural_fields_and_pages_all_changes(monkeypatch):
    calls = []

    async def fake_api(token, method, path, *, params=None, json_body=None, _client=None):
        calls.append(params or {})
        if (params or {}).get("pageToken") == "history-2":
            return {"success": True, "data": {
                "history": [{"id": "22", "messagesDeleted": [
                    {"message": {"id": "deleted"}},
                ]}],
                "historyId": "22",
            }}
        return {"success": True, "data": {
            "history": [{"id": "21", "messagesAdded": [
                {"message": {"id": "added"}},
            ], "labelsAdded": [{"message": {"id": "labelled"}, "labelIds": ["INBOX"]}]}],
            "nextPageToken": "history-2",
            "historyId": "21",
        }}

    monkeypatch.setattr(gmail_provider, "_api", fake_api)
    result = await gmail_provider.gmail_history_changes("token", "20")
    assert result["success"] is True
    assert result["data"]["historyId"] == "22"
    assert len(result["data"]["history"]) == 2
    assert calls[0]["historyTypes"] == [
        "messageAdded", "messageDeleted", "labelAdded", "labelRemoved",
    ]
    assert "messagesAdded" in calls[0]["fields"]
    assert "labelsAdded" in calls[0]["fields"]
    assert "messageAdded/message/id" not in calls[0]["fields"]


@pytest.mark.asyncio
async def test_gmail_get_downloads_attachment_id_body_and_small_file(monkeypatch):
    import base64

    message = {
        "id": "m1", "threadId": "t1", "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "From", "value": "ada@example.com"},
                       {"name": "To", "value": "me@gmail.com"}],
            "parts": [
                {"mimeType": "text/plain", "body": {"attachmentId": "body-1", "size": 5}},
                {"mimeType": "application/pdf", "filename": "note.pdf",
                 "body": {"attachmentId": "file-1", "size": 3}},
            ],
        },
    }

    requested = []

    async def fake_api(token, method, path, *, params=None, json_body=None, _client=None):
        requested.append(path)
        if path.endswith("/messages/m1"):
            return {"success": True, "data": message}
        if path.endswith("/attachments/body-1"):
            return {"success": True, "data": {"data": gmail_provider._b64url_encode(b"Hello")}}
        if path.endswith("/attachments/file-1"):
            return {"success": True, "data": {"data": gmail_provider._b64url_encode(b"PDF")}}
        raise AssertionError(f"Unexpected Gmail API path: {path}")

    monkeypatch.setattr(gmail_provider, "_api", fake_api)
    result = await gmail_provider.gmail_get_message("token", "m1", include_attachments=True)
    assert result["success"] is True
    assert result["item"]["RawTextBody"] == "Hello"
    attachment = result["item"]["Attachments"][0]
    assert attachment["name"] == "note.pdf"
    assert base64.b64decode(attachment["content"]) == b"PDF"

    requested.clear()
    mirror_item = await gmail_provider.gmail_get_message("token", "m1", include_attachments=False)
    assert mirror_item["success"] is True
    assert mirror_item["item"]["RawTextBody"] == "Hello"
    assert "content" not in mirror_item["item"]["Attachments"][0]
    assert not any(path.endswith("/attachments/file-1") for path in requested)


def test_gmail_payload_caps_inline_attachment_decoding_before_collecting(monkeypatch):
    import base64

    monkeypatch.setattr(gmail_provider, "MAX_IMPORT_ATTACHMENT_BYTES", 4)
    monkeypatch.setattr(gmail_provider, "MAX_IMPORT_ATTACHMENTS_TOTAL_BYTES", 5)
    encoded = [gmail_provider._b64url_encode(raw) for raw in (b"abc", b"def", b"z")]
    decoded_calls = []
    original_decode = gmail_provider._b64url_decode

    def track_decode(value):
        decoded_calls.append(value)
        return original_decode(value)

    monkeypatch.setattr(gmail_provider, "_b64url_decode", track_decode)
    _text, _html, attachments, _headers, _refs, _errors = gmail_provider._walk_payload({
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "application/octet-stream", "filename": "one.bin",
             "body": {"data": encoded[0]}},
            {"mimeType": "application/octet-stream", "filename": "two.bin",
             "body": {"data": encoded[1]}},
            {"mimeType": "application/octet-stream", "filename": "three.bin",
             "body": {"data": encoded[2]}},
        ],
    })

    assert decoded_calls == [encoded[0], encoded[2]]
    assert [record["name"] for record in attachments] == ["one.bin", "two.bin", "three.bin"]
    assert base64.b64decode(attachments[0]["content"]) == b"abc"
    assert "content" not in attachments[1]
    assert base64.b64decode(attachments[2]["content"]) == b"z"


@pytest.mark.asyncio
async def test_gmail_attachment_response_size_is_checked_before_decoding(monkeypatch):
    monkeypatch.setattr(gmail_provider, "MAX_IMPORT_ATTACHMENT_BYTES", 3)
    decoded_calls = []
    original_decode = gmail_provider._b64url_decode

    def track_decode(value):
        decoded_calls.append(value)
        return original_decode(value)

    message = {
        "id": "m1", "labelIds": ["INBOX"],
        "payload": {"mimeType": "multipart/mixed", "parts": [
            {"mimeType": "application/pdf", "filename": "large.pdf",
             "body": {"attachmentId": "file-1", "size": 3}},
        ]},
    }

    async def fake_api(token, method, path, *, params=None, json_body=None, _client=None):
        if path.endswith("/messages/m1"):
            return {"success": True, "data": message}
        if path.endswith("/attachments/file-1"):
            return {"success": True, "data": {
                "data": gmail_provider._b64url_encode(b"four"),
            }}
        raise AssertionError(f"Unexpected Gmail API path: {path}")

    monkeypatch.setattr(gmail_provider, "_api", fake_api)
    monkeypatch.setattr(gmail_provider, "_b64url_decode", track_decode)
    result = await gmail_provider.gmail_get_message("token", "m1", include_attachments=True)

    assert result["success"] is True
    assert result["item"]["Attachments"] == []
    assert decoded_calls == []


def test_gmail_payload_decodes_rfc2047_headers_and_quoted_address_lists():
    item = gmail_provider._normalize_api_message({
        "id": "m1", "threadId": "t1", "internalDate": "1700000000000",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "text/plain", "body": {"data": "SGVsbG8="},
            "headers": [
                {"name": "from", "value": "=?UTF-8?Q?Ada_Lovelace?= <ada@example.com>"},
                {"name": "to", "value": '"Doe, Jane" <jane@example.com>, Other <other@example.com>'},
                {"name": "subject", "value": "=?UTF-8?Q?R=C3=A9ponse?="},
                {"name": "message-id", "value": "<m1@example.com>"},
                {"name": "in-reply-to", "value": "<root@example.com>"},
            ],
        },
    })
    assert item["From"] == {"Address": "ada@example.com", "Name": "Ada Lovelace"}
    assert item["To"] == [
        {"Address": "jane@example.com"}, {"Address": "other@example.com"},
    ]
    assert item["Subject"] == "Réponse"
    assert item["InReplyTo"] == "<root@example.com>"
    assert item["Headers"]["Message-ID"] == "<m1@example.com>"


def test_imap_mirror_cursor_tracks_backlog_and_stops_before_failed_message(monkeypatch):
    class FakeIMAP:
        def __init__(self, fail_at=None):
            self.fail_at = fail_at
            self.fetches = []

        def select(self, _folder, readonly=False):
            return "OK", [b"1"]

        def status(self, _folder, _items):
            return "OK", [b'"INBOX" (UIDNEXT 20 EXISTS 19 UNSEEN 1)']

        def uid(self, command, *args):
            if command == "SEARCH":
                return "OK", [b"5 6 7 8"]
            if command == "FETCH":
                uid = args[0]
                self.fetches.append(uid)
                if uid == self.fail_at:
                    return "NO", []
                return "OK", [(b"(FLAGS (\\Seen))", b"raw-message")]
            raise AssertionError(f"Unexpected IMAP command: {command}")

        def logout(self):
            return "BYE", [b"logout"]

    monkeypatch.setattr(
        gmail_provider, "parse_mime",
        lambda _raw, **_kwargs: {"RawTextBody": "Body", "Attachments": []},
    )
    views = {"INBOX": ("INBOX",)}

    capped_connection = FakeIMAP()
    monkeypatch.setattr(gmail_provider, "_imap_login", lambda *_args: capped_connection)
    capped = gmail_provider._imap_mirror_blocking(
        "me@gmail.com", "app-password", views,
        cursors={"mirror:INBOX": 5}, lookback_days=14, max_per_folder=2,
    )
    assert capped["success"] is True
    assert capped_connection.fetches == ["5", "6"]
    assert capped["cursor_updates"] == {"mirror:INBOX": 7}

    failed_connection = FakeIMAP(fail_at="6")
    monkeypatch.setattr(gmail_provider, "_imap_login", lambda *_args: failed_connection)
    partial = gmail_provider._imap_mirror_blocking(
        "me@gmail.com", "app-password", views,
        cursors={"mirror:INBOX": 5}, lookback_days=14, max_per_folder=10,
    )
    assert partial["success"] is False
    assert failed_connection.fetches == ["5", "6"]
    assert partial["cursor_updates"] == {"mirror:INBOX": 6}
    assert any("fetch failed" in error for error in partial["errors"])


def test_imap_reply_sync_resumes_capped_backlog_and_failed_uid(monkeypatch):
    class FakeIMAP:
        def __init__(self, fail_at=None):
            self.fail_at = fail_at
            self.fetches = []

        def select(self, _folder, readonly=False):
            return "OK", [b"1"]

        def status(self, _folder, _items):
            return "OK", [b'"INBOX" (UIDNEXT 20 EXISTS 19 UNSEEN 1)']

        def uid(self, command, *args):
            if command == "SEARCH":
                return "OK", [b"5 6 7 8"]
            if command == "FETCH":
                uid = args[0]
                self.fetches.append(uid)
                if uid == self.fail_at:
                    return "NO", []
                return "OK", [(b"(RFC822 {4})", b"raw!")]
            raise AssertionError(f"Unexpected IMAP command: {command}")

        def logout(self):
            return "BYE", [b"logout"]

    monkeypatch.setattr(gmail_provider, "MAX_PER_SYNC", 2)
    monkeypatch.setattr(
        gmail_provider, "parse_mime",
        lambda _raw, **_kwargs: {"RawTextBody": "Body", "Attachments": []},
    )

    capped_connection = FakeIMAP()
    monkeypatch.setattr(gmail_provider, "_imap_login", lambda *_args: capped_connection)
    capped = gmail_provider._imap_sync_blocking(
        "me@gmail.com", "app-password", ["INBOX"], {"INBOX": 5},
        import_all=False, lookback_days=7,
    )
    assert capped["success"] is True
    assert capped_connection.fetches == ["5", "6"]
    assert capped["cursor_updates"] == {"INBOX": 7}

    failed_connection = FakeIMAP(fail_at="6")
    monkeypatch.setattr(gmail_provider, "_imap_login", lambda *_args: failed_connection)
    partial = gmail_provider._imap_sync_blocking(
        "me@gmail.com", "app-password", ["INBOX"], {"INBOX": 5},
        import_all=False, lookback_days=7,
    )
    assert partial["success"] is False
    assert failed_connection.fetches == ["5", "6"]
    assert partial["cursor_updates"] == {"INBOX": 6}


@pytest.mark.asyncio
async def test_imap_reply_sync_merges_only_safe_folder_cursor_updates(db, monkeypatch):
    old_state = {"folders": {"INBOX": 10, "[Gmail]/Spam": 20}, "since": "before"}
    mailbox = await _mailbox(
        db, address="me@gmail.com", provider="imap", credential="app-password",
        cursor_state=json.dumps(old_state),
    )
    seen = {}

    async def fake_imap_sync(_address, _password, folders, cursors, **_kwargs):
        seen.update(folders=folders, cursors=cursors)
        return {
            "success": False,
            "items": [],
            "cursors": {"INBOX": 999, "[Gmail]/Spam": 25},
            "cursor_updates": {"[Gmail]/Spam": 25},
            "errors": ["INBOX/11: fetch failed (NO)"],
        }

    monkeypatch.setattr(gmail_provider, "imap_sync", fake_imap_sync)
    items, state, errors = await mailbox_service._fetch_imap(db, mailbox)

    assert items == []
    assert seen["cursors"] == old_state["folders"]
    assert state["folders"] == {"INBOX": 10, "[Gmail]/Spam": 25}
    assert state["since"] == "before"
    assert errors == ["INBOX/11: fetch failed (NO)"]


async def _token():
    return "token", None


@pytest.mark.asyncio
async def test_imap_mirror_preserves_failed_cursors_and_reports_partial_errors(db, monkeypatch):
    old_time = "2026-11-01T00:00:00+00:00"
    old_state = {
        "mirror": {"mirror:INBOX": 10, "mirror:SENT": 20},
        "mirrored_at": old_time,
    }
    mailbox = await _mailbox(db, address="me@gmail.com", provider="imap",
                             cursor_state=json.dumps(old_state))
    seen_request = {}

    async def fake_mirror(_address, _password, *, cursors, first_sync):
        seen_request.update(cursors=cursors, first_sync=first_sync)
        return {
            "success": False,
            "items": [],
            # Aggregate state includes a bad cursor; only cursor_updates is safe.
            "cursors": {"mirror:INBOX": 999, "mirror:SENT": 25},
            "cursor_updates": {"mirror:SENT": 25},
            "errors": ["INBOX/11: fetch failed (NO)"],
        }

    monkeypatch.setattr(gmail_provider, "imap_mirror_folders", fake_mirror)
    result = await mailbox_message_service._mirror_imap(db, mailbox)

    assert seen_request == {
        "cursors": {"mirror:INBOX": 10, "mirror:SENT": 20},
        "first_sync": False,
    }
    assert result["success"] is False
    assert result["errors"] == ["INBOX/11: fetch failed (NO)"]
    saved = json.loads(mailbox.cursor_state)
    assert saved["mirror"] == {"mirror:INBOX": 10, "mirror:SENT": 25}
    assert saved["mirrored_at"] == old_time


@pytest.mark.asyncio
async def test_imap_mirror_does_not_trust_aggregate_cursors_without_safe_updates(db, monkeypatch):
    old_state = {"mirror": {"mirror:INBOX": 10}, "mirrored_at": "before"}
    mailbox = await _mailbox(db, address="me@gmail.com", provider="imap",
                             cursor_state=json.dumps(old_state))

    async def fake_mirror(_address, _password, **_kwargs):
        return {
            "success": False,
            "items": [],
            "cursors": {"mirror:INBOX": 999},
            "errors": ["INBOX: search failed (NO)"],
        }

    monkeypatch.setattr(gmail_provider, "imap_mirror_folders", fake_mirror)
    result = await mailbox_message_service._mirror_imap(db, mailbox)

    assert result["success"] is False
    assert json.loads(mailbox.cursor_state) == old_state


async def _no_mirror(db, mailbox, **kwargs):
    return {"success": True, "seen": 0, "stored": 0}


@pytest.mark.asyncio
async def test_gmail_mirror_keeps_history_cursor_on_api_failure(db, monkeypatch):
    mailbox = await _mailbox(db, address="me@gmail.com")
    old_state = {"mirror_history_id": "history-old", "mirrored_at": "2026-11-01T00:00:00+00:00"}
    mailbox.cursor_state = json.dumps(old_state)

    async def fake_access_token(db, mailbox):
        return "access-token", None

    async def fake_history(token, history_id):
        return {"success": False, "status_code": 429, "error": "rate limited"}

    monkeypatch.setattr(mailbox_service, "access_token_for", fake_access_token)
    monkeypatch.setattr(gmail_provider, "gmail_history_changes", fake_history)
    result = await mailbox_message_service._mirror_gmail_api(db, mailbox)
    assert result["success"] is False
    assert mailbox.cursor_state == json.dumps(old_state)
    assert "rate limited" in " ".join(result["errors"])


@pytest.mark.asyncio
async def test_gmail_api_retries_safe_gets_but_never_retries_side_effects(monkeypatch):
    class Response:
        def __init__(self, status_code):
            self.status_code = status_code
            self.headers = {}
            self.text = "temporary error"

        def json(self):
            return {"error": {"message": "temporary error"}} if self.status_code >= 400 else {"ok": True}

    calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, **kwargs):
            calls.append(method)
            return Response(503 if len(calls) == 1 else 200)

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(gmail_provider.httpx, "AsyncClient", Client)
    monkeypatch.setattr(gmail_provider.asyncio, "sleep", no_sleep)

    get_result = await gmail_provider._api("token", "GET", "/users/me/profile")
    assert get_result["success"] is True
    assert calls == ["GET", "GET"]

    calls.clear()
    post_result = await gmail_provider._api("token", "POST", "/users/me/messages/send", json_body={})
    assert post_result["success"] is False
    assert calls == ["POST"]


def test_gmail_message_fetch_has_a_small_concurrency_budget():
    """Every message is fetched individually, with no unbounded task burst."""
    assert gmail_provider.BATCH_CONCURRENCY == 3
    assert gmail_provider.BATCH_GET_LIMIT >= gmail_provider.BATCH_CONCURRENCY
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
