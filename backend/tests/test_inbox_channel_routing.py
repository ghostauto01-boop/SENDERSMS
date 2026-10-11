"""Which inbox a click opens, the Conversations page, and Gmail Google sign-in.

Three complaints from the same screen, each with its own code path:

1. **"I clicked an email campaign's reply and it took me to the SMS inbox."**
   Every deep link into an inbox said ``/inbox``, whatever the campaign had
   actually sent on. An email conversation lives on the email channel, so the
   SMS inbox had no thread for that person: an empty chat list and no way to
   answer. The links are now derived from the campaign's channel — in the
   overview API, in the campaign drill-down, and in the UI that renders both.

2. **"Add a page with only the people who replied."** ``replied_only`` on the
   email inbox list is that page: a thread only counts once the person on the
   other end has written back, so one-way campaign mail stays out of it.

3. **"I was using an app password; I want Google sign-in, several Gmail
   inboxes, and the console steps."** The OAuth client can now be saved from
   Settings → Gmail replies (encrypted, database-backed, environment still
   honoured), the consent URL forces Google's account chooser so a second inbox
   is the same click as the first, and the operator lands back on the page they
   started from.

Everything runs offline: Google's endpoints are monkeypatched at the provider
boundary, so the assertions are about this app's behaviour.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1 import mailbox as mailbox_api
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.campaign import Campaign, CampaignContact
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.models.email_inbox import EmailMailbox
from app.models.user import User
from app.providers import gmail as gmail_provider
from app.security.auth import get_current_user
from app.security.encryption import encrypt_value
from app.services import mailbox_service, system_settings


# ==========================================================================
# Fixtures
# ==========================================================================


@pytest_asyncio.fixture
async def client():
    """The real app over HTTP, an in-memory database, and the session behind it."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    async def _get_db():
        yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="tester", password_hash="x", role="admin", is_active=True
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, session
    app.dependency_overrides.clear()
    await session.close()
    await engine.dispose()


@pytest.fixture(autouse=True)
def public_url_and_no_oauth(monkeypatch):
    """A deployment with a public address but no Google client configured."""
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example.test")
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "")
    yield


async def _contact(session, *, email=None, phone=None) -> Contact:
    contact = Contact(
        first_name="Ada",
        last_name="Obi",
        email=email,
        phone_number=phone,
        country="Nigeria" if phone else None,
    )
    session.add(contact)
    await session.flush()
    return contact


async def _thread(session, contact, *, channel, campaign=None, replied=False,
                  subject=None, preview=None) -> Conversation:
    """One conversation, optionally with an inbound reply on it."""
    conv = Conversation(
        contact_id=contact.id,
        channel=channel,
        status="active",
        campaign_id=campaign.id if campaign else None,
        last_campaign_id=campaign.id if campaign else None,
        subject=subject,
        last_message_preview=preview or ("Yes, tell me more" if replied else "Hi Ada"),
        message_count=2 if replied else 1,
    )
    session.add(conv)
    await session.flush()
    session.add(Message(
        conversation_id=conv.id, contact_id=contact.id, channel=channel,
        direction="outgoing", body="Hi Ada", status="delivered",
        campaign_id=campaign.id if campaign else None,
        idempotency_key=f"out-{conv.id}",
    ))
    if replied:
        session.add(Message(
            conversation_id=conv.id, contact_id=contact.id, channel=channel,
            direction="incoming", body="Yes, tell me more", status="delivered",
            idempotency_key=f"in-{conv.id}",
        ))
    await session.flush()
    return conv


async def _campaign(session, *, name, channel) -> Campaign:
    camp = Campaign(name=name, status="running", total_contacts=2, channel=channel)
    session.add(camp)
    await session.flush()
    return camp


# ==========================================================================
# 1. An email campaign opens the email inbox
# ==========================================================================


@pytest.mark.asyncio
async def test_overview_deep_links_follow_the_campaign_channel(client):
    ac, session = client
    sms_camp = await _campaign(session, name="SMS promo", channel="sms")
    email_camp = await _campaign(session, name="Email promo", channel="email")
    await session.commit()

    rows = {r["name"]: r for r in (await ac.get("/api/v1/overview/campaigns")).json()["items"]}

    assert rows["Email promo"]["inbox_url"] == f"/email-inbox?campaign_id={email_camp.id}"
    # The email inbox spells "only the ones who replied" as a view, the SMS one
    # as a flag — each link has to use the spelling its destination understands.
    assert rows["Email promo"]["replies_url"] == (
        f"/email-inbox?campaign_id={email_camp.id}&view=replied"
    )
    assert rows["SMS promo"]["inbox_url"] == f"/inbox?campaign_id={sms_camp.id}"
    assert rows["SMS promo"]["replies_url"] == f"/inbox?campaign_id={sms_camp.id}&replied=1"


@pytest.mark.asyncio
async def test_a_campaigns_replies_list_only_contains_its_own_channel(client):
    """The drill-down behind the "N replies" button, with both threads present."""
    ac, session = client
    contact = await _contact(session, email="ada@example.com", phone="+2348031110001")
    email_camp = await _campaign(session, name="Email promo", channel="email")
    session.add(CampaignContact(campaign_id=email_camp.id, contact_id=contact.id, status="sent"))
    email_conv = await _thread(session, contact, channel="email", campaign=email_camp,
                               replied=True, subject="Dinner on Friday?")
    # The same person also has a text thread, attributed to the same campaign id
    # by an earlier SMS send: it must not show up in an email campaign's replies.
    await _thread(session, contact, channel="sms", campaign=email_camp, replied=True)
    await session.commit()

    body = (await ac.get(f"/api/v1/campaigns/{email_camp.id}/conversations")).json()

    assert [i["conversation_id"] for i in body["items"]] == [email_conv.id]
    assert body["channel"] == "email"
    row = body["items"][0]
    # The row carries what an email thread is addressed by, and where it opens.
    assert row["channel"] == "email"
    assert row["contact_email"] == "ada@example.com"


@pytest.mark.asyncio
async def test_performance_counts_only_the_channel_the_campaign_sent_on(client):
    ac, session = client
    contact = await _contact(session, email="ada@example.com", phone="+2348031110002")
    email_camp = await _campaign(session, name="Email promo", channel="email")
    await _thread(session, contact, channel="email", campaign=email_camp, replied=True)
    await _thread(session, contact, channel="sms", campaign=email_camp, replied=True)
    await session.commit()

    perf = (await ac.get(f"/api/v1/campaigns/{email_camp.id}/performance")).json()

    assert perf["channel"] == "email"
    assert perf["leads"] == 1        # not 2: the SMS thread is a different campaign's business
    assert perf["replied"] == 1


# ==========================================================================
# 2. The Conversations page: only people who replied
# ==========================================================================


@pytest.mark.asyncio
async def test_replied_only_lists_the_conversations_and_counts_both_pages(client):
    ac, session = client
    ada = await _contact(session, email="ada@example.com")
    bola = await _contact(session, email="bola@example.com")
    chidi = await _contact(session, email="chidi@example.com")
    await _thread(session, ada, channel="email", replied=True)
    await _thread(session, bola, channel="email", replied=False)
    await _thread(session, chidi, channel="email", replied=False)
    # An SMS thread that did reply must not leak into the email inbox.
    await _thread(session, ada, channel="sms", replied=True)
    await session.commit()

    everything = (await ac.get("/api/v1/email/inbox/conversations")).json()
    replied = (await ac.get("/api/v1/email/inbox/conversations",
                            params={"replied_only": True})).json()

    assert everything["total"] == 3 and everything["thread_total"] == 3
    assert everything["replied_total"] == 1        # the Conversations tab label
    assert replied["total"] == 1
    assert [c["contact_email"] for c in replied["items"]] == ["ada@example.com"]


@pytest.mark.asyncio
async def test_the_inbox_deep_link_a_campaign_sends_scopes_the_list(client):
    """`/email-inbox?campaign_id=…&contact_id=…` — where a reply row clicks to."""
    ac, session = client
    camp = await _campaign(session, name="Email promo", channel="email")
    ada = await _contact(session, email="ada@example.com")
    bola = await _contact(session, email="bola@example.com")
    other = await _campaign(session, name="Another", channel="email")
    await _thread(session, ada, channel="email", campaign=camp, replied=True)
    await _thread(session, bola, channel="email", campaign=other, replied=True)
    await session.commit()

    by_campaign = (await ac.get("/api/v1/email/inbox/conversations",
                                params={"campaign_id": camp.id})).json()
    assert [c["contact_email"] for c in by_campaign["items"]] == ["ada@example.com"]

    by_person = (await ac.get("/api/v1/email/inbox/conversations",
                              params={"contact_id": bola.id})).json()
    assert [c["contact_email"] for c in by_person["items"]] == ["bola@example.com"]

    # Both together: the campaign the link names, narrowed to one person.
    both = (await ac.get("/api/v1/email/inbox/conversations",
                         params={"campaign_id": camp.id, "contact_id": bola.id})).json()
    assert both["items"] == []


# ==========================================================================
# 3. Google sign-in for the replies inbox
# ==========================================================================


@pytest.mark.asyncio
async def test_google_oauth_starts_unconfigured_and_reports_the_callback_url(client):
    ac, session = client
    body = (await ac.get("/api/v1/settings/google-oauth")).json()

    assert body["configured"] is False and body["source"] == "none"
    # The value the operator has to paste into the console, spelled out in full.
    assert body["redirect_uri"] == "https://app.example.test/api/v1/mailbox/google/callback"
    assert body["javascript_origin"] == "https://app.example.test"
    assert body["scope"] == gmail_provider.GMAIL_SCOPE
    assert body["mailbox_count"] == 0


@pytest.mark.asyncio
async def test_the_oauth_client_can_be_saved_from_the_app_without_touching_the_env(client):
    ac, session = client
    saved = (await ac.put("/api/v1/settings/google-oauth", json={
        "client_id": "1234567890-abcdefghijklmnop.apps.googleusercontent.com",
        "client_secret": "GOCSPX-secret-value",
    })).json()

    assert saved["success"] is True and saved["configured"] is True
    assert saved["source"] == "db"
    # The secret is write-only: never echoed back, in full or in part.
    assert "client_secret" not in saved
    assert "GOCSPX" not in str(saved)

    read = (await ac.get("/api/v1/settings/google-oauth")).json()
    assert read["configured"] is True and read["has_secret"] is True
    assert read["client_id"].endswith(".apps.googleusercontent.com")
    assert "GOCSPX" not in read["client_id_masked"]

    # And it is what the mailbox list reports to the UI.
    listing = (await ac.get("/api/v1/mailbox")).json()
    assert listing["google_oauth_ready"] is True
    assert listing["redirect_uri"].endswith("/api/v1/mailbox/google/callback")

    # Stored encrypted, not in plain text.
    raw = await system_settings.get_setting(session, system_settings.GOOGLE_OAUTH_CLIENT_SECRET)
    assert raw and "GOCSPX" not in raw


@pytest.mark.asyncio
async def test_saving_a_client_id_without_a_secret_is_refused(client):
    ac, session = client
    response = await ac.put("/api/v1/settings/google-oauth", json={
        "client_id": "1234567890-abcdefghijklmnop.apps.googleusercontent.com"})

    assert response.status_code == 422
    assert "client secret" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_environment_credentials_still_work_and_are_reported_as_such(client, monkeypatch):
    ac, session = client
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "env-client.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "env-secret")

    body = (await ac.get("/api/v1/settings/google-oauth")).json()

    assert body["configured"] is True and body["source"] == "env"


@pytest.mark.asyncio
async def test_connect_with_google_switches_on_once_the_client_is_saved(client, monkeypatch):
    ac, session = client
    # Nothing configured: the button's endpoint explains how to fix it.
    refused = await ac.get("/api/v1/mailbox/google/start")
    assert refused.status_code == 400
    assert "Settings → Gmail replies" in refused.json()["detail"]

    await ac.put("/api/v1/settings/google-oauth", json={
        "client_id": "1234567890-abcdefghijklmnop.apps.googleusercontent.com",
        "client_secret": "GOCSPX-secret-value",
    })

    started = await ac.get("/api/v1/mailbox/google/start")
    assert started.status_code == 200
    url = started.json()["url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "access_type=offline" in url                    # without it: no refresh token
    # select_account is what makes a SECOND Gmail inbox possible: Google always
    # shows the chooser instead of reusing the signed-in account.
    assert "prompt=consent" in url and "select_account" in url
    assert "redirect_uri=https%3A%2F%2Fapp.example.test%2Fapi%2Fv1%2Fmailbox%2Fgoogle%2Fcallback" in url


@pytest.mark.asyncio
async def test_the_consent_round_trip_returns_to_the_page_it_started_from(client):
    ac, session = client
    state = mailbox_api._make_state(1, "/settings?tab=gmail")

    page = await ac.get("/api/v1/mailbox/google/callback",
                        params={"error": "access_denied", "state": state})

    assert page.status_code == 200
    assert "https://app.example.test/settings?tab=gmail" in page.text


def test_the_return_target_cannot_be_turned_into_an_open_redirect():
    assert mailbox_api._clean_return_to("/settings?tab=gmail") == "/settings?tab=gmail"
    assert mailbox_api._clean_return_to("https://evil.example/steal") == ""
    assert mailbox_api._clean_return_to("//evil.example/steal") == ""
    assert mailbox_api._clean_return_to(None) == ""


@pytest.mark.asyncio
async def test_a_mailbox_connected_with_the_app_saved_client_can_still_refresh(client, monkeypatch):
    """The poller reads no request and sees no env: the DB client must be enough."""
    ac, session = client
    await ac.put("/api/v1/settings/google-oauth", json={
        "client_id": "1234567890-abcdefghijklmnop.apps.googleusercontent.com",
        "client_secret": "GOCSPX-secret-value",
    })
    mailbox = EmailMailbox(
        name="Replies — ada@gmail.com", email_address="ada@gmail.com", provider="gmail_api",
        credential_encrypted=encrypt_value("refresh-token"),
        folders="INBOX,[Gmail]/Spam", poll_interval=60,
    )
    session.add(mailbox)
    await session.commit()

    seen: dict[str, str] = {}

    async def fake_refresh(client_id, client_secret, refresh_token):
        seen.update(client_id=client_id, client_secret=client_secret, refresh_token=refresh_token)
        return {"success": True, "access_token": "ya29.token"}

    monkeypatch.setattr(gmail_provider, "google_refresh_access_token", fake_refresh)

    token, error = await mailbox_service.access_token_for(session, mailbox)

    assert error is None and token == "ya29.token"
    assert seen["client_id"] == "1234567890-abcdefghijklmnop.apps.googleusercontent.com"
    assert seen["client_secret"] == "GOCSPX-secret-value"
    assert seen["refresh_token"] == "refresh-token"


@pytest.mark.asyncio
async def test_the_settings_screen_lists_every_connected_gmail_inbox(client, monkeypatch):
    """Several inboxes side by side is the point of the whole feature."""
    ac, session = client
    for index, address in enumerate(["ada@gmail.com", "bola@gmail.com"], start=1):
        session.add(EmailMailbox(
            name=f"Replies — {address}", email_address=address, provider="gmail_api",
            credential_encrypted=encrypt_value(f"refresh-token-{index}"),
            folders="INBOX,[Gmail]/Spam", poll_interval=60, total_replies=index,
        ))
    session.add(EmailMailbox(
        name="Replies — legacy@imap.test", email_address="legacy@imap.test", provider="imap",
        credential_encrypted=encrypt_value("app-password"), folders="INBOX", poll_interval=120,
    ))
    await session.commit()

    body = (await ac.get("/api/v1/settings/google-oauth")).json()

    assert body["mailbox_count"] == 2                      # the Gmail ones, not the IMAP one
    assert sorted(m["email_address"] for m in body["gmail_mailboxes"]) == [
        "ada@gmail.com", "bola@gmail.com"]
    # No credential of any kind leaves the server: the flag says one is stored,
    # the value itself never appears.
    assert all("credential_encrypted" not in m for m in body["gmail_mailboxes"])
    assert "refresh-token" not in str(body)


# ==========================================================================
# 4. The per-tab counters
# ==========================================================================
#
# The counters come from the inbox list endpoint, and the query behind them is
# also the one PostgreSQL refused in production (see test_postgres_inbox.py).
# These run everywhere, on SQLite, and pin the numbers the tabs show.


@pytest.mark.asyncio
async def test_the_tab_counters_reflect_every_thread_in_the_channel(client):
    ac, session = client
    sms = await _contact(session, phone="+2348030000101")
    unread = await _contact(session, phone="+2348030000102")
    email = await _contact(session, email="ada@example.test")
    await _thread(session, sms, channel="sms")
    unread_conv = await _thread(session, unread, channel="sms", replied=True)
    unread_conv.status = "unread"
    unread_conv.unread_count = 2
    await _thread(session, email, channel="email", replied=True)
    await session.commit()

    sms_body = (await ac.get("/api/v1/inbox/conversations?channel=sms")).json()
    assert sms_body["counts"]["all"] == 2
    assert sms_body["counts"]["unread"] == 1
    assert sms_body["counts"]["active"] == 1

    # The email thread is not part of the SMS counters: "all" is the channel
    # total, and the unread thread is counted in it exactly once.
    assert "email" not in sms_body["counts"]

    everything = (await ac.get("/api/v1/inbox/conversations?channel=all")).json()
    assert everything["counts"]["all"] == 3
