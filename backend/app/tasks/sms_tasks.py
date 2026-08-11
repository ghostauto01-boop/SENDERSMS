"""Celery SMS tasks — uses send_sms_direct."""
import asyncio,json,logging,os
from datetime import datetime, timezone
from sqlalchemy import select
from app.tasks.celery_app import celery_app
from app.database import async_session_factory
from app.models.conversation import Message
from app.config import settings

logger=logging.getLogger(__name__)

class MessageNotVisible(RuntimeError):
    """The message row could not be found yet (producer commit not visible)."""


async def _send_one(mid):
    """Send one message. Returns True when the gateway should be retried."""
    async with async_session_factory() as db:
        m=(await db.execute(select(Message).where(Message.id==mid))).scalar_one_or_none()
        if m is None:
            # Do NOT treat this as "nothing to do". A publisher that enqueues
            # before committing loses this race, and swallowing it means the
            # contact is never texted at all. Retry -- by then the row exists.
            raise MessageNotVisible(f"Message {mid} not found yet")
        if m.status in("sent","delivered"):return False
        from app.models.contact import Contact
        c=(await db.execute(select(Contact).where(Contact.id==m.contact_id))).scalar_one_or_none()
        if not c:m.status="failed";m.last_error="Contact not found";await db.commit();return False
        from app.providers.smsgate import send_sms_direct
        from app.services.system_settings import get_sim_number
        r=await send_sms_direct(c.phone_number,m.body,await get_sim_number(db))
        if r["success"]:m.status="sent";m.provider_message_id=r.get("provider_message_id","");m.sent_at=datetime.now(timezone.utc)
        else:
            m.retry_count=(m.retry_count or 0)+1;m.status="failed"if m.retry_count>=3 else"retrying"
            if m.retry_count>=3:m.failed_at=datetime.now(timezone.utc)
            m.last_error=r.get("error")
        m.provider_response=json.dumps(r.get("raw"))if r.get("raw")else None
        # Reflect the real gateway outcome on the campaign. The campaign task
        # only knows a message was queued; this is the first point where we
        # know whether the gateway actually accepted it, so the counters and
        # the per-contact row are settled here instead of at enqueue time.
        await _record_campaign_outcome(db,m,bool(r["success"]))
        await db.commit()
        # "retrying" used to be a dead end: nothing ever re-queued these, so a
        # message that hit a transient gateway error sat in that state forever
        # and the contact was never reached. Tell the caller to retry.
        return m.status=="retrying"


async def _record_campaign_outcome(db,m,ok):
    """Roll a send result up onto the campaign and its CampaignContact row."""
    if not m.campaign_id:return
    from app.models.campaign import Campaign,CampaignContact
    camp=(await db.execute(select(Campaign).where(Campaign.id==m.campaign_id))).scalar_one_or_none()
    cc=(await db.execute(select(CampaignContact).where(CampaignContact.campaign_id==m.campaign_id,CampaignContact.contact_id==m.contact_id))).scalar_one_or_none()
    if ok:
        if camp:camp.messages_sent=(camp.messages_sent or 0)+1
        if cc:
            cc.messages_sent=(cc.messages_sent or 0)+1
            # "sent" keeps the contact in the campaign's in-flight set until a
            # delivery receipt arrives; it must not go back to pending or the
            # next batch would text them a second time.
            if cc.status=="queued":cc.status="sent"
        from app.models.contact import Contact
        ct=(await db.execute(select(Contact).where(Contact.id==m.contact_id))).scalar_one_or_none()
        if ct:ct.messages_sent=(ct.messages_sent or 0)+1
    elif m.status=="failed":
        # Only settle as failed once retries are exhausted, otherwise a
        # transient blip would permanently mark the contact undeliverable.
        if camp:camp.messages_failed=(camp.messages_failed or 0)+1
        if cc and cc.status in("queued","sent"):cc.status="failed"

@celery_app.task(bind=True,max_retries=3,default_retry_delay=60)
def send_sms(self,mid):
    try:should_retry=_run(_send_one(mid))
    except MessageNotVisible as e:
        # The producer's transaction has not landed yet. Retry quickly and
        # give up quietly rather than failing the send outright.
        from celery.exceptions import MaxRetriesExceededError
        logger.warning("send_sms(%s): %s; retrying shortly.",mid,e)
        try:raise self.retry(exc=e,countdown=5,max_retries=5)
        except MaxRetriesExceededError:
            logger.error("send_sms(%s): message never became visible; giving up.",mid)
            return
    except Exception as e:raise self.retry(exc=e)
    if should_retry:
        from celery.exceptions import MaxRetriesExceededError
        try:raise self.retry(countdown=60)
        except MaxRetriesExceededError:pass

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
                except Exception:pass
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
    return loop.run_until_complete(coro)
