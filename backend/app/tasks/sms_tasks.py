"""Celery SMS tasks — provider from config, no DB gateway table."""
import asyncio, json, logging
from datetime import datetime, timezone
from sqlalchemy import select
from app.tasks.celery_app import celery_app
from app.database import async_session_factory
from app.models.conversation import Message
from app.providers.smsgate import SMSGateProvider
from app.config import settings

logger = logging.getLogger(__name__)

import os
def _get_sim_file():
    try:
        p = os.path.join(os.path.dirname(__file__), "..", "..", "..", ".sim_number")
        with open(p) as f: return int(f.read().strip())
    except: return 1

def _p():
    return SMSGateProvider(
        base_url=settings.SMSGATE_BASE_URL or "https://api.sms-gate.app/3rdparty/v1",
        username=settings.SMSGATE_USERNAME or "",
        password=settings.SMSGATE_PASSWORD or "",
        sim_number=_get_sim_file(), timeout=45)

async def _send_one(mid):
    async with async_session_factory() as db:
        m = (await db.execute(select(Message).where(Message.id == mid))).scalar_one_or_none()
        if not m or m.status in ("sent","delivered"): return
        from app.models.contact import Contact
        c = (await db.execute(select(Contact).where(Contact.id == m.contact_id))).scalar_one_or_none()
        if not c: m.status="failed"; m.last_error="Contact not found"; await db.commit(); return
        p = _p()
        r = await p.send_sms(to_number=c.phone_number, message=m.body, idempotency_key=m.idempotency_key)
        if r.success: m.status="sent"; m.provider_message_id=r.provider_message_id; m.sent_at=datetime.now(timezone.utc)
        else:
            m.retry_count=(m.retry_count or 0)+1
            m.status="failed" if m.retry_count>=3 else "retrying"
            if m.retry_count>=3: m.failed_at=datetime.now(timezone.utc)
            m.last_error=r.error
        m.provider_response=json.dumps(r.raw_response) if r.raw_response else None
        await p.close(); await db.commit()

@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_sms(self, mid):
    try: _run(_send_one(mid))
    except Exception as e: raise self.retry(exc=e)

@celery_app.task
def sync_delivery_status():
    async def s():
        async with async_session_factory() as db:
            ms = (await db.execute(select(Message).where(Message.status.in_(["sent","queued"]), Message.provider_message_id.isnot(None)).limit(100))).scalars().all()
            if not ms: return
            p = _p()
            for m in ms:
                try:
                    st = await p.get_message_status(m.provider_message_id)
                    if st.status in ("delivered","failed"): m.status=st.status; m.delivered_at=st.delivered_at or (datetime.now(timezone.utc) if st.status=="delivered" else None)
                except: pass
            await p.close(); await db.commit()
    _run(s())

@celery_app.task
def poll_inbox():
    async def p():
        async with async_session_factory() as db:
            pr = _p(); msgs = await pr.poll_inbox()
            from app.services.sms_service import SMSService; svc = SMSService(db)
            for msg in msgs[:20]:
                snd=msg.get("sender",msg.get("phoneNumber","")); txt=msg.get("text",msg.get("body","")); mid=msg.get("messageId",msg.get("id",""))
                if snd and txt and mid and not (await db.execute(select(Message).where(Message.idempotency_key==f"inbound-{mid}"))).scalar_one_or_none():
                    await svc.process_inbound_message(snd, txt, {"messageId":mid})
            await pr.close(); await db.commit()
    _run(p())

@celery_app.task
def gateway_health_check():
    async def c():
        from app.services.sms_service import SMSService
        async with async_session_factory() as db: await SMSService(db).check_gateway_health(); await db.commit()
    _run(c())

@celery_app.task
def process_inbound_sms(from_number, body, webhook_data=None):
    async def p():
        from app.services.sms_service import SMSService
        async with async_session_factory() as db: await SMSService(db).process_inbound_message(from_number, body, webhook_data); await db.commit()
    _run(p())

def _run(coro):
    loop = asyncio.get_event_loop()
    if loop.is_closed(): loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    loop.run_until_complete(coro)
