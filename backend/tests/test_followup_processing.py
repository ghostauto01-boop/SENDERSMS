"""The due-follow-up sweep sends a manual follow-up once, not just records it."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.contact import Contact
from app.models.conversation import Message
from app.models.followup import FollowUp


@pytest.mark.asyncio
async def test_due_manual_followup_is_sent_and_recorded(tmp_path, monkeypatch):
    import app.providers.smsgate as smsgate
    import app.tasks.campaign_tasks as campaign_tasks

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/followups.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as setup:
        contact = Contact(phone_number="+2348031234567", first_name="Ada")
        setup.add(contact)
        await setup.flush()
        followup = FollowUp(
            contact_id=contact.id,
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            message_text="Hi {{first_name}}, checking in.",
            status="pending",
        )
        setup.add(followup)
        await setup.commit()
        followup_id = followup.id

    calls = []

    async def fake_send(phone, body, sim_number):
        calls.append((phone, body, sim_number))
        return {"success": True, "provider_message_id": "simulated-1", "raw": {}}

    monkeypatch.setattr(campaign_tasks, "async_session_factory", factory)
    monkeypatch.setattr(smsgate, "send_sms_direct", fake_send)

    assert await campaign_tasks.process_due_followups_async() == [followup_id]
    # A second sweep cannot claim or send the same row again.
    assert await campaign_tasks.process_due_followups_async() == []
    assert calls == [("+2348031234567", "Hi Ada, checking in.", 1)]

    async with factory() as check:
        saved_followup = (
            await check.execute(select(FollowUp).where(FollowUp.id == followup_id))
        ).scalar_one()
        message = (await check.execute(select(Message))).scalar_one()
        assert saved_followup.status == "sent"
        assert saved_followup.attempt_count == 1
        assert saved_followup.message_id == message.id
        assert message.status == "sent"
        assert message.provider_message_id == "simulated-1"
        assert message.body == "Hi Ada, checking in."

    await engine.dispose()
