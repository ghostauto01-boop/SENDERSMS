"""Tests for the auto-reply feature.

Covers rule matching, the per-contact cooldown, and the two things that must
never happen: replying to someone who just opted out, and replying when the
operator has configured no rules at all.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.autoreply import AutoReplyRule
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.services.autoreply_service import AutoReplyService, rule_matches


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _contact(db, phone="+2348031234510", **kw):
    c = Contact(phone_number=phone, first_name=kw.pop("first_name", "Ada"), **kw)
    db.add(c)
    await db.flush()
    return c


def _rule(**kw):
    kw.setdefault("name", "Test rule")
    kw.setdefault("reply_body", "Thanks for your message!")
    return AutoReplyRule(**kw)


# --- pure matching -------------------------------------------------------


def test_contains_matches_anywhere_case_insensitively():
    r = _rule(keywords="price, pricing", match_type="contains")
    assert rule_matches(r, "what is the PRICE please")
    assert rule_matches(r, "pricing?")
    assert not rule_matches(r, "when do you open")


def test_exact_requires_whole_message():
    r = _rule(keywords="yes", match_type="exact")
    assert rule_matches(r, "yes")
    assert rule_matches(r, "  YES  ")
    assert not rule_matches(r, "yes please")


def test_starts_matches_prefix_only():
    r = _rule(keywords="info", match_type="starts")
    assert rule_matches(r, "info about your service")
    assert not rule_matches(r, "send me info")


def test_any_is_a_catch_all():
    r = _rule(match_type="any", keywords=None)
    assert rule_matches(r, "literally anything")
    assert rule_matches(r, "")


def test_keyword_rule_without_keywords_never_matches():
    """A half-filled rule must stay silent rather than answer everything."""
    r = _rule(keywords="", match_type="contains")
    assert not rule_matches(r, "anything at all")


# --- rule selection ------------------------------------------------------


@pytest.mark.asyncio
async def test_no_rules_configured_means_no_reply(db):
    """The feature ships inert: an empty rule table changes nothing."""
    c = await _contact(db)
    rule, text = await AutoReplyService(db).build_reply(c, "hello")
    assert rule is None and text is None


@pytest.mark.asyncio
async def test_disabled_rules_are_ignored(db):
    c = await _contact(db)
    db.add(_rule(keywords="hello", is_enabled=False))
    await db.flush()
    rule, _ = await AutoReplyService(db).build_reply(c, "hello there")
    assert rule is None


@pytest.mark.asyncio
async def test_lowest_priority_number_wins(db):
    c = await _contact(db)
    db.add(_rule(name="general", keywords="help", priority=100, reply_body="General help"))
    db.add(_rule(name="urgent", keywords="help", priority=1, reply_body="Urgent help"))
    await db.flush()
    rule, text = await AutoReplyService(db).build_reply(c, "help")
    assert rule.name == "urgent"
    assert text == "Urgent help"


@pytest.mark.asyncio
async def test_reply_renders_placeholders(db):
    c = await _contact(db, first_name="Ada")
    db.add(_rule(keywords="hi", reply_body="Hello {{first_name}}, thanks!"))
    await db.flush()
    _, text = await AutoReplyService(db).build_reply(c, "hi")
    assert text == "Hello Ada, thanks!"


@pytest.mark.asyncio
async def test_opted_out_contact_is_never_answered(db):
    """Replying to a STOP is the one thing that gets you blocked."""
    c = await _contact(db)
    c.is_opted_out = True
    db.add(_rule(match_type="any", keywords=None))
    await db.flush()
    rule, text = await AutoReplyService(db).build_reply(c, "STOP")
    assert rule is None and text is None


# --- cooldown ------------------------------------------------------------


async def _prior_auto_reply(db, contact, minutes_ago):
    conv = Conversation(contact_id=contact.id)
    db.add(conv)
    await db.flush()
    m = Message(
        conversation_id=conv.id,
        contact_id=contact.id,
        direction="outgoing",
        body="earlier auto reply",
        status="sent",
        idempotency_key=f"prior-{contact.id}-{minutes_ago}",
        is_auto_reply=True,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )
    db.add(m)
    await db.flush()
    return m


@pytest.mark.asyncio
async def test_cooldown_suppresses_a_second_reply(db):
    c = await _contact(db)
    db.add(_rule(keywords="hi", cooldown_minutes=60))
    await _prior_auto_reply(db, c, minutes_ago=10)
    rule, _ = await AutoReplyService(db).build_reply(c, "hi again")
    assert rule is None


@pytest.mark.asyncio
async def test_cooldown_expires(db):
    c = await _contact(db)
    db.add(_rule(keywords="hi", cooldown_minutes=60))
    await _prior_auto_reply(db, c, minutes_ago=120)
    rule, _ = await AutoReplyService(db).build_reply(c, "hi again")
    assert rule is not None


@pytest.mark.asyncio
async def test_cooldown_is_per_contact(db):
    """One chatty contact must not mute the autoresponder for everyone."""
    chatty = await _contact(db, phone="+2348031234510")
    other = await _contact(db, phone="+2348031234511")
    db.add(_rule(keywords="hi", cooldown_minutes=60))
    await _prior_auto_reply(db, chatty, minutes_ago=5)
    assert (await AutoReplyService(db).build_reply(chatty, "hi"))[0] is None
    assert (await AutoReplyService(db).build_reply(other, "hi"))[0] is not None


@pytest.mark.asyncio
async def test_zero_cooldown_always_replies(db):
    c = await _contact(db)
    db.add(_rule(keywords="hi", cooldown_minutes=0))
    await _prior_auto_reply(db, c, minutes_ago=0)
    rule, _ = await AutoReplyService(db).build_reply(c, "hi")
    assert rule is not None


@pytest.mark.asyncio
async def test_manual_outgoing_message_does_not_trigger_cooldown(db):
    """Only automated replies count; a human reply must not mute the rules."""
    c = await _contact(db)
    db.add(_rule(keywords="hi", cooldown_minutes=60))
    conv = Conversation(contact_id=c.id)
    db.add(conv)
    await db.flush()
    db.add(
        Message(
            conversation_id=conv.id,
            contact_id=c.id,
            direction="outgoing",
            body="typed by a person",
            status="sent",
            idempotency_key="manual-1",
            is_auto_reply=False,
        )
    )
    await db.flush()
    rule, _ = await AutoReplyService(db).build_reply(c, "hi")
    assert rule is not None
