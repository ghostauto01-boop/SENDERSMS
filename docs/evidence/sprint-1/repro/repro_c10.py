"""C10: is the Brevo webhook token (the credential for the inbound/events endpoint)
visible in a plain read, and can it be revealed/rotated?"""
import _common as c


async def main():
    from httpx import ASGITransport, AsyncClient

    from app.config import settings
    from app.services import email_service
    from campaign_factory import make_account

    settings.PUBLIC_BASE_URL = "https://app.example.test"
    engine, factory = await c.new_db()
    async with factory() as db:
        app = c.sign_in_as_admin(db)
        acct = await make_account(db, default=True)
        token = await email_service.ensure_webhook_token(db, acct)
        await db.commit()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/email/accounts")
            row = r.json()[0] if isinstance(r.json(), list) else r.json()["items"][0]
            print(f"GET /email/accounts  -> full token present in the response: {token in r.text}")
            print(f"   webhook_url = {row['webhook_url'].replace(token, '<FULL-TOKEN>')}")
            h = await client.post(f"/api/v1/webhooks/brevo/{acct.id}?token=wrong", json={"event": "delivered"})
            print(f"webhook with a wrong token -> HTTP {h.status_code}")
            for action in ("reveal", "rotate"):
                x = await client.post(f"/api/v1/email/accounts/{acct.id}/webhook/{action}")
                print(f"POST .../webhook/{action} -> HTTP {x.status_code}")
    app.dependency_overrides.clear()
    await engine.dispose()


c.run(main())
