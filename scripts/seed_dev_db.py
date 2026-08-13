"""Seed the local dev.sqlite with realistic WhatsApp-style demo conversations.

Run AFTER the backend has created its schema (backend boots -> init_db), with:
  .venv/bin/python seed_dev_db.py
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import httpx

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

API = "http://127.0.0.1:8000/api/v1"
SECRET = "dev-secret"


def sign(body: bytes, ts: str) -> str:
    return hmac.new(SECRET.encode(), body + ts.encode(), hashlib.sha256).hexdigest()


def envelope(event: str, payload: dict, evt_id: str) -> dict:
    return {"deviceId": "dev-1", "event": event, "id": evt_id,
            "webhookId": "wh-1", "payload": payload}


async def post_webhook(client: httpx.AsyncClient, body: dict) -> None:
    raw = json.dumps(body).encode()
    ts = str(int(time.time()))
    r = await client.post(
        f"{API}/webhooks/smsgateway",
        content=raw,
        headers={"Content-Type": "application/json",
                 "X-Signature": sign(raw, ts), "X-Timestamp": ts},
    )
    assert r.status_code == 200, (r.status_code, r.text)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def seed():
    now = datetime.now(timezone.utc)
    d1 = now - timedelta(hours=2)
    d2 = now - timedelta(hours=5)
    d3 = now - timedelta(days=1, hours=3)
    d4 = now - timedelta(days=3, hours=1)
    d5 = now - timedelta(days=6, hours=2)

    async with httpx.AsyncClient(timeout=15) as client:
        # --- Ada Obi (interested lead, mixed history) ---
        conv = [
            ("m-ada-1", d5, "+2348012345678", "Hello Ada! Hope you enjoyed your meal at XYZ Restaurant. Would you like to hear about our weekend deals? 🍕"),
            ("m-ada-2", d4, "+2348012345678", "Yes please! What's the offer?"),
            ("m-ada-3", d3, "+2348012345678", "Great! 20% off all orders above N5,000 this weekend. Use code WEEKEND20 🎉"),
            ("m-ada-4", d2, "+2348012345678", "Nice! I'll come by on Saturday with my friends 😊"),
        ]
        for i, (mid, ts, sender, msg) in enumerate(conv):
            await post_webhook(client, envelope("sms:received", {
                "messageId": mid, "message": msg, "sender": sender,
                "recipient": "+2340000000000", "simNumber": 1,
                "receivedAt": iso(ts)}, f"evt-ada-{i}"))

        # --- MTN alert (should never ping Pushover) ---
        for i in range(3):
            await post_webhook(client, envelope("sms:received", {
                "messageId": f"mtn-alert-{i}", "message": f"MTN: Your balance is N{1500 - i*250}. Recharge via *556*1000# to continue enjoying unlimited data.",
                "sender": "MTN", "recipient": "+2340000000000", "simNumber": 1,
                "receivedAt": iso(now - timedelta(hours=1 - i))}, f"evt-mtn-{i}"))

        # --- AIRTEL alert ---
        for i in range(2):
            await post_webhook(client, envelope("sms:received", {
                "messageId": f"air-alert-{i}", "message": f"AIRTEL: Thank you for recharging. Your new balance is N{800 + i*200}. Valid until {datetime.now().strftime('%d-%m-%Y')}.",
                "sender": "AIRTEL", "recipient": "+2340000000000", "simNumber": 1,
                "receivedAt": iso(now - timedelta(hours=3 + i))}, f"evt-air-{i}"))

        # --- Tunde Bakare (unread reply) ---
        for i, (mid, ts, msg) in enumerate([
            ("m-tun-1", d3, "Tunde, your order #1042 has been dispatched and will arrive tomorrow. Track here: https://track.example.com/1042"),
            ("m-tun-2", now - timedelta(minutes=12), "Please deliver to my office instead. I'll send the address now."),
        ]):
            await post_webhook(client, envelope("sms:received", {
                "messageId": mid, "message": msg, "sender": "+2348098765432",
                "recipient": "+2340000000000", "simNumber": 1,
                "receivedAt": iso(ts)}, f"evt-tun-{i}"))

        # --- GTBank (transaction alert) ---
        await post_webhook(client, envelope("sms:received", {
            "messageId": "gtb-1", "message": "GTBank: You spent N12,500 at JUMIA *4351 on 12 Aug. Available balance: N245,800.90.",
            "sender": "GTBANK", "recipient": "+2340000000000", "simNumber": 1,
            "receivedAt": iso(d1)}, "evt-gtb-1"))

    # --- Outgoing messages + statuses (direct DB writes) ---
    engine = create_async_engine("sqlite+aiosqlite:///./dev.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        from app.models.contact import Contact
        from app.models.conversation import Conversation, Message

        contacts = (await db.execute(select(Contact))).scalars().all()
        by_phone = {c.phone_number: c for c in contacts}

        async def conv_for(phone):
            c = by_phone[phone]
            cv = (await db.execute(select(Conversation).where(Conversation.contact_id == c.id))).scalar_one_or_none()
            return c, cv

        async def out(cv, phone, body, status, ts, err=None):
            cc = (await db.execute(select(Contact).where(Contact.phone_number == phone))).scalar_one()
            m = Message(conversation_id=cv.id, contact_id=cc.id, direction="outgoing",
                        body=body, segment_count=1, char_count=len(body), status=status,
                        provider="smsgate", idempotency_key=f"seed-out-{ts.timestamp()}",
                        created_at=ts, last_error=err,
                        sent_at=ts if status in ("sent", "delivered") else None,
                        delivered_at=ts if status == "delivered" else None,
                        failed_at=ts if status == "failed" else None)
            db.add(m)

        # Ada: outgoing campaign -> delivered, then her replies came back
        _, cv = await conv_for("+2348012345678")
        await out(cv, "+2348012345678", "Hi Ada! It's XYZ Restaurant 🍕 — this weekend only, 20% off orders above N5,000 with code WEEKEND20. Reply YES to book a table!",
                  "delivered", d5 - timedelta(hours=1))
        cv.status = "interested"; cv.message_count = 6; cv.unread_count = 0
        cv.last_message_preview = "Nice! I'll come by on Saturday with my friends 😊"
        cv.last_message_at = d2

        # Tunde: unread, waiting for reply
        _, cv = await conv_for("+2348098765432")
        cv.status = "unread"; cv.unread_count = 2
        cv.last_message_preview = "Please deliver to my office instead. I'll send the address now."
        cv.last_message_at = now - timedelta(minutes=12)

        # MTN: closed (nobody replies to carrier alerts)
        _, cv = await conv_for("MTN")
        cv.status = "closed"; cv.unread_count = 0

        # AIRTEL
        _, cv = await conv_for("AIRTEL")
        cv.status = "closed"; cv.unread_count = 0

        # GTBANK: unread
        _, cv = await conv_for("GTBANK")
        cv.status = "unread"; cv.unread_count = 1

        # New: a chat with only our outgoing message, failed (e.g. no credit)
        from app.models.contact import Contact as C
        nf = C(phone_number="+2347055556666", country="Nigeria", lead_status="new", source="import")
        db.add(nf); await db.flush()
        ncv = Conversation(contact_id=nf.id, status="active", unread_count=0,
                           message_count=1, last_message_preview="Your appointment is confirmed for Friday 9:00 AM.",
                           last_message_at=d1)
        db.add(ncv); await db.flush()
        await out(ncv, "+2347055556666", "Your appointment is confirmed for Friday 9:00 AM. Reply RESCHEDULE to pick a new time.",
                  "failed", d1, err="SIM has no credit")
        await db.commit()
    await engine.dispose()
    print("Seeded dev.db")


asyncio.run(seed())
