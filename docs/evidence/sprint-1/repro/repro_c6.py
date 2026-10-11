"""C6: ONE database, asked the same question of every endpoint that reports message numbers.

SMS: 11 outgoing (6 delivered -- one only by its delivery time --, 2 sent, 1 failed, 1 queued, 1 cancelled)
and 3 replies. Email: 8 outgoing (5 delivered -- one only because it was opened --, 1 sent, 1 failed,
1 queued) and 2 replies. Everything is from the last hour, plus one old message per channel.
"""
from datetime import datetime, timedelta, timezone

import _common as c


async def main():
    from httpx import ASGITransport, AsyncClient

    from app.models.contact import Contact
    from app.models.conversation import Conversation, Message

    engine, factory = await c.new_db()
    async with factory() as db:
        app = c.sign_in_as_admin(db)
        contact = Contact(first_name="Ada", phone_number="+2348031200001", email="ada@leadco.io")
        db.add(contact)
        await db.flush()
        convs = {ch: Conversation(contact_id=contact.id, channel=ch, status="active") for ch in ("sms", "email")}
        db.add_all(convs.values())
        await db.flush()
        n = iter(range(1, 1000))

        def ago(**d):
            return datetime.now(timezone.utc) - timedelta(**d)

        def msg(ch, direction="outgoing", status="sent", age=None, **kw):
            return Message(conversation_id=convs[ch].id, contact_id=contact.id, channel=ch, direction=direction,
                           status=status, body="x", idempotency_key=f"m{next(n)}", provider="x",
                           created_at=ago(**(age or {"hours": 1})), **kw)

        rows = [msg("sms", status="delivered") for _ in range(5)]
        rows += [msg("sms", delivered_at=ago(minutes=30)), msg("sms"), msg("sms"), msg("sms", status="failed"),
                 msg("sms", status="queued"), msg("sms", status="cancelled")]
        rows += [msg("sms", direction="incoming", status="received") for _ in range(3)]
        rows += [msg("email", status="delivered") for _ in range(4)]
        rows += [msg("email", opened_at=ago(minutes=20), open_count=2), msg("email"), msg("email", status="failed"),
                 msg("email", status="queued")]
        rows += [msg("email", direction="incoming", status="received") for _ in range(2)]
        rows += [msg("sms", status="delivered", age={"days": 60}), msg("email", status="delivered", age={"days": 60})]
        db.add_all(rows)
        await db.flush()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            async def get(path, **params):
                response = await client.get(path, params=params)
                return response.json() if response.status_code == 200 else {"_http": response.status_code}

            dash = await get("/api/v1/dashboard/stats", channel="sms")
            ana = await get("/api/v1/analytics/overview", days=30)
            fun = await get("/api/v1/analytics/funnel", days=30)
            ser = await get("/api/v1/analytics/timeseries", days=30)
            e_stats = await get("/api/v1/email/stats", days=30)
            e_over = (await get("/api/v1/email/overview", days=30)).get("totals", {})
            e_dash = await get("/api/v1/dashboard/stats", channel="email")
            hist = await get("/api/v1/send/history", per_page=100)

            def row(label, sent, delivered, failed, replies):
                print(f"  {label:44s} sent={sent!s:>3} delivered={delivered!s:>3} failed={failed!s:>3} replies={replies!s:>3}")

            print("SMS numbers (truth: sent 11 (12 all-time), delivered 6 (7), failed 1, replies 3)")
            row("dashboard/stats?channel=sms  (all time)", dash.get("messages_sent"), dash.get("messages_delivered"),
                dash.get("messages_failed"), dash.get("replies"))
            row("analytics/overview  (30 days, 'SMS')", ana.get("sent"), ana.get("delivered"), ana.get("failed"), ana.get("replies"))
            row("analytics/funnel  (same page, chart)", (fun.get("stages") or [{}])[0].get("count"),
                (fun.get("stages") or [{}, {}])[1].get("count") if fun.get("stages") else None, fun.get("failed"), None)
            pts = ser.get("points") or []
            row("analytics/timeseries  (same page, chart)", sum(p["sent"] for p in pts), sum(p["delivered"] for p in pts),
                sum(p["failed"] for p in pts), sum(p["replies"] for p in pts))
            print(f"  send/history rows (should be SMS only: 12 incl. the old one): {hist.get('total')}")
            print("Email numbers (truth: sent 8 (9 all-time), delivered 5 (6), failed 1, replies 2)")
            row("email/stats  (30 days)", e_stats.get("sent"), e_stats.get("delivered"), e_stats.get("failed"), e_stats.get("replies"))
            row("email/overview totals  (30 days)", e_over.get("sent"), e_over.get("delivered"), e_over.get("failed"), e_over.get("replies"))
            row("dashboard/stats?channel=email  (all time)", e_dash.get("messages_sent"), e_dash.get("messages_delivered"),
                e_dash.get("messages_failed"), e_dash.get("replies"))
    app.dependency_overrides.clear()
    await engine.dispose()


c.run(main())
