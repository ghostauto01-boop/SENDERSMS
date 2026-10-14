"""Replies leave from connected Gmail. Brevo mail leaves from @brevosend.com.

A composed send stays on the sender that was selected. A campaign whose
selected sender is rejected tries the rest of that campaign's pool, not an
unrelated account.
"""

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.conversation import Message
from app.models.email import EmailAccount
from app.providers import gmail as gmail_provider
from app.providers.brevo import pick_brevo_relay_address, pick_verified_from, sender_rejected
from app.security.encryption import encrypt_value
from app.services import email_service, mailbox_service


@pytest.fixture(autouse=True)
def public_base(monkeypatch):
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


def test_relay_picker_prefers_the_same_local_part_and_never_invents_one():
    senders = [
        {"email": "other@100.brevosend.com", "active": True},
        {"email": "hello@100.brevosend.com", "active": True},
        {"email": "hello@gmail.com", "active": True},
    ]
    assert pick_brevo_relay_address(senders, hint="hello@gmail.com") == "hello@100.brevosend.com"
    assert pick_verified_from(senders, "hello@gmail.com") == "hello@100.brevosend.com"
    assert pick_verified_from([], "hello@acme.io") == "hello@acme.io"
    assert pick_verified_from(
        [{"email": "hello@acme.io", "active": True}], "hello@acme.io"
    ) == "hello@acme.io"
    assert sender_rejected({"status_code": 400, "error": "Sender email is not valid"}) is True
    assert sender_rejected({"status_code": 400, "error": "invalid recipient"}) is False


async def _account(db, *, name, from_email, reply_to=None, key="key", default=False):
    account = EmailAccount(
        name=name,
        from_name=name,
        from_email=from_email,
        reply_to=reply_to or from_email,
        api_key_encrypted=encrypt_value(key),
        is_default=default,
        is_active=True,
    )
    db.add(account)
    await db.flush()
    return account


async def _contact(db):
    contact = Contact(
        first_name="Ada", email="prospect@acme.com", phone_number="email:prospect",
        country="Nigeria", lead_status="new", source="test",
    )
    db.add(contact)
    await db.flush()
    return contact


async def _message(db, contact, account, **overrides):
    conversation = await email_service.get_or_create_conversation(
        db, contact, channel="email", account_id=account.id,
    )
    fields = dict(
        conversation_id=conversation.id,
        contact_id=contact.id,
        channel="email",
        direction="outgoing",
        to_address=contact.email,
        from_address=account.from_email,
        subject="Hello",
        body="Hi there",
        status="queued",
        bulk_send=False,
        email_account_id=account.id,
        idempotency_key=f"test-{uuid.uuid4().hex[:16]}",
    )
    fields.update(overrides)
    message = Message(**fields)
    db.add(message)
    await db.flush()
    return message


class _Brevo:
    def __init__(self, posts, *, fail_keys=None, senders=None):
        self.posts = posts
        self.fail_keys = fail_keys or {}
        self.senders = senders or []

    def client(self):
        outer = self

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, *, headers=None, **kwargs):
                class Response:
                    status_code = 200
                    text = json.dumps({"senders": outer.senders})

                    @staticmethod
                    def json():
                        return {"senders": outer.senders}

                return Response()

            async def post(self, url, *, headers=None, json=None, **kwargs):
                key = (headers or {}).get("api-key")
                payload = json or {}
                outer.posts.append({"key": key, "payload": payload})
                status, body = outer.fail_keys.get(key, (201, {"messageId": "ok"}))

                class Response:
                    status_code = status
                    text = __import__("json").dumps(body)

                    @staticmethod
                    def json():
                        return body

                return Response()

        return FakeClient


@pytest.mark.asyncio
async def test_brevo_send_uses_listed_relay_and_personal_reply_to(db, monkeypatch):
    posts = []
    fake = _Brevo(posts, senders=[
        {"email": "me@gmail.com", "active": True, "name": "Me"},
        {"email": "me@8541322.brevosend.com", "active": True, "name": "Me"},
    ])
    monkeypatch.setattr("app.providers.brevo.httpx.AsyncClient", fake.client())
    contact = await _contact(db)
    account = await _account(
        db, name="Main", from_email="me@gmail.com", reply_to="me@gmail.com", default=True,
    )
    message = await _message(db, contact, account, bulk_send=True)
    result = await email_service.deliver(db, message)
    assert result["success"] is True
    sender = posts[0]["payload"]["sender"]["email"]
    assert sender == "me@8541322.brevosend.com"
    assert posts[0]["payload"]["replyTo"]["email"] == "me@gmail.com"
    assert message.from_address == "me@8541322.brevosend.com"
    assert account.from_email == "me@gmail.com"
    assert account.brevo_from_email == "me@8541322.brevosend.com"


@pytest.mark.asyncio
async def test_sender_400_refreshes_and_retries_the_relay_once(db, monkeypatch):
    sent = []
    listed = {"n": 0}

    async def flaky_list(api_key):
        listed["n"] += 1
        if listed["n"] == 1:
            return {"success": True, "senders": [{"email": "me@gmail.com", "active": True}]}
        return {
            "success": True,
            "senders": [
                {"email": "me@gmail.com", "active": True},
                {"email": "me@100.brevosend.com", "active": True},
            ],
        }

    async def fake_send(api_key, **kwargs):
        sent.append(kwargs["from_email"])
        if str(kwargs["from_email"]).endswith("brevosend.com"):
            return {"success": True, "provider_message_id": "retried"}
        return {"success": False, "status_code": 400, "error": "Sender email is not valid"}

    monkeypatch.setattr("app.providers.brevo.list_senders", flaky_list)
    monkeypatch.setattr("app.providers.brevo.send_email", fake_send)
    contact = await _contact(db)
    account = await _account(db, name="Main", from_email="me@gmail.com", default=True)
    message = await _message(db, contact, account)
    result = await email_service.deliver(db, message)
    assert result["success"] is True, result
    assert sent == ["me@gmail.com", "me@100.brevosend.com"]
    assert message.from_address == "me@100.brevosend.com"
    assert account.from_email == "me@gmail.com"


@pytest.mark.asyncio
async def test_composed_brevo_send_is_not_rerouted_to_a_matching_mailbox(db, monkeypatch):
    contact = await _contact(db)
    account = await _account(
        db, name="Main", from_email="hello@acme.io", reply_to="me@gmail.com", default=True,
    )
    await mailbox_service.create_mailbox(
        db, name="Gmail", email_address="me@gmail.com", provider="imap",
        credential="abcd efgh ijkl mnop",
    )
    called = []

    async def fake_smtp(*args, **kwargs):
        called.append(args)
        return {"success": True, "provider_message_id": "smtp"}

    async def fake_send(api_key, **kwargs):
        return {"success": True, "provider_message_id": "brevo-1"}

    monkeypatch.setattr(gmail_provider, "smtp_send", fake_smtp)
    monkeypatch.setattr("app.providers.brevo.send_email", fake_send)
    message = await _message(db, contact, account)
    result = await email_service.deliver(db, message)
    assert result["success"] is True
    assert called == []
    assert message.provider == "brevo"


@pytest.mark.asyncio
async def test_reply_without_address_match_still_leaves_from_gmail_api(db, monkeypatch):
    contact = await _contact(db)
    account = await _account(
        db, name="Main", from_email="hello@acme.io", reply_to="hello@acme.io", default=True,
    )
    mailbox = await mailbox_service.create_mailbox(
        db, name="API", email_address="me@gmail.com", provider="gmail_api",
        credential="refresh-token", client_id="client", client_secret="secret",
    )
    sent = []

    async def fake_refresh(*args, **kwargs):
        return {"success": True, "access_token": "ya29.fake"}

    async def fake_send(token, raw):
        sent.append({"token": token, "raw": raw})
        return {"success": True, "provider_message_id": "gmail-1"}

    monkeypatch.setattr(gmail_provider, "google_refresh_access_token", fake_refresh)
    monkeypatch.setattr(gmail_provider, "gmail_send", fake_send)
    message = await _message(db, contact, account, send_from_mailbox=True, subject="Re: Hello")
    result = await email_service.deliver(db, message)
    assert result["success"] is True, result
    assert result["provider"] == "gmail_api"
    assert message.from_address == "me@gmail.com"
    assert sent and sent[0]["token"] == "ya29.fake"
    assert mailbox.total_sent == 1


@pytest.mark.asyncio
async def test_reply_with_no_mailbox_still_uses_the_brevo_envelope(db, monkeypatch):
    posts = []
    fake = _Brevo(posts, senders=[{"email": "hello@100.brevosend.com", "active": True}])
    monkeypatch.setattr("app.providers.brevo.httpx.AsyncClient", fake.client())
    contact = await _contact(db)
    account = await _account(
        db, name="Main", from_email="hello@acme.io", reply_to="me@gmail.com", default=True,
    )
    message = await _message(db, contact, account, send_from_mailbox=True)
    result = await email_service.deliver(db, message)
    assert result["success"] is True
    assert posts[0]["payload"]["sender"]["email"] == "hello@100.brevosend.com"
    assert posts[0]["payload"]["replyTo"]["email"] == "me@gmail.com"


@pytest.mark.asyncio
async def test_campaign_pool_tries_the_next_member_after_a_rejection(db, monkeypatch):
    posts = []
    fake = _Brevo(posts, fail_keys={
        "key-bad": (400, {"message": "Sender is not valid", "code": "invalid_parameter"}),
    })
    monkeypatch.setattr("app.providers.brevo.httpx.AsyncClient", fake.client())
    contact = await _contact(db)
    bad = await _account(db, name="Bad", from_email="bad@acme.io", key="key-bad", default=True)
    good = await _account(db, name="Good", from_email="good@acme.io", key="key-good")
    campaign = Campaign(
        name="Pool", channel="email", status="running",
        email_account_id=bad.id,
        email_account_ids=[bad.id, good.id],
    )
    db.add(campaign)
    await db.flush()
    message = await _message(
        db, contact, bad, campaign_id=campaign.id, bulk_send=True, subject="Offer",
    )
    result = await email_service.deliver(db, message)
    assert result["success"] is True, result
    assert [item["key"] for item in posts] == ["key-bad", "key-good"]
    assert message.email_account_id == good.id


@pytest.mark.asyncio
async def test_one_off_explicit_sender_does_not_rotate(db, monkeypatch):
    posts = []
    fake = _Brevo(posts, fail_keys={
        "key-bad": (401, {"message": "Key not found", "code": "unauthorized"}),
    })
    monkeypatch.setattr("app.providers.brevo.httpx.AsyncClient", fake.client())
    contact = await _contact(db)
    bad = await _account(db, name="Bad", from_email="bad@acme.io", key="key-bad", default=True)
    await _account(db, name="Good", from_email="good@acme.io", key="key-good")
    message = await _message(db, contact, bad, subject="One off")
    result = await email_service.deliver(db, message)
    assert result["success"] is False
    assert [item["key"] for item in posts] == ["key-bad"]


@pytest.mark.asyncio
async def test_campaign_followup_stays_on_brevo_even_if_reply_to_matches_gmail(db, monkeypatch):
    contact = await _contact(db)
    account = await _account(
        db, name="Main", from_email="hello@acme.io", reply_to="me@gmail.com", default=True,
    )
    campaign = Campaign(
        name="Follow", channel="email", status="running", email_account_id=account.id,
    )
    db.add(campaign)
    await db.flush()
    await mailbox_service.create_mailbox(
        db, name="Gmail", email_address="me@gmail.com", provider="gmail_api",
        credential="refresh-token", client_id="client", client_secret="secret",
    )
    called = []

    async def fake_send(*args, **kwargs):
        called.append(args)
        return {"success": True}

    async def fake_brevo(api_key, **kwargs):
        return {"success": True, "provider_message_id": "brevo-1"}

    monkeypatch.setattr(gmail_provider, "gmail_send", fake_send)
    monkeypatch.setattr("app.providers.brevo.send_email", fake_brevo)
    message = await _message(
        db, contact, account, campaign_id=campaign.id, subject="Re: checking in",
    )
    result = await email_service.deliver(db, message)
    assert result["success"] is True
    assert called == []
    assert message.provider == "brevo"
