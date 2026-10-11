"""P0-5: 5 contacts on a list, 3 verified and 2 never verified ("unknown"), email campaign.

What does validate say, what does start do, how many messages are really created and
to whom, and what does the validator say about its own ability to confirm mailboxes?
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import _common as c


async def main():
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select

    import app.tasks.campaign_tasks as ct
    import app.tasks.queue as queue
    from app.models.campaign import Campaign
    from app.models.contact import Contact
    from app.models.contact_list import ContactList, ContactListMember
    from app.models.conversation import Message
    from campaign_factory import make_account

    c.open_sending_rules()  # the throttles are P0-3's business; keep them from hiding this
    engine, factory = await c.new_db()
    async with factory() as db:
        app = c.sign_in_as_admin(db)

        @asynccontextmanager
        async def _sf():
            yield db

        ct.async_session_factory = _sf
        ct.enqueue = lambda task, *a, **k: None
        queue.enqueue = lambda task, *a, **k: None

        account = await make_account(db, default=True)
        lst = ContactList(name="Audience")
        db.add(lst)
        await db.flush()
        verified, unknown = [], []
        for i in range(5):
            is_verified = i < 3
            contact = Contact(first_name=f"P{i}", email=f"person{i}@leadco{i}.io", email_source="csv",
                              email_verified=is_verified,
                              email_verified_at=datetime.now(timezone.utc) if is_verified else None)
            db.add(contact)
            await db.flush()
            db.add(ContactListMember(list_id=lst.id, contact_id=contact.id))
            (verified if is_verified else unknown).append(contact.id)
        campaign = Campaign(name="Outreach", status="draft", channel="email", list_id=lst.id,
                            message_body="Hello {{first_name}}", subject="Hello",
                            email_account_id=account.id)
        db.add(campaign)
        await db.flush()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            v = (await client.post(f"/api/v1/campaigns/{campaign.id}/validate")).json()
            print(f"validate: valid={v.get('valid')} errors={v.get('errors')}")
            print(f"          audience={v.get('audience')}")
            s = await client.post(f"/api/v1/campaigns/{campaign.id}/start")
            print(f"start: HTTP {s.status_code} {s.text[:160] if s.status_code >= 400 else ''}")
            if s.status_code < 400:
                await ct.process_campaign_batch_async(campaign.id, batch_size=50)
            msgs = (await db.execute(select(Message.contact_id).where(
                Message.campaign_id == campaign.id))).scalars().all()
            to_unknown = [m for m in msgs if m in unknown]
            print(f"messages created for the campaign: {len(msgs)}  "
                  f"(to verified contacts: {len(msgs) - len(to_unknown)}, "
                  f"to UNVERIFIED contacts: {len(to_unknown)})")

            st = (await client.get("/api/v1/validator/status")).json()
            print(f"validator status: engine={st.get('engine')} smtp_enabled={st.get('smtp_enabled')} "
                  f"smtp_reason={st.get('smtp_reason')!r} batch_size={st.get('batch_size')}")
            six = [{"email": f"a{i}@leadco{i}.io"} for i in range(6)]
            r = await client.post("/api/v1/validator/batch", json={"items": six, "deep": False})
            print(f"validator /batch with 6 items: HTTP {r.status_code}")
    app.dependency_overrides.clear()
    await engine.dispose()


c.run(main())
