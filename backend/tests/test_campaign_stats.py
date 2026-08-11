"""Campaign aggregate counters and conversation summaries.

Two defects found while running a full campaign end to end:

1. `campaign_tasks` created the outgoing Message but never updated the parent
   conversation's message_count / last_message_preview / last_message_at, so a
   contact reached only by a campaign showed up in the inbox as an empty thread
   with no timestamp to sort on. Every other send path already did this.
2. `campaign.messages_delivered`, `messages_failed` and `replies` were read by
   the analytics endpoint and the dashboard but incremented by nothing at all,
   so every campaign permanently reported a 0% delivery rate and 0 replies.
   Delivery webhooks arrive once PER PART for multipart messages, so the
   increment has to be idempotent.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.campaign import Campaign, CampaignContact
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.services.sms_service import SMSService


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def make_campaign(db, name="Promo"):
    camp = Campaign(name=name, status="running")
    db.add(camp)
    await db.flush()
    return camp


async def make_contact(db, phone="+2348031112222", **kw):
    c = Contact(phone_number=phone, country="Nigeria", **kw)
    db.add(c)
    await db.flush()
    return c


async def make_outgoing(db, camp, contact, pid, body="Hi there"):
    conv = (await db.execute(
        select(Conversation).where(Conversation.contact_id == contact.id)
    )).scalars().first()
    if conv is None:
        conv = Conversation(contact_id=contact.id, status="read")
        db.add(conv)
        await db.flush()
    m = Message(
        conversation_id=conv.id, contact_id=contact.id, campaign_id=camp.id,
        direction="outgoing", body=body, segment_count=1, char_count=len(body),
        status="sent", provider="smsgate", provider_message_id=pid,
        idempotency_key=f"out-{pid}",
    )
    db.add(m)
    db.add(CampaignContact(campaign_id=camp.id, contact_id=contact.id, status="sent"))
    await db.flush()
    return conv, m


class TestDeliveryCounters:
    @pytest.mark.asyncio
    async def test_delivered_increments_campaign(self, db):
        camp = await make_campaign(db)
        c = await make_contact(db)
        await make_outgoing(db, camp, c, "p1")

        await SMSService(db).process_delivery_status("p1", "delivered")
        assert camp.messages_delivered == 1

    @pytest.mark.asyncio
    async def test_multipart_delivery_counts_once(self, db):
        """sms:delivered fires once per part; the counter must not multiply."""
        camp = await make_campaign(db)
        c = await make_contact(db)
        await make_outgoing(db, camp, c, "p1")

        svc = SMSService(db)
        for _ in range(4):
            await svc.process_delivery_status("p1", "delivered")
        assert camp.messages_delivered == 1

    @pytest.mark.asyncio
    async def test_failed_increments_failed_counter(self, db):
        camp = await make_campaign(db)
        c = await make_contact(db)
        await make_outgoing(db, camp, c, "p1")

        await SMSService(db).process_delivery_status("p1", "failed")
        assert camp.messages_failed == 1
        assert camp.messages_delivered == 0

    @pytest.mark.asyncio
    async def test_late_sent_does_not_undo_delivered(self, db):
        camp = await make_campaign(db)
        c = await make_contact(db)
        await make_outgoing(db, camp, c, "p1")

        svc = SMSService(db)
        await svc.process_delivery_status("p1", "delivered")
        await svc.process_delivery_status("p1", "sent")
        assert camp.messages_delivered == 1

    @pytest.mark.asyncio
    async def test_non_campaign_message_is_ignored(self, db):
        c = await make_contact(db)
        conv = Conversation(contact_id=c.id)
        db.add(conv)
        await db.flush()
        db.add(Message(
            conversation_id=conv.id, contact_id=c.id, direction="outgoing",
            body="ad hoc", segment_count=1, char_count=6, status="sent",
            provider="smsgate", provider_message_id="p9", idempotency_key="out-p9",
        ))
        await db.flush()
        # Must not raise even though campaign_id is NULL.
        m = await SMSService(db).process_delivery_status("p9", "delivered")
        assert m.status == "delivered"


class TestReplyCounter:
    @pytest.mark.asyncio
    async def test_reply_credits_the_campaign(self, db):
        camp = await make_campaign(db)
        c = await make_contact(db)
        await make_outgoing(db, camp, c, "p1")

        await SMSService(db).process_inbound_message("+2348031112222", "Yes please")
        assert camp.replies == 1

    @pytest.mark.asyncio
    async def test_second_reply_from_same_contact_counts_once(self, db):
        camp = await make_campaign(db)
        c = await make_contact(db)
        await make_outgoing(db, camp, c, "p1")

        svc = SMSService(db)
        await svc.process_inbound_message("+2348031112222", "Yes please")
        await svc.process_inbound_message("+2348031112222", "And what time?")
        assert camp.replies == 1

    @pytest.mark.asyncio
    async def test_reply_marks_campaign_contact(self, db):
        camp = await make_campaign(db)
        c = await make_contact(db)
        await make_outgoing(db, camp, c, "p1")

        await SMSService(db).process_inbound_message("+2348031112222", "Interested")
        cc = (await db.execute(
            select(CampaignContact).where(CampaignContact.campaign_id == camp.id)
        )).scalar_one()
        assert cc.status == "replied"
        assert cc.last_reply_at is not None

    @pytest.mark.asyncio
    async def test_reply_to_a_new_campaign_counts_again(self, db):
        """A contact who replied to last month's campaign is still a new
        responder for this one -- dedup is per campaign, not per contact."""
        old = await make_campaign(db, "Old")
        new = await make_campaign(db, "New")
        c = await make_contact(db)

        await make_outgoing(db, old, c, "p1")
        svc = SMSService(db)
        await svc.process_inbound_message("+2348031112222", "reply to old")
        assert old.replies == 1

        await make_outgoing(db, new, c, "p2", body="new campaign")
        await svc.process_inbound_message("+2348031112222", "reply to new")
        assert new.replies == 1

    @pytest.mark.asyncio
    async def test_inbound_from_unknown_number_does_not_crash(self, db):
        await make_campaign(db)
        m = await SMSService(db).process_inbound_message("+2348039999999", "Hello")
        assert m is not None


class TestOneThreadPerContact:
    """A duplicate conversation row used to be possible (no unique constraint)
    and it permanently broke that contact: every send and every inbound hit
    scalar_one_or_none() and raised MultipleResultsFound."""

    @pytest.mark.asyncio
    async def test_duplicate_conversation_is_rejected(self, db):
        c = await make_contact(db)
        db.add(Conversation(contact_id=c.id))
        await db.flush()
        db.add(Conversation(contact_id=c.id))
        with pytest.raises(Exception):
            await db.flush()

    @pytest.mark.asyncio
    async def test_inbound_reuses_the_existing_thread(self, db):
        c = await make_contact(db)
        conv = Conversation(contact_id=c.id)
        db.add(conv)
        await db.flush()

        await SMSService(db).process_inbound_message("+2348031112222", "hello")
        rows = (await db.execute(
            select(Conversation).where(Conversation.contact_id == c.id)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == conv.id
