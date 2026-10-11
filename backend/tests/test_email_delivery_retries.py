"""Durable email attempt logs and bounded retry/backoff behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.ads import AdsCampaign
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.models.email import EmailSendLog
from app.services.email_send_logs import summarize_campaign


async def _message(db, *, campaign_id: int, number: int):
    contact = Contact(
        phone_number=f"+2348031234{number:03d}",
        email=f"recipient{number}@brandmail{number}.io",
        first_name="Ada",
    )
    db.add(contact)
    await db.flush()
    conversation = Conversation(contact_id=contact.id, channel="email", status="active")
    db.add(conversation)
    await db.flush()
    message = Message(
        conversation_id=conversation.id,
        contact_id=contact.id,
        ads_campaign_id=campaign_id,
        channel="email",
        direction="outgoing",
        body="Hello Ada",
        subject="Hello",
        to_address=contact.email,
        bulk_send=True,
        status="queued",
        provider="brevo",
        idempotency_key=f"email-retry-{number}",
    )
    db.add(message)
    await db.flush()
    return message


async def _install_session_factory(monkeypatch, db):
    @asynccontextmanager
    async def session_factory():
        yield db

    monkeypatch.setattr("app.tasks.sms_tasks.async_session_factory", session_factory)


@pytest.mark.asyncio
async def test_brevo_429_waits_60_seconds_and_success_keeps_failed_attempt(
    api_db, monkeypatch
):
    from app.services import email_service
    from app.tasks.sms_tasks import _send_one

    campaign = AdsCampaign(
        name="Retry history", status="active", channel="email", test_mode=False
    )
    api_db.add(campaign)
    await api_db.flush()
    message = await _message(api_db, campaign_id=campaign.id, number=1)
    await api_db.commit()
    await _install_session_factory(monkeypatch, api_db)

    outcomes = [
        {"success": False, "status_code": 429, "error": "rate limit exceeded"},
        {"success": True, "provider_message_id": "brevo-accepted"},
    ]

    async def deliver(_db, _message):
        outcome = outcomes.pop(0)
        if outcome.get("success"):
            _message.provider_message_id = outcome["provider_message_id"]
            _message.sent_at = datetime.now(timezone.utc)
        return outcome

    monkeypatch.setattr(email_service, "deliver", deliver)

    wait = await _send_one(message.id)
    await api_db.refresh(message)
    assert isinstance(wait, int) and 55 <= wait <= 60, message.last_error
    assert message.status == "retrying", message.last_error
    assert message.retry_count == 1
    assert message.retry_at is not None
    assert "60 seconds" in message.last_error

    logs = list((await api_db.execute(
        select(EmailSendLog).where(EmailSendLog.message_id == message.id)
        .order_by(EmailSendLog.attempt)
    )).scalars().all())
    assert len(logs) == 1
    assert logs[0].attempt == 1
    assert logs[0].status == "retrying"
    assert logs[0].error_code == "429"
    assert "rate limit" in logs[0].error_message.lower()
    assert logs[0].retry_at is not None

    # Advance the durable retry timestamp without sleeping through the delay.
    message.retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await api_db.commit()
    result = await _send_one(message.id)
    await api_db.refresh(message)
    assert result is False
    assert message.status == "sent"
    assert message.provider_message_id == "brevo-accepted"

    logs = list((await api_db.execute(
        select(EmailSendLog).where(EmailSendLog.message_id == message.id)
        .order_by(EmailSendLog.attempt)
    )).scalars().all())
    assert [(row.attempt, row.status) for row in logs] == [(1, "retrying"), (2, "sent")]
    assert "rate limit" in logs[0].error_message.lower(), "success must not erase the earlier error"
    summary = await summarize_campaign(api_db, campaign.id)
    assert summary["sent"] == 1
    assert summary["attempts"] == 2
    assert summary["retries"] == 1


@pytest.mark.asyncio
async def test_transient_email_errors_back_off_exponentially_and_stop_after_five(
    api_db, monkeypatch
):
    from app.services import email_service
    from app.tasks.sms_tasks import _send_one

    campaign = AdsCampaign(
        name="Bounded retries", status="active", channel="email", test_mode=False
    )
    api_db.add(campaign)
    await api_db.flush()
    message = await _message(api_db, campaign_id=campaign.id, number=2)
    await api_db.commit()
    await _install_session_factory(monkeypatch, api_db)

    async def deliver(_db, _message):
        return {"success": False, "status_code": 503, "error": "temporary unavailable"}

    monkeypatch.setattr(email_service, "deliver", deliver)

    expected_delays = (60, 120, 240, 480)
    for attempt, expected_delay in enumerate(expected_delays, start=1):
        result = await _send_one(message.id)
        await api_db.refresh(message)
        assert isinstance(result, int)
        assert message.status == "retrying", message.last_error
        assert message.retry_count == attempt
        assert message.retry_at is not None
        retry_at = message.retry_at
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        remaining = (retry_at - datetime.now(timezone.utc)).total_seconds()
        assert expected_delay - 5 <= remaining <= expected_delay
        assert "Automatic retry" in message.last_error
        message.retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await api_db.commit()

    result = await _send_one(message.id)
    await api_db.refresh(message)
    assert result is False
    assert message.status == "failed"
    assert message.retry_count == 5
    assert message.retry_at is None
    assert "failed after 5 attempts" in message.last_error
    assert "check sender health" in message.last_error.lower()

    logs = list((await api_db.execute(
        select(EmailSendLog).where(EmailSendLog.message_id == message.id)
        .order_by(EmailSendLog.attempt)
    )).scalars().all())
    assert [(row.attempt, row.status) for row in logs] == [
        (1, "retrying"), (2, "retrying"), (3, "retrying"),
        (4, "retrying"), (5, "failed"),
    ]
    assert all("temporary unavailable" in (row.error_message or "") for row in logs)
