"""SMS Service — uses direct send_sms + Pushover notifications."""
import json,logging,uuid,os
from datetime import datetime, timezone
from sqlalchemy import select,update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.contact import Contact
from app.models.conversation import Conversation,Message
from app.models.suppression import SuppressionEntry
from app.models.campaign import Campaign,CampaignContact
from app.models.followup import FollowUp
from app.models.notification import NotificationProvider
from app.utils.phone import normalize_nigerian_number,normalize_inbound_sender,detect_opt_out_keyword,count_sms_segments
from app.utils.templating import render_template
from app.utils.naming import contact_display_name
from app.security.encryption import decrypt_value
from app.config import settings

logger = logging.getLogger(__name__)

# Strong refs for detached background tasks (asyncio only holds weak ones).
_BG_TASKS:set=set()

class SMSService:
    def __init__(self,db:AsyncSession):self.db=db

    async def _get_sim(self):
        from app.services.system_settings import get_sim_number
        return await get_sim_number(self.db)

    async def send_message(self,contact_id,body,campaign_id=None):
        c=(await self.db.execute(select(Contact).where(Contact.id==contact_id))).scalar_one_or_none()
        if not c or c.is_opted_out:return None
        if(await self.db.execute(select(SuppressionEntry).where(SuppressionEntry.phone_number==c.phone_number))).scalar_one_or_none():return None
        body=render_template(body,c)
        ch,sg=count_sms_segments(body);ik=f"send-{contact_id}-{uuid.uuid4().hex[:12]}"
        cr=(await self.db.execute(select(Conversation).where(Conversation.contact_id==contact_id).order_by(Conversation.id).limit(1))).scalars().first()
        if not cr:cr=Conversation(contact_id=contact_id,campaign_id=campaign_id,status="active");self.db.add(cr);await self.db.flush()
        from app.services.gateway_dispatch import get_active_gateway
        active=await get_active_gateway(self.db)
        msg=Message(conversation_id=cr.id,contact_id=contact_id,campaign_id=campaign_id,direction="outgoing",body=body,segment_count=sg,char_count=ch,status="sending",provider=active,idempotency_key=ik)
        self.db.add(msg);await self.db.flush()
        from app.services.gateway_dispatch import send_sms_dispatch
        provider_name,r=await send_sms_dispatch(self.db,c.phone_number,body,await self._get_sim())
        msg.provider=provider_name
        if r["success"]:msg.status="sent";msg.provider_message_id=r.get("provider_message_id","");msg.sent_at=datetime.now(timezone.utc)
        else:msg.status="failed";msg.last_error=r.get("error","");msg.failed_at=datetime.now(timezone.utc)
        msg.provider_response=json.dumps(r.get("raw"))if r.get("raw")else None
        c.messages_sent=(c.messages_sent or 0)+1;c.last_contacted_at=datetime.now(timezone.utc)
        cr.message_count=(cr.message_count or 0)+1;cr.last_message_preview=body[:100];cr.last_message_at=datetime.now(timezone.utc)
        await self.db.flush();return msg

    @staticmethod
    def _aware(d):
        """Coerce a DB datetime to UTC-aware.

        SQLite (and any column written before timezone support) hands back
        naive datetimes, and comparing one to an aware timestamp raises
        TypeError, which aborted the whole inbound message.
        """
        if d is None:
            return None
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)

    @staticmethod
    def _parse_ts(raw):
        """Parse an SMS-Gate ISO-8601 timestamp (e.g. 2024-06-22T15:46:11.000+07:00)."""
        if not raw:
            return None
        try:
            d=datetime.fromisoformat(str(raw).strip().replace("Z","+00:00"))
        except (TypeError,ValueError):
            return None
        if d.tzinfo is None:
            d=d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)

    async def process_inbound_message(self,from_number,body,webhook_data=None,provider="smsgate"):
        # Inbound senders are not always Nigerian mobiles: short codes, sender IDs
        # and international numbers must land in the inbox too, not be discarded.
        n=normalize_inbound_sender(from_number)
        if not n:logger.warning(f"INBOUND: unusable sender {from_number!r}; dropping");return None
        # SMS-Gate derives messageId from message CONTENT, so the same text from the
        # same sender repeats the id. Scope the key by sender+timestamp as well, or
        # a legitimate repeat reply ("YES", "STOP") is swallowed as a duplicate.
        wd=webhook_data or {}
        mid=wd.get("messageId") or ""
        stamp=str(wd.get("receivedAt") or "")
        if mid:
            ik=f"inbound-{n}-{mid}-{stamp}" if stamp else f"inbound-{n}-{mid}"
        else:
            ik=f"inbound-{n}-{uuid.uuid4().hex[:12]}"
        ik=ik[:255]
        if(await self.db.execute(select(Message).where(Message.idempotency_key==ik))).scalar_one_or_none():
            logger.info(f"INBOUND: duplicate {ik}; ignoring");return None
        c=(await self.db.execute(select(Contact).where(Contact.phone_number==n))).scalar_one_or_none()
        if not c:c=Contact(phone_number=n,country="Nigeria",lead_status="new",source="inbound_sms");self.db.add(c);await self.db.flush()
        kw=detect_opt_out_keyword(body)
        if kw:
            c.is_opted_out=True;c.opted_out_at=datetime.now(timezone.utc);c.opt_out_reason=f"Keyword:{kw}"
            # An explicit STOP is a consent revocation. Leaving consent_status
            # at "unknown" made opted-out contacts indistinguishable from
            # never-asked ones in compliance exports and audits.
            c.consent_status="opted_out";c.has_consented=False
            self.db.add(SuppressionEntry(phone_number=n,contact_id=c.id,reason=f"Opt-out:{kw}",source="keyword",opt_out_keyword=kw))
            await self._stop_seq(c.id)
        cr=(await self.db.execute(select(Conversation).where(Conversation.contact_id==c.id).order_by(Conversation.id).limit(1))).scalars().first()
        if not cr:
            # A brand-new conversation still has one unread message in it.
            cr=Conversation(contact_id=c.id,status="unread",unread_count=1);self.db.add(cr);await self.db.flush()
        else:cr.status="unread";cr.unread_count=(cr.unread_count or 0)+1
        ch,sg=count_sms_segments(body)
        # Preserve the real receive time. Inbox export replays historical SMS, and
        # stamping them all with now() shuffles the chat into the wrong order.
        received_at=self._parse_ts(stamp) or datetime.now(timezone.utc)
        m=Message(conversation_id=cr.id,contact_id=c.id,direction="incoming",body=body,segment_count=sg,char_count=ch,status="delivered",provider=provider,provider_message_id=mid or None,idempotency_key=ik,created_at=received_at)
        self.db.add(m);await self.db.flush()
        cr.message_count=(cr.message_count or 0)+1
        # Inbox export replays old SMS out of order; the preview and the sort
        # timestamp must both track the NEWEST message, not the last one to arrive.
        prev_at=self._aware(cr.last_message_at)
        if not prev_at or received_at>=prev_at:
            cr.last_message_at=received_at;cr.last_message_preview=body[:100]
        c.messages_received=(c.messages_received or 0)+1;c.last_reply_at=datetime.now(timezone.utc)
        if c.lead_status=="new":c.lead_status="replied"
        # Credit the reply to the campaign that last messaged this contact, so
        # the campaign reply rate stops reading 0. Count one reply per contact
        # per campaign: a chatty contact is still a single responder.
        last_out=(await self.db.execute(select(Message).where(Message.contact_id==c.id,Message.direction=="outgoing",Message.campaign_id.isnot(None)).order_by(Message.created_at.desc()).limit(1))).scalar_one_or_none()
        if last_out is not None and last_out.campaign_id:
            cc=(await self.db.execute(select(CampaignContact).where(CampaignContact.campaign_id==last_out.campaign_id,CampaignContact.contact_id==c.id))).scalar_one_or_none()
            # Dedup per campaign, not per contact: someone who replied to last
            # month's campaign is still a new responder for this one. The
            # CampaignContact row is the natural per-campaign marker.
            first_reply_to_campaign=cc is None or cc.last_reply_at is None
            if first_reply_to_campaign:
                camp=(await self.db.execute(select(Campaign).where(Campaign.id==last_out.campaign_id))).scalar_one_or_none()
                if camp:camp.replies=(camp.replies or 0)+1
            if cc is not None:
                cc.last_reply_at=received_at
                if cc.status in("pending","queued","sent","delivered"):cc.status="replied"
        if not kw:await self._stop_seq(c.id)
        cr.sequence_paused=True;await self.db.flush()
        await self._notify_inbound(c,body[:160])
        # Auto-reply last: it must never be able to stop the inbound message
        # from being stored, so it is best-effort and swallows its own errors.
        # Skipped entirely for opt-out keywords -- answering "STOP" with
        # marketing copy is exactly what compliance rules forbid.
        if not kw:
            try:
                await self._maybe_auto_reply(c,cr,body)
            except Exception as e:
                logger.error(f"AUTOREPLY err:{e}")
        return m

    async def _maybe_auto_reply(self,contact,conversation,body):
        """Answer an inbound message if the operator configured a rule for it.

        Returns the outgoing Message, or None when no rule applies. The reply
        is queued through the normal outbound path so it gets delivery
        receipts, segment counting and retries like any other message.
        """
        from app.services.autoreply_service import AutoReplyService

        rule,text=await AutoReplyService(self.db).build_reply(contact,body)
        if not rule or not text:
            return None

        ik=f"autoreply-{rule.id}-{contact.id}-{uuid.uuid4().hex[:12]}"[:255]
        ch,sg=count_sms_segments(text)
        from app.services.gateway_dispatch import get_active_gateway
        out=Message(
            conversation_id=conversation.id,contact_id=contact.id,direction="outgoing",
            body=text,segment_count=sg,char_count=ch,status="queued",provider=await get_active_gateway(self.db),
            idempotency_key=ik,is_auto_reply=True,
        )
        self.db.add(out)
        rule.times_triggered=(rule.times_triggered or 0)+1
        rule.last_triggered_at=datetime.now(timezone.utc)
        conversation.message_count=(conversation.message_count or 0)+1
        conversation.last_message_preview=text[:100]
        conversation.last_message_at=datetime.now(timezone.utc)
        await self.db.flush()

        # Commit before handing the id to the worker: the worker looks the
        # message up by id and would not see an uncommitted row.
        await self.db.commit()

        from app.tasks.queue import try_enqueue
        from app.tasks.sms_tasks import send_sms
        if try_enqueue(send_sms,out.id):
            logger.info(f"AUTOREPLY: rule '{rule.name}' -> contact {contact.id}")
        else:
            logger.error(f"AUTOREPLY: broker unavailable; reply {out.id} left queued")
        return out

    async def _notify_inbound(self,contact,body):
        """Fire a Pushover alert for an inbound SMS.

        This runs in the webhook request path, and SMS-Gate retries for ~2 days
        unless it gets a 2xx within 30 seconds. A slow or blackholed Pushover
        must therefore never hold up the response: we read the credentials
        inline (we need the DB session) but dispatch the HTTP call as a
        detached background task. The message is already committed by then, so
        losing a notification is survivable; losing the message is not.
        """
        try:
            # Carrier/service sender IDs (MTN, Airtel, banks, 2FA short codes)
            # only send automated alerts; pinging Pushover for each one is
            # noise. Muted senders are stored uppercased and editable from
            # Settings -> Pushover. The message still lands in the inbox.
            from app.services.system_settings import get_muted_notify_senders
            sender = (contact.phone_number or "").strip().upper()
            if sender and sender in await get_muted_notify_senders(self.db):
                logger.info(f"NOTIFY: muted sender {sender!r} — no Pushover alert")
                return
            r=await self.db.execute(select(NotificationProvider).where(NotificationProvider.provider=="pushover",NotificationProvider.is_enabled==True).limit(1))
            prov=r.scalar_one_or_none()
            if not prov:return
            cfg=json.loads(prov.config_json or"{}");uk=decrypt_value(cfg.get("user_key_encrypted",""));at=decrypt_value(cfg.get("app_token_encrypted",""))
            if not uk or not at:return
            title=f"📱 New SMS from {contact_display_name(contact, contact.phone_number)}"
            import asyncio
            async def _send():
                try:
                    from app.providers.pushover import PushoverProvider
                    ok=await PushoverProvider(app_token=at,user_key=uk).send_notification(title,body)
                    logger.info(f"NOTIFY: Pushover {'OK' if ok else 'FAIL'}")
                except Exception as e:logger.error(f"NOTIFY send err:{e}")
            task=asyncio.create_task(_send())
            # Hold a reference so the task is not garbage-collected mid-flight.
            _BG_TASKS.add(task);task.add_done_callback(_BG_TASKS.discard)
        except Exception as e:logger.error(f"NOTIFY err:{e}")

    async def check_gateway_health(self):
        from app.services.gateway_dispatch import gateway_health
        return await gateway_health(self.db)

    async def _stop_seq(self,contact_id):
        await self.db.execute(update(FollowUp).where(FollowUp.contact_id==contact_id,FollowUp.status.in_(["pending","sending"])).values(status="cancelled"))
        await self.db.execute(update(CampaignContact).where(CampaignContact.contact_id==contact_id,CampaignContact.status.in_(["pending","queued","sent"])).values(status="replied",next_action_at=None))

    async def process_delivery_status(self,pid,status,delivered_at=None):
        """Apply a delivery-status transition to the outgoing message `pid`.

        Guards against regressions: a late `sms:sent` webhook must not knock an
        already-delivered message back down, and multipart messages emit one
        `sms:delivered` per part.
        """
        if not pid:return None
        m=(await self.db.execute(select(Message).where(Message.provider_message_id==pid,Message.direction=="outgoing"))).scalar_one_or_none()
        if not m:
            logger.info(f"STATUS: no outgoing message for provider id {pid}")
            return None
        rank={"queued":0,"sending":1,"sent":2,"delivered":3,"failed":3,"cancelled":3}
        if rank.get(status,0)<rank.get(m.status,0):
            return m
        # Was this message already in the state we are about to record? If so
        # the campaign counter was incremented on the first webhook and must
        # not move again (SMS-Gate sends one sms:delivered per message part).
        counted_before=(m.status==status)
        m.status=status
        now=datetime.now(timezone.utc)
        # sms:delivered fires once per part of a multipart message; keep the
        # first timestamp rather than letting each part bump it to now().
        if status=="delivered":
            if not m.delivered_at:m.delivered_at=delivered_at or now
        elif status in("failed","cancelled"):
            if not m.failed_at:m.failed_at=delivered_at or now
        elif status=="sent":
            if not m.sent_at:m.sent_at=delivered_at or now
        # Roll the campaign's aggregate counters forward. These are what the
        # analytics endpoint and the dashboard report, and nothing else
        # maintained them, so every campaign showed a 0% delivery rate no
        # matter how many messages actually landed. Only count a transition
        # into the state (guarded by the rank check above plus the timestamp
        # guards) so per-part delivery webhooks cannot double-count.
        if m.campaign_id and not counted_before:
            camp=(await self.db.execute(select(Campaign).where(Campaign.id==m.campaign_id))).scalar_one_or_none()
            if camp:
                if status=="delivered":camp.messages_delivered=(camp.messages_delivered or 0)+1
                elif status in("failed","cancelled"):camp.messages_failed=(camp.messages_failed or 0)+1
        # Settle the per-contact row too. process_campaign treats
        # pending/queued/sent as "still in flight" and only completes a
        # campaign once that set is empty, so leaving the row on "sent" after
        # the final receipt arrived kept every campaign running forever.
        if m.campaign_id and status in("delivered","failed","cancelled"):
            cc=(await self.db.execute(select(CampaignContact).where(CampaignContact.campaign_id==m.campaign_id,CampaignContact.contact_id==m.contact_id))).scalar_one_or_none()
            if cc is not None and cc.status in("queued","sent"):
                cc.status="delivered"if status=="delivered"else"failed"
        await self.db.flush()
        return m
