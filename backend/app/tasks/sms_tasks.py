"""Celery SMS tasks — uses send_sms_direct."""
import asyncio,json,logging,os
from datetime import datetime, timezone
from sqlalchemy import select
from app.tasks.celery_app import celery_app
from app.database import async_session_factory
from app.models.conversation import Message
from app.config import settings

logger=logging.getLogger(__name__)

def _get_sim():
    try:return int(open(os.path.join(os.path.dirname(__file__),"..","..","..",".sim_number")).read().strip())
    except:return 1

async def _send_one(mid):
    async with async_session_factory() as db:
        m=(await db.execute(select(Message).where(Message.id==mid))).scalar_one_or_none()
        if not m or m.status in("sent","delivered"):return
        from app.models.contact import Contact
        c=(await db.execute(select(Contact).where(Contact.id==m.contact_id))).scalar_one_or_none()
        if not c:m.status="failed";m.last_error="Contact not found";await db.commit();return
        from app.providers.smsgate import send_sms_direct
        r=await send_sms_direct(c.phone_number,m.body,_get_sim())
        if r["success"]:m.status="sent";m.provider_message_id=r.get("provider_message_id","");m.sent_at=datetime.now(timezone.utc)
        else:
            m.retry_count=(m.retry_count or 0)+1;m.status="failed"if m.retry_count>=3 else"retrying"
            if m.retry_count>=3:m.failed_at=datetime.now(timezone.utc)
            m.last_error=r.get("error")
        m.provider_response=json.dumps(r.get("raw"))if r.get("raw")else None
        await db.commit()

@celery_app.task(bind=True,max_retries=3,default_retry_delay=60)
def send_sms(self,mid):
    try:_run(_send_one(mid))
    except Exception as e:raise self.retry(exc=e)

@celery_app.task
def sync_delivery_status():
    async def s():
        async with async_session_factory() as db:
            ms=(await db.execute(select(Message).where(Message.status.in_(["sent","queued"]),Message.provider_message_id.isnot(None)).limit(100))).scalars().all()
            if not ms:return
            from app.providers.smsgate import SMSGateProvider
            p=SMSGateProvider()
            for m in ms:
                try:st=await p.get_message_status(m.provider_message_id);m.status=st.status if st.status in("delivered","failed")else m.status;m.delivered_at=st.delivered_at if st.status=="delivered"else m.delivered_at
                except:pass
            await p.close();await db.commit()
    _run(s())

@celery_app.task
def gateway_health_check():
    async def c():
        from app.services.sms_service import SMSService
        async with async_session_factory()as db:await SMSService(db).check_gateway_health();await db.commit()
    _run(c())

@celery_app.task
def process_inbound_sms(from_number,body,webhook_data=None):
    async def p():
        from app.services.sms_service import SMSService
        async with async_session_factory()as db:await SMSService(db).process_inbound_message(from_number,body,webhook_data);await db.commit()
    _run(p())

def _run(coro):
    loop=asyncio.get_event_loop()
    if loop.is_closed():loop=asyncio.new_event_loop();asyncio.set_event_loop(loop)
    loop.run_until_complete(coro)
