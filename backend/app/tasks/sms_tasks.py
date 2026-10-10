"""Celery SMS tasks — uses send_sms_direct."""
import asyncio,json,logging,os
from datetime import datetime, timezone
from sqlalchemy import select, update
from app.tasks.celery_app import celery_app
from app.database import async_session_factory
from app.models.conversation import Message
from app.config import settings

logger=logging.getLogger(__name__)

class MessageNotVisible(RuntimeError):
    """The message row could not be found yet (producer commit not visible)."""

# How long the no-worker (inline) path may sleep waiting for a rate-limit
# window before it gives up and leaves the message queued for the next sweep.
_INLINE_RATE_WAIT_CAP = 60


async def _send_one(mid, final_on_failure=False, rate_wait_cap=_INLINE_RATE_WAIT_CAP):
    """Send one message.

    Returns:
      * ``False``          — settled (sent or failed), nothing more to do.
      * ``True``           — transient gateway error, retry shortly (~60s).
      * ``int``            — rate-limited; the number is how many seconds to
                             wait before trying again. The message is left
                             ``queued`` so the next retry / inline sweep sends it.

    ``final_on_failure`` is used by the API's no-worker fallback. There is no
    Celery retry context in that path, so a failed direct attempt must settle
    visibly instead of becoming a permanent ``retrying`` row.
    """
    async with async_session_factory() as db:
        m=(await db.execute(select(Message).where(Message.id==mid))).scalar_one_or_none()
        if m is None:
            # Do NOT treat this as "nothing to do". A publisher that enqueues
            # before committing loses this race, and swallowing it means the
            # contact is never texted at all. Retry -- by then the row exists.
            raise MessageNotVisible(f"Message {mid} not found yet")
        if m.status in("sent","delivered"):return False

        # Atomic claim: exactly one worker (Celery task, inline sweep, or a
        # retry) may attempt to send this row. A stuck "sending" row from a
        # crashed attempt is re-claimable because it stays in the IN list.
        claim = await db.execute(
            update(Message)
            .where(Message.id == mid, Message.status.in_(("queued", "retrying", "sending")))
            .values(status="sending")
        )
        if claim.rowcount != 1:
            return False
        m.status = "sending"
        await db.commit()

        is_email = (m.channel or "sms") == "email"

        # A campaign that was paused or stopped after this message was created
        # must not send it. (A breaker trip that kept sending the 25 messages
        # already in flight would be a poor breaker.)
        hold = await _campaign_hold(db, m)
        if hold == "stopped":
            m.status = "cancelled"
            m.last_error = "Campaign stopped"
            await db.commit()
            return False
        if hold == "paused":
            # Left queued, not failed: resume re-publishes it, and the inline
            # sweeper skips mail whose campaign is paused.
            m.status = "queued"
            m.last_error = "Campaign is paused"
            await db.commit()
            return False

        # Enforce sending limits + pacing before touching the gateway. This used to
        # be skipped for email ("no SIM to protect"), which meant an email campaign
        # could send its whole audience in minutes from one brand-new mailbox. A
        # mailbox has a reputation to protect just as a SIM does, so outreach email
        # goes through the same gate -- scoped to email and to the mailbox about to
        # send. A one-to-one reply to someone who wrote to us is not outreach and is
        # never held by the window or the cap.
        from app.services.sending_limits import SendingGate
        if is_email:
            outreach = bool(m.bulk_send or m.campaign_id or m.ads_campaign_id)
            account_id = m.email_account_id
            if account_id is None and outreach:
                from app.services import email_service
                default = await email_service.get_default_account(db)
                account_id = default.id if default else None
            gate = SendingGate(db, "email", account_id=account_id)
            check = await gate.check() if outreach else {"allowed": True}
        else:
            gate = SendingGate(db, "sms")
            check = await gate.check()
        if not check["allowed"]:
            wait = int(check["wait_seconds"] or 60)
            if final_on_failure and 0 < wait <= rate_wait_cap:
                await asyncio.sleep(wait)
                check = await gate.check()
            if not check["allowed"]:
                m.status = "queued"
                m.last_error = f"Rate limited: {check['reason']}"
                await db.commit()
                return int(check["wait_seconds"] or 60)

        from app.models.contact import Contact
        c=(await db.execute(select(Contact).where(Contact.id==m.contact_id))).scalar_one_or_none()
        if not c:m.status="failed";m.last_error="Contact not found";await db.commit();return False
        if is_email:
            # Everything below this point is SMS-specific (number validation,
            # SIM selection, gateway failover). The email twin does the same
            # job against Brevo, including account failover.
            return await _send_one_email(db, m, c, final_on_failure=final_on_failure)
        # Last-chance pre-send filter: never bill the SIM for a number that
        # cannot receive. (Queued rows may predate the contact going bad.)
        from app.services.list_hygiene import contact_is_blocked_from_send, mark_undeliverable
        blocked=contact_is_blocked_from_send(c)
        if blocked:
            m.status="failed";m.last_error=f"Filtered before send: {blocked}"
            m.failed_at=datetime.now(timezone.utc)
            if not c.is_undeliverable and blocked not in ("opted_out",):
                await mark_undeliverable(c, blocked)
            await _record_campaign_outcome(db,m,False)
            await db.commit()
            return False
        from app.providers.smsgate import send_sms_direct
        from app.services.system_settings import get_sim_number
        sim=await get_sim_number(db)
        r=await send_sms_direct(c.phone_number,m.body,sim)
        if not r["success"]:
            # SIM failover: a dead/no-credit SIM fails fast — retry once on
            # the other slot before giving up. Only one attempt can bill.
            other=2 if sim==1 else 1
            r2=await send_sms_direct(c.phone_number,m.body,other)
            if r2["success"]:
                r=r2
            else:
                r={"success":False,
                   "error":f"SIM{sim}: {r.get('error','')} | SIM{other}: {r2.get('error','')}"[:500],
                   "raw":r2.get("raw") or r.get("raw")}
        if r["success"]:m.status="sent";m.provider_message_id=r.get("provider_message_id","");m.sent_at=datetime.now(timezone.utc)
        else:
            m.retry_count=(m.retry_count or 0)+1
            m.status="failed"if final_on_failure or m.retry_count>=3 else"retrying"
            if m.status=="failed":m.failed_at=datetime.now(timezone.utc)
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


async def _campaign_hold(db, m):
    """"paused" / "stopped" when the message's campaign is no longer sending, else None."""
    if m.campaign_id:
        from app.models.campaign import Campaign
        status = (await db.execute(
            select(Campaign.status).where(Campaign.id == m.campaign_id)
        )).scalar_one_or_none()
        if status in ("paused", "stopped"):
            return status
    if getattr(m, "ads_campaign_id", None):
        from app.models.ads import AdsCampaign
        status = (await db.execute(
            select(AdsCampaign.status).where(AdsCampaign.id == m.ads_campaign_id)
        )).scalar_one_or_none()
        if status == "paused":
            return "paused"
    return None


async def _send_one_email(db, m, contact, *, final_on_failure=False):
    """Deliver one claimed email ``Message`` through Brevo.

    Mirrors the SMS branch's contract exactly (``False`` = settled, ``True`` =
    retry shortly) so Celery retries, the inline sweep and the retry endpoints
    keep working unchanged. Account fallback, suppression checks, per-account
    daily ceilings and contact counters all live in ``email_service``.
    """
    from app.services import email_service

    # A campaign message (classic or Ads Manager) is outreach: it is refused an address
    # nobody has verified even if it was queued before the address was edited.
    outreach = bool(m.campaign_id or m.ads_campaign_id or getattr(m, "bulk_send", False))
    problem = await email_service.contact_email_problem(db, contact, outreach=outreach)
    if problem:
        m.status = "failed"
        m.last_error = f"Filtered before send: {problem}"
        m.failed_at = datetime.now(timezone.utc)
        await _record_campaign_outcome(db, m, False)
        await db.commit()
        return False

    result = await email_service.deliver(db, m)
    if result.get("deferred"):
        # Today's allowance for the mailbox is used up. Hold the message (it is not
        # a failure, and must not use up its retries); try again in an hour.
        m.status = "queued"
        m.last_error = str(result.get("error") or "daily limit reached")[:500]
        await db.commit()
        return 3600
    if result.get("success"):
        contact.emails_sent = (contact.emails_sent or 0) + 1
        contact.last_emailed_at = datetime.now(timezone.utc)
        await _record_campaign_outcome(db, m, True)
    else:
        m.retry_count = (m.retry_count or 0) + 1
        m.status = "failed" if final_on_failure or m.retry_count >= 3 else "retrying"
        if m.status == "failed":
            m.failed_at = datetime.now(timezone.utc)
        m.last_error = str(result.get("error") or "send failed")[:500]
        # A hard rejection (bad key / banned sender / blocked recipient) will
        # not fix itself; only a transient (429 / timeout) is worth retrying.
        if result.get("status_code") in (400, 401, 402, 403):
            m.status = "failed"
            m.failed_at = m.failed_at or datetime.now(timezone.utc)
            contact.email_fail_count = (contact.email_fail_count or 0) + 1
            contact.email_last_error = m.last_error
        await _record_campaign_outcome(db, m, False)
        if m.status == "failed":
            # A provider that refuses our mail is exactly what the circuit breaker
            # watches for; a refusal never produces a bounce webhook of its own.
            from app.services import circuit_breaker
            await circuit_breaker.check_after_event(
                db, campaign_id=m.campaign_id, ads_campaign_id=m.ads_campaign_id
            )
    await db.commit()
    return m.status == "retrying"


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
        if ct:
            # The two channels keep separate counters so "how many SMS has this
            # lead had?" is not inflated by email (and vice versa).
            if (m.channel or "sms") == "email":
                ct.emails_sent=(ct.emails_sent or 0)+1
            else:
                ct.messages_sent=(ct.messages_sent or 0)+1
    elif m.status=="failed":
        # Only settle as failed once retries are exhausted, otherwise a
        # transient blip would permanently mark the contact undeliverable.
        if camp:camp.messages_failed=(camp.messages_failed or 0)+1
        if cc and cc.status in("queued","sent"):
            cc.status="failed"
            cc.next_action_at=None
            # Do not advance to a follow-up when the preceding sequence SMS
            # never reached the gateway.
            from app.models.followup import FollowUp
            pending=(await db.execute(select(FollowUp).where(
                FollowUp.campaign_contact_id==cc.id,
                FollowUp.status=="pending",
            ))).scalars().all()
            for followup in pending:
                followup.status="cancelled"
                followup.last_error="Previous sequence SMS failed"

@celery_app.task(bind=True,max_retries=3,default_retry_delay=60)
def send_sms(self,mid):
    try:result=_run(_send_one(mid))
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
    if result is True:
        from celery.exceptions import MaxRetriesExceededError
        try:raise self.retry(countdown=60)
        except MaxRetriesExceededError:pass
    elif isinstance(result,int):
        # Rate limited. Re-schedule rather than burn a Celery retry: a 10/hour
        # limit can wait much longer than the transient-failure retry budget.
        seconds=min(max(int(result),5),3600)
        from datetime import timedelta as _td
        from app.tasks.queue import enqueue_at
        try:
            enqueue_at(send_sms,datetime.now(timezone.utc)+_td(seconds=seconds),mid)
        except Exception:
            from celery.exceptions import MaxRetriesExceededError
            try:raise self.retry(countdown=seconds,max_retries=20)
            except MaxRetriesExceededError:pass

@celery_app.task
def sync_delivery_status():
    async def s():
        async with async_session_factory() as db:
            ms=(await db.execute(select(Message).where(Message.status.in_(["sent","queued"]),Message.provider_message_id.isnot(None),Message.channel!="email").limit(100))).scalars().all()
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

@celery_app.task
def process_scheduled_messages():
    """Celery wrapper for due scheduled messages (also run inline via _poll)."""
    async def _do():
        # Reuse the same logic as inline poller: import and delegate
        from app.main import _process_scheduled
        await _process_scheduled()
    _run(_do())

def _run(coro):
    loop=asyncio.get_event_loop()
    if loop.is_closed():loop=asyncio.new_event_loop();asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)
