"""Tests for the GoHighLevel-style reply automation engine."""

import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.automation import Automation
from app.models.contact import Contact, ContactTag, Tag
from app.models.suppression import SuppressionEntry
from app.services.ai_classifier import classify
from app.services.automation_service import AutomationService


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _contact(db, phone="+2348031234567", **kw):
    c = Contact(phone_number=phone, **kw)
    db.add(c)
    await db.flush()
    return c


def _automation(db, conditions, actions, match_all=True, **kw):
    a = Automation(
        name=kw.pop("name", "Test"),
        conditions_json=json.dumps(conditions),
        actions_json=json.dumps(actions),
        match_all=match_all,
        **kw,
    )
    db.add(a)
    return a


class TestConditions:
    @pytest.mark.asyncio
    async def test_sentiment_condition(self, db):
        c = await _contact(db)
        a = _automation(db, [{"field": "sentiment", "op": "is", "value": "negative"}], [])
        await db.flush()
        fired = await AutomationService(db).run_for_reply(c, "wrong number", classify("wrong number"))
        assert fired == 1
        await db.refresh(a)
        assert a.times_triggered == 1

    @pytest.mark.asyncio
    async def test_has_tag_condition(self, db):
        c = await _contact(db)
        tag = Tag(name="restaurants")
        db.add(tag)
        await db.flush()
        db.add(ContactTag(contact_id=c.id, tag_id=tag.id))
        _automation(db, [{"field": "has_tag", "op": "is", "value": "restaurants"}], [])
        await db.flush()
        fired = await AutomationService(db).run_for_reply(c, "hi", classify("hi"))
        assert fired == 1

    @pytest.mark.asyncio
    async def test_match_any(self, db):
        c = await _contact(db)
        _automation(
            db,
            [
                {"field": "sentiment", "op": "is", "value": "positive"},
                {"field": "intent", "op": "is", "value": "wrong_number"},
            ],
            [],
            match_all=False,
        )
        await db.flush()
        fired = await AutomationService(db).run_for_reply(c, "wrong number", classify("wrong number"))
        assert fired == 1

    @pytest.mark.asyncio
    async def test_no_conditions_never_fires(self, db):
        c = await _contact(db)
        _automation(db, [], [])
        await db.flush()
        fired = await AutomationService(db).run_for_reply(c, "hi", classify("hi"))
        assert fired == 0


class TestActions:
    @pytest.mark.asyncio
    async def test_opt_out_action(self, db):
        c = await _contact(db)
        _automation(db, [{"field": "any", "op": "is", "value": ""}], [{"type": "opt_out"}])
        await db.flush()
        await AutomationService(db).run_for_reply(c, "stop", classify("stop"))
        await db.refresh(c)
        assert c.is_opted_out is True
        assert (await db.execute(select(SuppressionEntry))).scalars().first() is not None

    @pytest.mark.asyncio
    async def test_add_tag_action(self, db):
        c = await _contact(db)
        _automation(db, [{"field": "any", "op": "is", "value": ""}], [{"type": "add_tag", "value": "positive-reply"}])
        await db.flush()
        await AutomationService(db).run_for_reply(c, "yes", classify("yes"))
        names = (
            await db.execute(
                select(Tag.name)
                .join(ContactTag, ContactTag.tag_id == Tag.id)
                .where(ContactTag.contact_id == c.id)
            )
        ).scalars().all()
        assert "positive-reply" in names

    @pytest.mark.asyncio
    async def test_set_status_action(self, db):
        c = await _contact(db)
        _automation(db, [{"field": "any", "op": "is", "value": ""}], [{"type": "set_status", "value": "interested"}])
        await db.flush()
        await AutomationService(db).run_for_reply(c, "yes", classify("yes"))
        await db.refresh(c)
        assert c.lead_status == "interested"

    @pytest.mark.asyncio
    async def test_send_sms_action_queues_a_message(self, db, monkeypatch):
        # Avoid the real gateway: the message should be queued for the sweeper.
        async def fake_send(phone, body, sim):
            return {"success": True, "provider_message_id": "fake-1", "raw": {}}

        import app.providers.smsgate as smsgate
        monkeypatch.setattr(smsgate, "send_sms_direct", fake_send)

        c = await _contact(db, first_name="Ada")
        _automation(
            db,
            [{"field": "sentiment", "op": "is", "value": "positive"}],
            [{"type": "send_sms", "body": "Great, we'll send the menu {{first_name}}!"}],
        )
        await db.flush()
        await AutomationService(db).run_for_reply(c, "yes", classify("yes"))

        from app.models.conversation import Message
        outbound = (
            await db.execute(select(Message).where(Message.direction == "outgoing"))
        ).scalars().first()
        assert outbound is not None
        assert outbound.body == "Great, we'll send the menu Ada!"
        assert outbound.status in ("sent", "queued")


class TestEndToEndInbound:
    """Automations must fire from the real inbound-SMS (webhook) path.

    ``SMSService.process_inbound_message`` is what the gateway webhook calls, so
    these tests drive the whole pipeline — contact lookup, AI classification
    stored on the Message, sequence pause, then automation evaluation — instead
    of calling ``run_for_reply`` directly.
    """

    @pytest.mark.asyncio
    async def test_wrong_number_reply_opts_out_via_automation(self, db):
        from app.services.sms_service import SMSService

        c = await _contact(db, phone="+2348031234567")
        _automation(
            db,
            [{"field": "intent", "op": "is", "value": "wrong_number"}],
            [{"type": "opt_out"}],
            name="Wrong-number cleanup",
        )
        await db.commit()

        await SMSService(db).process_inbound_message("+2348031234567", "No, wrong number")
        await db.commit()

        # The reply was classified and the AI fields were stored on the message.
        from app.models.conversation import Message
        incoming = (
            await db.execute(select(Message).where(Message.direction == "incoming"))
        ).scalars().first()
        assert incoming is not None
        assert incoming.ai_intent == "wrong_number"
        assert incoming.ai_sentiment == "negative"

        # The automation fired and opted the contact out.
        await db.refresh(c)
        assert c.is_opted_out is True
        a = (await db.execute(select(Automation))).scalars().first()
        await db.refresh(a)
        assert a.times_triggered == 1

    @pytest.mark.asyncio
    async def test_positive_reply_sends_second_message(self, db, monkeypatch):
        import app.providers.smsgate as smsgate

        async def fake_send(phone, body, sim):
            return {"success": True, "provider_message_id": "e2e-1", "raw": {}}

        monkeypatch.setattr(smsgate, "send_sms_direct", fake_send)

        from app.services.sms_service import SMSService

        c = await _contact(db, phone="+2348031234567", first_name="Ada")
        _automation(
            db,
            [{"field": "sentiment", "op": "is", "value": "positive"}],
            [{"type": "send_sms", "body": "Great, we'll send the menu {{first_name}}!"}],
            name="Yes follow-up",
        )
        await db.commit()

        await SMSService(db).process_inbound_message("+2348031234567", "Yes that is correct")
        await db.commit()

        from app.models.conversation import Message
        outbound = (
            await db.execute(
                select(Message).where(Message.direction == "outgoing")
            )
        ).scalars().all()
        assert any("Ada" in (m.body or "") for m in outbound)

        a = (await db.execute(select(Automation))).scalars().first()
        await db.refresh(a)
        assert a.times_triggered == 1

    @pytest.mark.asyncio
    async def test_non_matching_reply_does_not_fire(self, db, monkeypatch):
        import app.providers.smsgate as smsgate

        async def fake_send(phone, body, sim):
            return {"success": True, "provider_message_id": "e2e-2", "raw": {}}

        monkeypatch.setattr(smsgate, "send_sms_direct", fake_send)

        from app.services.sms_service import SMSService

        c = await _contact(db, phone="+2348031234567")
        _automation(
            db,
            [{"field": "sentiment", "op": "is", "value": "positive"}],
            [{"type": "opt_out"}],
            name="Only act on positive replies",
        )
        await db.commit()

        await SMSService(db).process_inbound_message("+2348031234567", "No, wrong number")
        await db.commit()

        await db.refresh(c)
        assert c.is_opted_out is False
        a = (await db.execute(select(Automation))).scalars().first()
        await db.refresh(a)
        assert a.times_triggered == 0
