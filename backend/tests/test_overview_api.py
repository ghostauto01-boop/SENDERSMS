"""Unified overview + campaign drill-down endpoints.

These endpoints are the "sync my campaign overview to my application
overview" surface: one list that reports both campaign systems, and a
per-campaign drill-down into the inbox threads it produced.

The numbers must be derived from the same tables the inbox renders
(``messages`` / ``conversations``), otherwise the overview and the inbox
quote different figures for the same day -- which is exactly the bug that
made two separate dashboards untrustworthy.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.models.campaign import Campaign, CampaignContact
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.security.auth import get_current_user


@pytest_asyncio.fixture
async def client():
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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, session
    app.dependency_overrides.clear()
    await session.close()
    await engine.dispose()


async def seed(session, *, replies=1, sent=2):
    """One campaign, `sent` contacts messaged, `replies` of them replied."""
    camp = Campaign(name="Lagos Promo", status="running", total_contacts=sent)
    session.add(camp)
    await session.flush()

    for i in range(sent):
        contact = Contact(phone_number=f"+23480311100{i:02d}", country="Nigeria")
        session.add(contact)
        await session.flush()
        session.add(CampaignContact(campaign_id=camp.id, contact_id=contact.id, status="sent"))
        conv = Conversation(
            contact_id=contact.id,
            status="active",
            campaign_id=camp.id,
            last_campaign_id=camp.id,
        )
        session.add(conv)
        await session.flush()
        session.add(
            Message(
                conversation_id=conv.id,
                contact_id=contact.id,
                campaign_id=camp.id,
                direction="outgoing",
                body="offer",
                status="delivered",
                idempotency_key=f"out-{i}",
            )
        )
        if i < replies:
            conv.status = "interested"
            conv.unread_count = 1
            session.add(
                Message(
                    conversation_id=conv.id,
                    contact_id=contact.id,
                    campaign_id=camp.id,
                    direction="incoming",
                    body="Yes I am interested",
                    status="delivered",
                    ai_sentiment="positive",
                    idempotency_key=f"in-{i}",
                )
            )
    await session.flush()
    return camp


@pytest.mark.asyncio
async def test_overview_lists_campaigns_with_inbox_derived_counts(client):
    ac, session = client
    camp = await seed(session, replies=1, sent=3)

    r = await ac.get("/api/v1/overview/campaigns")

    assert r.status_code == 200, r.text
    data = r.json()
    row = next(i for i in data["items"] if i["id"] == camp.id and i["kind"] == "campaign")
    assert row["name"] == "Lagos Promo"
    assert row["is_live"] is True
    assert row["sent"] == 3
    assert row["delivered"] == 3
    assert row["leads"] == 3
    assert row["replied"] == 1
    assert row["unread"] == 1
    assert row["interested"] == 1
    assert row["reply_rate"] == pytest.approx(33.3, abs=0.1)
    # Deep links the UI turns into "see the replies" buttons.
    assert row["inbox_url"] == f"/inbox?campaign_id={camp.id}"
    assert row["replies_url"] == f"/inbox?campaign_id={camp.id}&replied=1"


@pytest.mark.asyncio
async def test_overview_totals_reconcile_with_the_rows(client):
    ac, session = client
    await seed(session, replies=2, sent=4)

    data = (await ac.get("/api/v1/overview/campaigns")).json()

    totals = data["totals"]
    assert totals["sent"] == sum(i["sent"] for i in data["items"])
    assert totals["replied"] == sum(i["replied"] for i in data["items"])
    assert totals["live"] == 1


@pytest.mark.asyncio
async def test_unified_metrics_matches_the_inbox(client):
    ac, session = client
    await seed(session, replies=2, sent=5)

    data = (await ac.get("/api/v1/overview/metrics", params={"days": 30})).json()

    assert data["messaging"]["sent"] == 5
    assert data["messaging"]["delivered"] == 5
    assert data["messaging"]["replies"] == 2
    assert data["inbox"]["conversations"] == 5
    assert data["inbox"]["replied"] == 2
    assert data["inbox"]["unread"] == 2
    assert data["inbox"]["sentiment"]["positive"] == 2
    assert data["audience"]["contacts"] == 5
    # The campaign block is the same join as /overview/campaigns.
    assert data["campaigns"]["replied"] == 2
    assert len(data["series"]) == 30


@pytest.mark.asyncio
async def test_campaign_conversations_drill_down(client):
    ac, session = client
    camp = await seed(session, replies=1, sent=3)

    r = await ac.get(f"/api/v1/campaigns/{camp.id}/conversations")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 3
    replied = [i for i in data["items"] if i["has_replied"]]
    assert len(replied) == 1
    assert replied[0]["last_reply"]["body"] == "Yes I am interested"
    assert replied[0]["last_reply"]["ai_sentiment"] == "positive"
    # Every row carries the ids needed to open the chat in the inbox.
    assert all(i["conversation_id"] and i["contact_id"] for i in data["items"])


@pytest.mark.asyncio
async def test_campaign_conversations_replied_only(client):
    ac, session = client
    camp = await seed(session, replies=1, sent=3)

    r = await ac.get(
        f"/api/v1/campaigns/{camp.id}/conversations", params={"replied_only": True}
    )

    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["has_replied"] is True


@pytest.mark.asyncio
async def test_campaign_performance_is_message_derived(client):
    ac, session = client
    camp = await seed(session, replies=1, sent=4)

    data = (await ac.get(f"/api/v1/campaigns/{camp.id}/performance")).json()

    assert data["sent"] == 4
    assert data["delivered"] == 4
    assert data["leads"] == 4
    assert data["replied"] == 1
    assert data["interested"] == 1
    assert data["sentiment"]["positive"] == 1
    assert data["delivery_rate"] == 100.0
    assert data["reply_rate"] == 25.0


@pytest.mark.asyncio
async def test_inbox_can_be_filtered_to_one_campaign(client):
    ac, session = client
    camp = await seed(session, replies=1, sent=3)
    # A contact with no campaign at all must be excluded by the filter.
    stray = Contact(phone_number="+2349099999999", country="Nigeria")
    session.add(stray)
    await session.flush()
    session.add(Conversation(contact_id=stray.id, status="active"))
    await session.flush()

    everything = (await ac.get("/api/v1/inbox/conversations")).json()
    filtered = (
        await ac.get("/api/v1/inbox/conversations", params={"campaign_id": camp.id})
    ).json()
    replies = (
        await ac.get(
            "/api/v1/inbox/conversations",
            params={"campaign_id": camp.id, "replied_only": True},
        )
    ).json()

    assert everything["total"] == 4
    assert filtered["total"] == 3
    assert replies["total"] == 1
    # Every filtered row carries the campaign badge the inbox renders.
    assert all(i["campaign"]["id"] == camp.id for i in filtered["items"])
    assert all(i["campaign"]["name"] == "Lagos Promo" for i in filtered["items"])


@pytest.mark.asyncio
async def test_inbox_campaign_filters_lists_only_campaigns_with_leads(client):
    ac, session = client
    camp = await seed(session, replies=1, sent=2)
    empty = Campaign(name="Never Sent", status="draft")
    session.add(empty)
    stray = Contact(phone_number="+2349088888888", country="Nigeria")
    session.add(stray)
    await session.flush()
    session.add(Conversation(contact_id=stray.id, status="active"))
    await session.flush()

    data = (await ac.get("/api/v1/inbox/campaign-filters")).json()

    names = {i["name"] for i in data["items"]}
    assert "Lagos Promo" in names
    assert "Never Sent" not in names, "a campaign with no leads must not be offered"
    row = next(i for i in data["items"] if i["id"] == camp.id)
    assert row["leads"] == 2
    assert row["replies"] == 1
    assert data["unattributed"]["leads"] == 1


@pytest.mark.asyncio
async def test_conversation_detail_carries_the_campaign_badge(client):
    ac, session = client
    camp = await seed(session, replies=1, sent=1)
    conv_id = (
        await session.execute(__import__("sqlalchemy").select(Conversation.id))
    ).scalars().first()

    data = (await ac.get(f"/api/v1/inbox/conversations/{conv_id}")).json()

    assert data["campaign"]["id"] == camp.id
    assert data["campaign"]["name"] == "Lagos Promo"
    assert data["campaign"]["kind"] == "campaign"


@pytest.mark.asyncio
async def test_a_retargeted_lead_is_not_counted_twice_in_the_totals(client):
    """One person touched by two campaigns is one lead in the header.

    Per-campaign rows deliberately credit BOTH campaigns -- each one really
    did work that lead, and each one's reply rate must say so. But the header
    sits directly above the list, so summing the rows would render as a
    contradiction: "3 leads" over a list of 2 people.
    """
    ac, session = client
    from app.models.ads import AdsCampaign

    camp = await seed(session, replies=1, sent=2)
    ads = AdsCampaign(name="Retarget", objective="replies", status="active")
    session.add(ads)
    await session.flush()

    # Retarget the contact who did NOT reply: now they belong to both.
    conv = (
        await session.execute(
            select(Conversation).where(Conversation.campaign_id == camp.id).order_by(
                Conversation.id.desc()
            )
        )
    ).scalars().first()
    conv.last_campaign_id = None
    conv.last_ads_campaign_id = ads.id
    await session.flush()

    data = (await ac.get("/api/v1/overview/campaigns")).json()
    rows = {(i["kind"], i["id"]): i for i in data["items"]}

    # Both campaigns still claim that lead...
    assert rows[("campaign", camp.id)]["leads"] == 2
    assert rows[("ads", ads.id)]["leads"] == 1
    assert sum(i["leads"] for i in data["items"]) == 3

    # ...but only two actual people are being talked to.
    assert data["totals"]["leads"] == 2

    conversations = (
        await session.execute(select(func.count()).select_from(Conversation))
    ).scalar()
    assert data["totals"]["leads"] == conversations


@pytest.mark.asyncio
async def test_metrics_header_agrees_with_the_inbox_conversation_count(client):
    """The two halves of the overview screen must never disagree."""
    ac, session = client
    from app.models.ads import AdsCampaign

    camp = await seed(session, replies=2, sent=3)
    ads = AdsCampaign(name="Retarget", objective="replies", status="active")
    session.add(ads)
    await session.flush()
    conv = (
        await session.execute(
            select(Conversation).where(Conversation.campaign_id == camp.id).order_by(
                Conversation.id
            )
        )
    ).scalars().first()
    conv.last_ads_campaign_id = ads.id
    await session.flush()

    data = (await ac.get("/api/v1/overview/metrics?days=30")).json()

    # "N conversations" in the inbox panel and "N leads" in the campaign
    # panel are the same people, so they must be the same number.
    assert data["campaigns"]["leads"] == data["inbox"]["conversations"]
    assert data["campaigns"]["replied"] == data["inbox"]["replied"]
