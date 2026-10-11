"""P0-3: a 200-contact email campaign, ONE mailbox, started on a Saturday night.

Before: the sending gate was never consulted for email, so the whole audience was
queued and handed to the provider. After: nothing goes out of hours, and inside the
window exactly the daily cap (30 per mailbox) is queued -- and still only 30 on a
second pass.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import _common as c

LAGOS = ZoneInfo("Africa/Lagos")


async def scenario(label, moment):
    from sqlalchemy import func, select

    import app.tasks.campaign_tasks as ct
    import app.tasks.sms_tasks as st
    from app.models.campaign import CampaignContact
    from app.models.contact_list import ContactListMember
    from app.models.conversation import Message
    from app.services import email_service, sending_limits
    from campaign_factory import make_account, make_campaign

    engine, factory = await c.new_db()
    async with factory() as db:
        if hasattr(sending_limits, "_utcnow"):
            sending_limits._utcnow = lambda: moment.astimezone(timezone.utc)
        from app.models.template import Template

        account = await make_account(db, default=True)
        template = Template(name="Hello", channel="email", subject="Hello", body="Hello {{first_name}}")
        db.add(template)
        await db.flush()
        # A template is attached on purpose: an email campaign carrying only its own message
        # crashed the OLD code with UnboundLocalError before it ever reached the throttle.
        camp = await make_campaign(db, status="running", channel="email", n=200,
                                   email_account_id=account.id, template_id=template.id)
        ids = (await db.execute(select(ContactListMember.contact_id).where(
            ContactListMember.list_id == camp.list_id))).scalars().all()
        for cid in ids:
            db.add(CampaignContact(campaign_id=camp.id, contact_id=cid, status="pending"))
        await db.flush()

        @asynccontextmanager
        async def _f():
            yield db

        ct.async_session_factory = _f
        st.async_session_factory = _f
        ct.enqueue = lambda *a, **k: None

        async def delivered(db_, message):
            message.status = "sent"
            message.sent_at = moment.astimezone(timezone.utc)
            return {"success": True}

        email_service.deliver = delivered

        print(f"-- {label}: {moment.isoformat()}")
        for attempt in (1, 2):
            await ct.process_campaign_batch_async(camp.id, batch_size=200)
            created = (await db.execute(select(func.count(Message.id)).where(
                Message.campaign_id == camp.id))).scalar_one()
            print(f"   batch pass {attempt}: {created} email messages exist (audience 200, one mailbox)")
        for mid in (await db.execute(select(Message.id).where(Message.campaign_id == camp.id))).scalars().all():
            await st._send_one(mid)
        sent = (await db.execute(select(func.count(Message.id)).where(
            Message.campaign_id == camp.id, Message.status.in_(("sent", "delivered"))))).scalar_one()
        pending = (await db.execute(select(func.count(CampaignContact.id)).where(
            CampaignContact.campaign_id == camp.id, CampaignContact.status == "pending"))).scalar_one()
        print(f"   handed to the provider in this harness: {sent} "
              f"(the 30 s send pacing is not waited out here); contacts still pending: {pending}")
    await engine.dispose()


async def main():
    from app.services import sending_limits

    print("sending rules in force:", {k: sending_limits.DEFAULTS.get(k) for k in (
        "enable_daily_limit", "sending_start_time", "sending_end_time", "allow_weekends")})
    await scenario("Saturday 23:30 Lagos (the original reproduction)",
                   datetime(2026, 9, 5, 23, 30, tzinfo=LAGOS))
    await scenario("Wednesday 12:00 Lagos (inside the window)",
                   datetime(2026, 9, 2, 12, 0, tzinfo=LAGOS))


c.run(main())
