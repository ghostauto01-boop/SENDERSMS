"""Regression tests: email body rendering, footer removal, sender pools.

Outbound message bodies must remain unchanged; opt-out headers and inbound
opt-out processing are handled separately. These tests pin the body in place
across the text part, the HTML part and the Brevo payload, and pin the new
OPTIONAL multi-sender pool (off = legacy single-sender behaviour).
"""

import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base
from app.models.ads import AdsAssignment, AdsCampaign, AdsCreative, AdsSet
from app.models.contact import Contact
from app.models.conversation import Message
from app.models.email import EmailAccount
from app.providers import brevo
from app.security.encryption import encrypt_value
from app.services import ads_service, email_service


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
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example.test")
    yield


async def _account(db, name, from_email, *, active=True):
    account = EmailAccount(
        name=name, from_name=name, from_email=from_email,
        reply_to=from_email, api_key_encrypted=encrypt_value(f"key-{name}"),
        is_active=active, is_default=False,
    )
    db.add(account)
    await db.flush()
    return account


async def _contact(db, email="ada@example.com", name="Ada"):
    suffix = abs(hash(email)) % 100000
    contact = Contact(first_name=name, email=email, phone_number=f"+1555{suffix:07d}"[:20])
    db.add(contact)
    await db.flush()
    return contact


# ==========================================================================
# Part A/B — outbound bodies never receive an unsubscribe footer
# ==========================================================================


def test_apply_unsubscribe_leaves_plain_text_body_unchanged():
    body = "Hi ada@example.com, this is my message. Reply if interested. Best, Moh"
    text, html = email_service.apply_unsubscribe(body, None, "ada@example.com")
    assert text == body
    assert html is None


def test_apply_unsubscribe_leaves_html_body_unchanged():
    body_text = "Hello there"
    body_html = "<html><body><p>Hello there</p></body></html>"
    text, html = email_service.apply_unsubscribe(body_text, body_html, "ada@example.com")
    assert text == body_text
    assert html == body_html


@pytest.mark.asyncio
async def test_render_email_renders_tokens_and_unsubscribes_the_recipient(db):
    contact = await _contact(db)
    account = await _account(db, "Main", "sender@acme-leads.io")
    rendered = await email_service.render_email(
        db, contact,
        subject="Hi {{first_name}}",
        text_body="Hi {{first_name}}, this is my message. Reply if interested. Best, Moh",
        html_body=None, account=account, append_unsubscribe=True,
    )
    assert rendered["subject"] == "Hi Ada"
    assert rendered["text"].startswith("Hi Ada, this is my message.")
    assert "{{first_name}}" not in rendered["text"]
    # No unsubscribe URL or button is appended to the visible message body.
    assert "unsubscribe" not in rendered["text"].lower()
    assert "unsubscribe" not in rendered["html"].lower()
    assert "Hi Ada, this is my message." in rendered["html"]


@pytest.mark.asyncio
async def test_brevo_payload_carries_body_and_refuses_blank(monkeypatch):
    sent = {}

    class FakeResponse:
        status_code = 201
        text = '{"messageId": "<fake@brevo>"}'

        def json(self):
            return {"messageId": "<fake@brevo>"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, *, headers=None, json=None, **kwargs):
            sent.update(json or {})
            return FakeResponse()

    monkeypatch.setattr(brevo.httpx, "AsyncClient", FakeClient)
    result = await brevo.send_email(
        "key", to_email="ada@example.com", to_name="Ada", subject="Hi Ada",
        html="<p>Hi Ada, this is my message.</p>",
        text="Hi Ada, this is my message.",
        from_email="sender@acme-leads.io", from_name="Main",
    )
    assert result["success"] is True
    assert sent["textContent"] == "Hi Ada, this is my message."
    assert sent["htmlContent"] == "<p>Hi Ada, this is my message.</p>"
    assert sent["subject"] == "Hi Ada"

    blank = await brevo.send_email(
        "key", to_email="ada@example.com", to_name=None, subject="Hi",
        html=None, text="  ", from_email="sender@acme-leads.io", from_name="Main",
    )
    assert blank["success"] is False
    assert "empty" in blank["error"].lower()


# ==========================================================================
# Multi-sender pool — optional, deterministic, creative-style pinning
# ==========================================================================


def test_pick_sender_id_strategies_are_deterministic():
    pool = [11, 22, 33]
    assert [email_service.pick_sender_id(pool, key=i) for i in range(6)] == [
        11, 22, 33, 11, 22, 33,
    ]
    weighted = [
        email_service.pick_sender_id(pool[:2], key=i, distribution="weighted",
                                     weights={11: 75, 22: 25})
        for i in range(8)
    ]
    assert weighted.count(11) == 6 and weighted.count(22) == 2
    assert weighted[2] == 22  # interleaved, not two contiguous blocks
    random_a = [email_service.pick_sender_id(pool, key=i, distribution="random") for i in range(9)]
    random_b = [email_service.pick_sender_id(pool, key=i, distribution="random") for i in range(9)]
    assert random_a == random_b  # stable per contact: retries never re-roll
    assert email_service.normalize_sender_ids('[11, "x", 22, 22]', primary=11) == [11, 22]
    assert email_service.sender_assignment_counts(pool, 100)[11] in (33, 34)


@pytest.mark.asyncio
async def test_resolve_campaign_sender_rotates_and_skips_dead_senders(db):
    one = await _account(db, "One", "one@acme-leads.io")
    two = await _account(db, "Two", "two@acme-leads.io", active=False)
    three = await _account(db, "Three", "three@acme-leads.io")
    campaign = AdsCampaign(
        name="Pool", channel="email", status="active",
        email_account_ids=[one.id, two.id, three.id],
    )
    db.add(campaign)
    await db.flush()

    picked = []
    for key in range(4):
        sender = await email_service.resolve_campaign_sender(db, campaign, key=key)
        picked.append(email_service.sender_email(sender))
    # Rotation order is 1,2,3,1 — but #2 is disabled, so its slot falls
    # through to the next usable pool member instead of stalling.
    assert picked == [
        "one@acme-leads.io", "three@acme-leads.io",
        "three@acme-leads.io", "one@acme-leads.io",
    ]


@pytest.mark.asyncio
async def test_assign_contacts_pins_senders_like_creatives(db):
    one = await _account(db, "One", "one@acme-leads.io")
    two = await _account(db, "Two", "two@acme-leads.io")
    campaign = AdsCampaign(
        name="Pool campaign", channel="email", status="active",
        email_account_ids=[one.id, two.id], subject="Hi",
    )
    db.add(campaign)
    await db.flush()
    ads_set = AdsSet(campaign_id=campaign.id, name="Set", status="active")
    db.add(ads_set)
    await db.flush()
    creative = AdsCreative(
        set_id=ads_set.id, campaign_id=campaign.id, name="Creative",
        status="active", body="Hi {{first_name}}, this is my message.",
    )
    db.add(creative)
    contacts = [await _contact(db, email=f"lead{i}@example.com", name=f"L{i}") for i in range(4)]

    result = await ads_service.assign_contacts(db, campaign, ads_set, contacts)
    assert result["assigned"] == 4
    rows = (
        await db.execute(
            select(AdsAssignment).where(AdsAssignment.campaign_id == campaign.id)
            .order_by(AdsAssignment.contact_id)
        )
    ).scalars().all()
    assert [r.email_account_id for r in rows] == [one.id, two.id, one.id, two.id]


@pytest.mark.asyncio
async def test_campaign_api_accepts_optional_sender_pool(api_client, api_db):
    one = EmailAccount(
        name="One", from_name="One", from_email="one@acme-leads.io",
        api_key_encrypted=encrypt_value("key-one"), is_active=True, is_default=True,
    )
    two = EmailAccount(
        name="Two", from_name="Two", from_email="two@acme-leads.io",
        api_key_encrypted=encrypt_value("key-two"), is_active=True,
    )
    api_db.add_all([one, two])
    await api_db.commit()

    created = await api_client.post("/api/v1/campaigns/", json={
        "name": "Pooled", "channel": "email", "subject": "Hi",
        "message_body": "Hi {{first_name}}, this is my message.",
        "email_account_ids": [one.id, two.id],
        "sender_distribution": "weighted",
        "email_sender_weights": {str(one.id): 75, str(two.id): 25},
    })
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["email_account_ids"] == [one.id, two.id]
    assert body["sender_distribution"] == "weighted"
    assert body["email_sender_weights"] == {str(one.id): 75, str(two.id): 25}

    # And without a pool the campaign is exactly the legacy single-sender one.
    plain = await api_client.post("/api/v1/campaigns/", json={
        "name": "Single", "channel": "email", "subject": "Hi",
        "message_body": "Hello",
    })
    assert plain.status_code == 201, plain.text
    assert plain.json()["email_account_ids"] is None
    assert plain.json()["sender_distribution"] == "round_robin"

    bad = await api_client.post("/api/v1/campaigns/", json={
        "name": "Bad pool", "channel": "email", "subject": "Hi",
        "message_body": "Hello", "email_account_ids": [999999],
    })
    assert bad.status_code == 404


@pytest.mark.asyncio
async def test_ads_dispatch_sends_full_personalized_bodies_from_rotating_senders(db):
    """Five personalization scenarios through the real dispatch path."""
    one = await _account(db, "One", "one@acme-leads.io")
    two = await _account(db, "Two", "two@acme-leads.io")
    campaign = AdsCampaign(
        name="Dispatch pool", channel="email", status="active", subject="Hello",
        email_account_ids=[one.id, two.id],
    )
    db.add(campaign)
    await db.flush()
    ads_set = AdsSet(campaign_id=campaign.id, name="Set", status="active")
    db.add(ads_set)
    await db.flush()
    creative = AdsCreative(
        set_id=ads_set.id, campaign_id=campaign.id, name="Creative", status="active",
        subject="Hi {{first_name}}",
        body="Hi {{first_name}}, this is my message. Reply if interested. Best, Moh",
    )
    db.add(creative)
    names = ["Ada", "Bola", "Chidi", "Dara", "Efe"]
    contacts = []
    for i, name in enumerate(names):
        contact = await _contact(db, email=f"lead{i}@acme-leads.io", name=name)
        contact.email_verified = True
        contacts.append(contact)
    await ads_service.assign_contacts(db, campaign, ads_set, contacts)
    await db.commit()

    result = await ads_service.dispatch_campaign(db, campaign, limit=10)
    assert result["sent"] == 5, result

    messages = (
        await db.execute(
            select(Message).where(Message.ads_campaign_id == campaign.id)
            .order_by(Message.contact_id)
        )
    ).scalars().all()
    assert len(messages) == 5
    for message, contact, name in zip(messages, contacts, names):
        # The creative body is sent as written, without an appended footer.
        assert message.body == f"Hi {name}, this is my message. Reply if interested. Best, Moh"
        assert message.subject == f"Hi {name}"
        assert "unsubscribe" not in message.body.lower()
        if message.html_body:
            assert f"Hi {name}, this is my message." in message.html_body
            # P1-8 "plain personal email": the ONLY footer that may appear is
            # the small gray "unsubscribe" link line (11px, #9ca3af) — no
            # button, no card shell, and never a raw unsubscribe URL in the
            # text part.
            assert "<button" not in message.html_body.lower()
            html_low = message.html_body.lower()
            if "unsubscribe" in html_low:
                # one link: the URL's /unsubscribe path + the link text
                assert html_low.count(">unsubscribe</a>") == 1
                assert "11px" in message.html_body and "#9ca3af" in message.html_body
    # The pool actually rotated: no single From took all five.
    assert [m.from_address for m in messages] == [
        "one@acme-leads.io", "two@acme-leads.io", "one@acme-leads.io",
        "two@acme-leads.io", "one@acme-leads.io",
    ]
