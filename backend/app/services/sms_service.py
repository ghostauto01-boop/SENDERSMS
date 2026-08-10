"""SMS Service — uses direct send_sms + Pushover notifications."""
import json,logging,uuid,os
from datetime import datetime, timezone
from sqlalchemy import select,update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.contact import Contact
from app.models.conversation import Conversation,Message
from app.models.suppression import SuppressionEntry
from app.models.campaign import CampaignContact
from app.models.followup import FollowUp
from app.models.notification import NotificationProvider
from app.utils.phone import normalize_nigerian_number,detect_opt_out_keyword,count_sms_segments
from app.security.encryption import decrypt_value
from app.config import settings

logger = logging.getLogger(__name__)

class SMSService:
    def __init__(self,db:AsyncSession):self.db=db

    @staticmethod
    def _get_sim():
        try: return int(open(os.path.join(os.path.dirname(__file__),"..","..","..",".sim_number")).read().strip())
        except: return 1

    async def send_message(self,contact_id,body,campaign_id=None):
        c=(await self.db.execute(select(Contact).where(Contact.id==contact_id))).scalar_one_or_none()
        if not c or c.is_opted_out:return None
        if(await self.db.execute(select(SuppressionEntry).where(SuppressionEntry.phone_number==c.phone_number))).scalar_one_or_none():return None
        body=body.replace("{{first_name}}",c.first_name or"").replace("{{business_name}}",c.business_name or"").replace("{{city}}",c.city or"").replace("{{state}}",c.state or"")
        ch,sg=count_sms_segments(body);ik=f"send-{contact_id}-{uuid.uuid4().hex[:12]}"
        cr=(await self.db.execute(select(Conversation).where(Conversation.contact_id==contact_id))).scalar_one_or_none()
        if not cr:cr=Conversation(contact_id=contact_id,campaign_id=campaign_id,status="active");self.db.add(cr);await self.db.flush()
        msg=Message(conversation_id=cr.id,contact_id=contact_id,campaign_id=campaign_id,direction="outgoing",body=body,segment_count=sg,char_count=ch,status="sending",provider="smsgate",idempotency_key=ik)
        self.db.add(msg);await self.db.flush()
        from app.providers.smsgate import send_sms_direct
        r=await send_sms_direct(c.phone_number,body,SMSService._get_sim())
        if r["success"]:msg.status="sent";msg.provider_message_id=r.get("provider_message_id","");msg.sent_at=datetime.now(timezone.utc)
        else:msg.status="failed";msg.last_error=r.get("error","");msg.failed_at=datetime.now(timezone.utc)
        msg.provider_response=json.dumps(r.get("raw"))if r.get("raw")else None
        c.messages_sent=(c.messages_sent or 0)+1;c.last_contacted_at=datetime.now(timezone.utc)
        cr.message_count=(cr.message_count or 0)+1;cr.last_message_preview=body[:100];cr.last_message_at=datetime.now(timezone.utc)
        await self.db.flush();return msg

    async def process_inbound_message(self,from_number,body,webhook_data=None):
        n=normalize_nigerian_number(from_number)
        if not n:logger.warning(f"INBOUND:bad number {from_number}");return None
        ik=f"inbound-{webhook_data['messageId']}"if webhook_data and webhook_data.get("messageId")else f"inbound-{n}-{uuid.uuid4().hex[:12]}"
        if(await self.db.execute(select(Message).where(Message.idempotency_key==ik))).scalar_one_or_none():return None
        c=(await self.db.execute(select(Contact).where(Contact.phone_number==n))).scalar_one_or_none()
        if not c:c=Contact(phone_number=n,country="Nigeria",lead_status="new",source="inbound_sms");self.db.add(c);await self.db.flush()
        kw=detect_opt_out_keyword(body)
        if kw:c.is_opted_out=True;c.opted_out_at=datetime.now(timezone.utc);c.opt_out_reason=f"Keyword:{kw}";self.db.add(SuppressionEntry(phone_number=n,contact_id=c.id,reason=f"Opt-out:{kw}",source="keyword",opt_out_keyword=kw));await self._stop_seq(c.id)
        cr=(await self.db.execute(select(Conversation).where(Conversation.contact_id==c.id))).scalar_one_or_none()
        if not cr:cr=Conversation(contact_id=c.id,status="unread");self.db.add(cr);await self.db.flush()
        else:cr.status="unread";cr.unread_count=(cr.unread_count or 0)+1
        ch,sg=count_sms_segments(body)
        m=Message(conversation_id=cr.id,contact_id=c.id,direction="incoming",body=body,segment_count=sg,char_count=ch,status="delivered",provider="smsgate",idempotency_key=ik)
        self.db.add(m);await self.db.flush()
        cr.message_count=(cr.message_count or 0)+1;cr.last_message_preview=body[:100];cr.last_message_at=datetime.now(timezone.utc)
        c.messages_received=(c.messages_received or 0)+1;c.last_reply_at=datetime.now(timezone.utc)
        if c.lead_status=="new":c.lead_status="replied"
        if not kw:await self._stop_seq(c.id)
        cr.sequence_paused=True;await self.db.flush()
        await self._notify_inbound(c,body[:160])
        return m

    async def _notify_inbound(self,contact,body):
        try:
            r=await self.db.execute(select(NotificationProvider).where(NotificationProvider.provider=="pushover",NotificationProvider.is_enabled==True).limit(1))
            prov=r.scalar_one_or_none()
            if not prov:return
            cfg=json.loads(prov.config_json or"{}");uk=decrypt_value(cfg.get("user_key_encrypted",""));at=decrypt_value(cfg.get("app_token_encrypted",""))
            if not uk or not at:return
            from app.providers.pushover import PushoverProvider
            ok=await PushoverProvider(app_token=at,user_key=uk).send_notification(f"📱 New SMS from {contact.business_name or contact.first_name or contact.phone_number}",body)
            logger.info(f"NOTIFY: Pushover {'OK' if ok else 'FAIL'}")
        except Exception as e:logger.error(f"NOTIFY err:{e}")

    async def check_gateway_health(self):
        from app.providers.smsgate import test_connection_direct
        r=await test_connection_direct()
        return{"is_healthy":r["success"]and r.get("online",False),"status":"healthy"if r["success"]and r.get("online")else"unhealthy","error":r.get("error")}

    async def _stop_seq(self,contact_id):
        await self.db.execute(update(FollowUp).where(FollowUp.contact_id==contact_id,FollowUp.status.in_(["pending","sending"])).values(status="cancelled"))
        await self.db.execute(update(CampaignContact).where(CampaignContact.contact_id==contact_id,CampaignContact.status.in_(["pending","queued","sent"])).values(status="replied",next_action_at=None))

    async def process_delivery_status(self,pid,status,delivered_at=None):
        m=(await self.db.execute(select(Message).where(Message.provider_message_id==pid))).scalar_one_or_none()
        if m:m.status=status;m.delivered_at=delivered_at or(datetime.now(timezone.utc)if status=="delivered"else None)
        await self.db.flush()
