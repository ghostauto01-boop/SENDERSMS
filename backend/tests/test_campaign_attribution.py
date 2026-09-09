"""Campaign attribution: which campaign is each inbox lead from?

The inbox reads ``conversations``; every send path knew its campaign but only
recorded it on ``messages``. These tests lock down the join that makes the
inbox badge, the campaign->replies drill-down and the unified overview agree
with each other.

Covered:

1. A campaign send stamps first-touch AND last-touch attribution.
2. First touch never moves when a second campaign re-targets the same lead;
   last touch always does.
3. Threads created before attribution existed are repaired from their own
   message history (no migration job needed).
4. An inbound reply is credited to the campaign that last messaged the
   contact, so campaign reply counts and the inbox agree.
5. The ads manager attributes to ``ads_campaign_id``, kept separate from the
   classic ``campaign_id`` because they are different tables.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.services.attribution import (
    backfill_conversation,
    campaign_label_map,
    conversation_campaign,
    conversation_last_campaign,
    stamp_conversation,
)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def make_campaign(db, name="Promo", status="running"):
    camp = Campaign(name=name, status=status)
    db.add(camp)
    await db.flush()
    return camp


async def make_contact(db, phone="+2348031112222"):
    contact = Contact(phone_number=phone, country="Nigeria")
    db.add(contact)
    await db.flush()
    return contact


async def make_conversation(db, contact):
    conv = Conversation(contact_id=contact.id, status="active")
    db.add(conv)
    await db.flush()
    return conv


async def add_message(db, conv, contact, *, campaign_id=None, ads_campaign_id=None,
                      direction="outgoing", body="hi", key=None):
    msg = Message(
        conversation_id=conv.id,
        contact_id=contact.id,
        campaign_id=campaign_id,
        ads_campaign_id=ads_campaign_id,
        direction=direction,
        body=body,
        idempotency_key=key or f"k-{conv.id}-{body}-{direction}-{campaign_id}-{ads_campaign_id}",
    )
    db.add(msg)
    await db.flush()
    return msg


@pytest.mark.asyncio
async def test_stamp_sets_first_and_last_touch(db):
    contact = await make_contact(db)
    conv = await make_conversation(db, contact)
    camp = await make_campaign(db)

    stamp_conversation(conv, campaign_id=camp.id)

    assert conv.campaign_id == camp.id
    assert conv.last_campaign_id == camp.id
    assert conv.ads_campaign_id is None


@pytest.mark.asyncio
async def test_first_touch_is_sticky_last_touch_moves(db):
    """A lead sourced by January Promo stays a January Promo lead."""
    contact = await make_contact(db)
    conv = await make_conversation(db, contact)
    first = await make_campaign(db, "January Promo")
    second = await make_campaign(db, "March Re-engagement")

    stamp_conversation(conv, campaign_id=first.id)
    stamp_conversation(conv, campaign_id=second.id)

    assert conv.campaign_id == first.id, "first touch must not be overwritten"
    assert conv.last_campaign_id == second.id, "last touch must follow the newest send"


@pytest.mark.asyncio
async def test_ads_and_classic_attribution_do_not_collide(db):
    """The two campaign tables use separate columns; only one is ever set."""
    contact = await make_contact(db)
    conv = await make_conversation(db, contact)

    stamp_conversation(conv, ads_campaign_id=77)
    assert conv.ads_campaign_id == 77
    assert conv.campaign_id is None
    assert conv.last_ads_campaign_id == 77
    assert conv.last_campaign_id is None

    # A later classic campaign takes last touch, but first touch stays on ads.
    camp = await make_campaign(db)
    stamp_conversation(conv, campaign_id=camp.id)
    assert conv.ads_campaign_id == 77
    assert conv.last_campaign_id == camp.id
    assert conv.last_ads_campaign_id is None


@pytest.mark.asyncio
async def test_stamp_with_no_campaign_is_a_noop(db):
    """Manual sends must not clear a thread's existing attribution."""
    contact = await make_contact(db)
    conv = await make_conversation(db, contact)
    camp = await make_campaign(db)
    stamp_conversation(conv, campaign_id=camp.id)

    stamp_conversation(conv)  # a hand-typed inbox reply

    assert conv.campaign_id == camp.id
    assert conv.last_campaign_id == camp.id


@pytest.mark.asyncio
async def test_backfill_repairs_legacy_thread_from_message_history(db):
    """Existing data gets the right badge with no migration job."""
    contact = await make_contact(db)
    conv = await make_conversation(db, contact)
    first = await make_campaign(db, "Old Campaign")
    latest = await make_campaign(db, "Newer Campaign")

    # A thread from before attribution: messages carry the campaign, the
    # conversation does not.
    await add_message(db, conv, contact, campaign_id=first.id, body="m1")
    await add_message(db, conv, contact, campaign_id=latest.id, body="m2")
    assert conv.campaign_id is None

    changed = await backfill_conversation(db, conv)

    assert changed is True
    assert conv.campaign_id == first.id, "first touch = oldest outgoing campaign message"
    assert conv.last_campaign_id == latest.id, "last touch = newest"


@pytest.mark.asyncio
async def test_backfill_is_idempotent_and_cheap(db):
    contact = await make_contact(db)
    conv = await make_conversation(db, contact)
    camp = await make_campaign(db)
    await add_message(db, conv, contact, campaign_id=camp.id)

    assert await backfill_conversation(db, conv) is True
    # Second call has nothing to do and reports no change, so the inbox does
    # not flush the session on every poll.
    assert await backfill_conversation(db, conv) is False


@pytest.mark.asyncio
async def test_backfill_ignores_threads_with_no_campaign_messages(db):
    """A purely inbound thread has no campaign, and must not invent one."""
    contact = await make_contact(db)
    conv = await make_conversation(db, contact)
    await add_message(db, conv, contact, direction="incoming", body="who is this?")

    assert await backfill_conversation(db, conv) is False
    assert conv.campaign_id is None
    assert conv.last_campaign_id is None


@pytest.mark.asyncio
async def test_label_map_resolves_names_for_the_badge(db):
    contact = await make_contact(db)
    conv = await make_conversation(db, contact)
    camp = await make_campaign(db, "Lagos Restaurants")
    stamp_conversation(conv, campaign_id=camp.id)

    labels = await campaign_label_map(db, [conv])
    badge = conversation_campaign(conv, labels)

    assert badge == {
        "id": camp.id,
        "kind": "campaign",
        "name": "Lagos Restaurants",
        "status": "running",
    }
    assert conversation_last_campaign(conv, labels)["id"] == camp.id


@pytest.mark.asyncio
async def test_inbound_reply_is_credited_to_the_last_campaign(db):
    """The reply the inbox shows and the campaign's reply count agree."""
    from app.services.sms_service import SMSService

    contact = await make_contact(db, "+2348031119999")
    conv = await make_conversation(db, contact)
    camp = await make_campaign(db, "Reply Test")
    await add_message(db, conv, contact, campaign_id=camp.id, body="offer")
    stamp_conversation(conv, campaign_id=camp.id)
    await db.flush()

    msg = await SMSService(db).process_inbound_message("+2348031119999", "Yes please")

    assert msg is not None
    assert msg.direction == "incoming"
    assert msg.campaign_id == camp.id, "reply must be attributed to the campaign"

    refreshed = (
        await db.execute(select(Campaign).where(Campaign.id == camp.id))
    ).scalar_one()
    assert refreshed.replies == 1


@pytest.mark.asyncio
async def test_reply_on_a_legacy_thread_still_gets_attributed(db):
    """Backfill runs on the receive path, so old threads self-heal."""
    from app.services.sms_service import SMSService

    contact = await make_contact(db, "+2348031118888")
    conv = await make_conversation(db, contact)
    camp = await make_campaign(db, "Legacy")
    # Message carries the campaign; the conversation predates attribution.
    await add_message(db, conv, contact, campaign_id=camp.id, body="legacy blast")
    await db.flush()
    assert conv.last_campaign_id is None

    msg = await SMSService(db).process_inbound_message("+2348031118888", "interested")

    assert msg.campaign_id == camp.id
    assert conv.campaign_id == camp.id
    assert conv.last_campaign_id == camp.id
