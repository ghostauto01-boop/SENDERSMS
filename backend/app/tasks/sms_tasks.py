"""Celery SMS tasks — uses send_sms_direct."""
import asyncio,json,logging,os
from datetime import datetime, timedelta, timezone
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
        if m.status in ("sent", "delivered"):
            return False
        now = datetime.now(timezone.utc)
        if m.retry_at is not None:
            due = m.retry_at if m.retry_at.tzinfo else m.retry_at.replace(tzinfo=timezone.utc)
            if due > now:
                return max(1, int((due - now).total_seconds()))

        # Atomic claim: exactly one worker (Celery task, inline sweep, or a
        # retry) may attempt to send this row. A stuck "sending" row from a
        # crashed attempt is re-claimable because it stays in the IN list.
        claim = await db.execute(
            update(Message)
            .where(Message.id == mid, Message.status.in_(("queued", "retrying", "sending")))
            .values(status="sending", retry_at=None)
        )
        if claim.rowcount != 1:
            return False
        m.status = "sending"
        m.retry_at = None
        await db.commit()

        is_email = (m.channel or "sms") == "email"

        # A campaign that was paused or stopped after this message was created
        # must not send it. (A breaker trip that kept sending the 25 messages
        # already in flight would be a poor breaker.)
        hold = await _campaign_hold(db, m)
        if hold == "stopped":
            m.status = "cancelled"
            m.retry_at = None
            m.last_error = "Campaign stopped; this message will not be sent."
            await db.commit()
            return False
        if hold == "paused":
            # Left queued, not failed: resume re-publishes it, and the inline
            # sweeper skips mail whose campaign is paused.
            m.status = "queued"
            m.retry_at = None
            m.last_error = "Campaign is paused; it will resume when the campaign is resumed."
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
                wait = max(int(check["wait_seconds"] or 60), 5)
                m.retry_at = datetime.now(timezone.utc) + timedelta(seconds=wait)
                m.last_error = f"Sending is paused by the configured limit: {check['reason']}. Retry is scheduled automatically."
                await db.commit()
                return wait

        from app.models.contact import Contact
        c=(await db.execute(select(Contact).where(Contact.id==m.contact_id))).scalar_one_or_none()
        if not c:
            m.status = "failed"
            m.last_error = "Message was not sent because its contact no longer exists."
            m.failed_at = datetime.now(timezone.utc)
            m.retry_at = None
            await db.commit()
            return False
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
        if r["success"]:
            m.status = "sent"
            m.provider_message_id = r.get("provider_message_id", "")
            m.sent_at = datetime.now(timezone.utc)
            m.retry_at = None
            m.last_error = None
        else:
            attempt_number = max(1, int(m.retry_count or 0) + 1)
            m.retry_count = attempt_number
            error = " ".join(str(r.get("error") or "SMS provider did not accept the message.").split())[:500]
            rate_limited = any(marker in error.lower() for marker in ("429", "rate limit", "too many requests"))
            if attempt_number < 5:
                delay = 60 if rate_limited else min(60 * (2 ** (attempt_number - 1)), 3600)
                m.status = "retrying"
                m.retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                m.last_error = (
                    "SMS provider rate limit reached. Automatic retry is scheduled in 60 seconds."
                    if rate_limited else
                    f"Temporary SMS delivery failure: {error} Automatic retry {attempt_number + 1} of 5 is scheduled in {delay} seconds."
                )
            else:
                m.status = "failed"
                m.retry_at = None
                m.failed_at = datetime.now(timezone.utc)
                m.last_error = f"SMS delivery failed after 5 attempts: {error}. Check SMS gateway health, then retry the message."
        m.provider_response = json.dumps(r.get("raw")) if r.get("raw") else None
        # Campaign counters follow provider acceptance, never queue insertion.
        await _record_campaign_outcome(db, m, bool(r["success"]))
        await db.commit()
        return max(1, int((m.retry_at - datetime.now(timezone.utc)).total_seconds())) if m.status == "retrying" and m.retry_at else False



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
    """Send an email with durable, actionable attempt history and bounded retries."""
    from app.services import email_service
    from app.services.email_send_logs import begin_email_attempt, finish_email_attempt, next_email_log_attempt

    attempt_number = max(1, int(m.retry_count or 0) + 1)
    log_attempt = await next_email_log_attempt(db, m)
    await begin_email_attempt(db, m, attempt=log_attempt)

    outreach = bool(m.campaign_id or m.ads_campaign_id)
    problem = await email_service.contact_email_problem(db, contact, outreach=outreach)
    if problem:
        m.status = "failed"
        m.last_error = f"Email not sent because this contact is {problem.replace('_', ' ')}. Review the contact's consent and address before trying again."
        m.failed_at = datetime.now(timezone.utc)
        m.retry_at = None
        await finish_email_attempt(
            db, m, status="failed", error_message=m.last_error, attempt=log_attempt,
        )
        await _record_campaign_outcome(db, m, False)
        await db.commit()
        return False

    try:
        result = await email_service.deliver(db, m)
    except Exception as exc:  # noqa: BLE001 - providers should not strand a claimed row
        logger.exception("Email delivery raised for message %s", m.id)
        result = {"success": False, "error": f"Temporary provider connection error ({type(exc).__name__})."}

    now = datetime.now(timezone.utc)
    if result.get("deferred"):
        # Mailbox allowance, not a provider rejection: retain the message and
        # wait for capacity without consuming an automatic retry attempt.
        m.status = "queued"
        m.retry_at = now + timedelta(hours=1)
        m.last_error = (str(result.get("error") or "Sender daily limit reached; retrying in one hour.")[:500])
        await finish_email_attempt(
            db, m, status="deferred", error_message=m.last_error,
            retry_at=m.retry_at, attempt=log_attempt,
        )
        await db.commit()
        return 3600

    if result.get("success"):
        if not m.campaign_id:
            contact.emails_sent = (contact.emails_sent or 0) + 1
        contact.last_emailed_at = now
        m.status = "sent"
        m.retry_at = None
        m.last_error = None
        await finish_email_attempt(
            db, m, status="sent", sent_at=m.sent_at or now, attempt=log_attempt,
        )
        await _record_campaign_outcome(db, m, True)
        await db.commit()
        return False

    raw_code = result.get("status_code")
    try:
        status_code = int(raw_code) if raw_code is not None else None
    except (TypeError, ValueError):
        status_code = None
    provider_error = " ".join(str(result.get("error") or "Email provider did not accept the message.").split())[:500]
    permanent = status_code in (400, 401, 402, 403, 404, 422) or provider_error.lower().startswith("no email could be sent:")
    rate_limited = status_code == 429
    m.retry_count = attempt_number
    if not permanent and attempt_number < 5:
        delay = 60 if rate_limited else min(60 * (2 ** (attempt_number - 1)), 3600)
        m.status = "retrying"
        m.retry_at = now + timedelta(seconds=delay)
        if rate_limited:
            user_error = "Brevo rate limit reached (HTTP 429). Automatic retry is scheduled in 60 seconds."
        else:
            user_error = (
                f"Temporary email delivery error: {provider_error} "
                f"Automatic retry {attempt_number + 1} of 5 is scheduled in {delay} seconds."
            )
    else:
        m.status = "failed"
        m.failed_at = now
        m.retry_at = None
        if status_code in (401, 403):
            user_error = "Brevo rejected the API key. Reconnect the sender or re-save its key in Email Manager."
        elif status_code == 402:
            user_error = "Brevo reports the account has no sending credit. Check the Brevo plan and balance."
        elif status_code == 400:
            user_error = f"Brevo rejected this message: {provider_error}. Check the sender address, domain authentication, and content."
        elif attempt_number >= 5:
            user_error = f"Email delivery failed after 5 attempts: {provider_error}. Check sender health, then retry the message."
        elif permanent:
            user_error = f"Email was rejected: {provider_error}. Check the sender and recipient details."
        else:
            user_error = f"Email delivery failed: {provider_error}. Check the sender account and retry the message."
    m.last_error = user_error[:2000]
    await finish_email_attempt(
        db, m,
        status="retrying" if m.status == "retrying" else "failed",
        error_code=status_code,
        error_message=m.last_error,
        retry_at=m.retry_at,
        attempt=log_attempt,
    )
    await _record_campaign_outcome(db, m, False)
    if m.status == "failed":
        contact.email_fail_count = (contact.email_fail_count or 0) + 1
        contact.email_last_error = m.last_error
        from app.services import circuit_breaker
        await circuit_breaker.check_after_event(
            db, campaign_id=m.campaign_id, ads_campaign_id=m.ads_campaign_id
        )
    await db.commit()
    if m.status == "retrying":
        return max(1, int((m.retry_at - now).total_seconds()))
    return False


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

@celery_app.task(bind=True,max_retries=5,default_retry_delay=60)
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
def process_due_message_retries():
    """Durable poller for provider retries when an ETA task was lost."""
    async def process():
        from sqlalchemy import or_
        now = datetime.now(timezone.utc)
        async with async_session_factory() as db:
            ids = list((await db.execute(
                select(Message.id).where(
                    Message.direction == "outgoing",
                    Message.status.in_(("queued", "retrying")),
                    or_(Message.retry_at.is_(None), Message.retry_at <= now),
                ).order_by(Message.retry_at, Message.created_at).limit(50)
            )).scalars().all())
        attempted = 0
        for message_id in ids:
            try:
                await _send_one(message_id, final_on_failure=False)
                attempted += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Due-message retry %s failed: %s", message_id, exc)
        return attempted
    return _run(process())


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
