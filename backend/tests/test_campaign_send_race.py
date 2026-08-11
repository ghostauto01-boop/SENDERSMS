"""Regression cover for the campaign send race.

The bug: ``_send_template_message`` handed the message id to Celery *before*
the surrounding transaction committed. A fast worker dequeued the id, looked
it up, found nothing (the row was not visible yet) and returned quietly --
so the contact was never texted and nothing was logged as failed. In a
3-contact batch only the last contact reliably got a message.

Two independent guarantees are asserted here:

1. Producer side: nothing is published to the broker until after commit.
2. Consumer side: a message id that is not visible yet is retried rather
   than silently swallowed.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.campaign import Campaign, CampaignContact
from app.models.contact import Contact
from app.models.conversation import Message


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _make_campaign(db, n_contacts=3):
    campaign = Campaign(
        name="Race",
        status="running",
        message_body="Hi {{first_name}}",
        total_contacts=n_contacts,
    )
    db.add(campaign)
    await db.flush()

    contacts = []
    for i in range(n_contacts):
        c = Contact(
            first_name=f"Ada{i}",
            phone_number=f"+23480312345{10 + i}",
        )
        db.add(c)
        await db.flush()
        db.add(
            CampaignContact(
                campaign_id=campaign.id, contact_id=c.id, status="pending"
            )
        )
        contacts.append(c)
    await db.commit()
    return campaign, contacts


@pytest.mark.asyncio
async def test_send_does_not_enqueue_before_commit(db, monkeypatch):
    """Every contact in a batch is queued, and the outbox defers publishing.

    Previously the enqueue happened inline mid-transaction; this asserts the
    message ids are collected instead, so the caller can publish post-commit.
    """
    from app.tasks import campaign_tasks

    campaign, contacts = await _make_campaign(db, n_contacts=3)

    published = []
    monkeypatch.setattr(
        campaign_tasks, "enqueue", lambda task, *a, **k: published.append(a)
    )

    outbox = []
    ccs = (
        await db.execute(
            CampaignContact.__table__.select().where(
                CampaignContact.campaign_id == campaign.id
            )
        )
    ).fetchall()
    assert len(ccs) == 3

    for row in ccs:
        cc = await db.get(CampaignContact, row.id)
        await campaign_tasks._process_campaign_contact(db, campaign, cc, outbox)

    # Nothing may reach the broker while the transaction is still open.
    assert published == [], "message was published before commit"
    # ...but all three sends must be pending in the outbox.
    assert len(outbox) == 3, f"expected 3 queued sends, got {len(outbox)}"

    msgs = (await db.execute(Message.__table__.select())).fetchall()
    assert len(msgs) == 3
    assert all(m.status == "queued" for m in msgs)


@pytest.mark.asyncio
async def test_flush_outbox_publishes_every_message(db, monkeypatch):
    """_flush_outbox publishes each id exactly once."""
    from app.tasks import campaign_tasks

    published = []
    monkeypatch.setattr(
        campaign_tasks, "enqueue", lambda task, *a, **k: published.append(a[0])
    )

    await campaign_tasks._flush_outbox([11, 22, 33])
    assert published == [11, 22, 33]


@pytest.mark.asyncio
async def test_flush_outbox_noop_when_empty(monkeypatch):
    from app.tasks import campaign_tasks

    called = []
    monkeypatch.setattr(
        campaign_tasks, "enqueue", lambda *a, **k: called.append(a)
    )
    await campaign_tasks._flush_outbox([])
    assert called == []


@pytest.mark.asyncio
async def test_missing_message_is_retried_not_swallowed(monkeypatch):
    """A not-yet-visible message id must raise, so Celery retries it.

    Returning False here is what made the lost sends invisible: the worker
    reported success for a message it never sent.
    """
    from app.tasks import sms_tasks

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(sms_tasks, "async_session_factory", factory)

    try:
        with pytest.raises(sms_tasks.MessageNotVisible):
            await sms_tasks._send_one(999999)
    finally:
        await engine.dispose()
