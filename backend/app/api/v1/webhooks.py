"""Webhook handler — receives SMS-Gate.app events. Parses nested payload format."""
import json, logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.database import get_db
from app.models.contact import Contact
from app.models.webhook import WebhookEvent
from app.models.user import User
from app.security.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

# Events that put a message INTO the chat, per the SMS-Gate webhook reference.
# https://docs.sms-gate.app/features/webhooks/
INBOUND_EVENTS = ("sms:received", "sms:data-received", "mms:received", "mms:downloaded")

# Events that update the state of a message we sent.
STATUS_EVENTS = {
    "sms:sent": "sent",
    "sms:delivered": "delivered",
    "sms:failed": "failed",
    "sms:cancelled": "cancelled",
}


def _inbound_text(event_type: str, payload: dict) -> str:
    """Extract displayable text for each inbound event shape.

    `sms:received` carries `message`; `mms:downloaded` carries `body` plus
    `subject`; `mms:received` has no body yet (it fires before download); and
    `sms:data-received` carries base64 `data` that is not human text.
    """
    if event_type == "sms:received":
        return str(payload.get("message") or payload.get("text") or "")

    if event_type == "mms:downloaded":
        parts = []
        if payload.get("subject"):
            parts.append(f"[{payload['subject']}]")
        if payload.get("body"):
            parts.append(str(payload["body"]))
        for att in payload.get("attachments") or []:
            if isinstance(att, dict):
                parts.append(f"[attachment: {att.get('name') or att.get('contentType') or 'file'}]")
        return " ".join(parts).strip()

    if event_type == "mms:received":
        subject = payload.get("subject") or ""
        return f"[MMS received{': ' + subject if subject else ''} — downloading]"

    if event_type == "sms:data-received":
        return "[data message]"

    return str(payload.get("message") or payload.get("text") or payload.get("body") or "")


def _verify_signature(request: Request, raw_body: bytes) -> None:
    """Reject webhook deliveries that are not signed by our SMS gateway.

    Without this any anonymous caller can inject fake inbound messages,
    poison lead statuses and trigger push notifications.
    """
    secret = settings.SMSGATE_WEBHOOK_SECRET

    if not secret:
        if settings.SMSGATE_WEBHOOK_ALLOW_UNSIGNED and not settings.is_production:
            logger.warning(
                "Webhook signature check SKIPPED (SMSGATE_WEBHOOK_ALLOW_UNSIGNED=1). "
                "Never do this in production."
            )
            return
        logger.error(
            "Webhook rejected: SMSGATE_WEBHOOK_SECRET is not configured. "
            "Set it to the signing key from SMS-Gate Settings -> Webhooks."
        )
        raise HTTPException(status_code=503, detail="Webhook signing not configured")

    from app.providers.smsgate import SMSGateProvider

    signature = request.headers.get("x-signature", "")
    timestamp = request.headers.get("x-timestamp", "")

    if not SMSGateProvider.validate_webhook_signature(
        raw_body, signature, timestamp, secret
    ):
        logger.warning(
            "Webhook rejected: invalid signature from %s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

async def _parse(req: Request) -> dict:
    ct = (req.headers.get("content-type","")or"").lower()
    body = {}
    try:
        if "json" in ct: body = await req.json()
        else:
            raw = await req.body()
            try: body = json.loads(raw)
            except Exception: 
                from urllib.parse import parse_qs
                try: body = {k:v[0]if isinstance(v,list)and len(v)==1 else v for k,v in parse_qs(raw.decode("utf-8",errors="replace")).items()}
                except Exception: pass
    except Exception: pass
    if not body: body = dict(req.query_params)
    return body

@router.post("/smsgateway")
async def smsgateway_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Receive SMS events from SMS-Gate.app.
    
    SMS-Gate.app sends webhooks in this format:
    {
      "deviceId": "...",
      "event": "sms:received",
      "id": "webhook-event-id",
      "payload": {
        "messageId": "abc123",
        "message": "Hello!",        <-- SMS content
        "sender": "+1234567890",    <-- who sent it
        "recipient": "+0987654321", <-- device's number
        "simNumber": 1,
        "receivedAt": "2024-06-22T15:46:11.000+07:00"
      },
      "webhookId": "..."
    }
    
    For sms:sent/delivered/failed events:
    {
      "event": "sms:delivered",
      "payload": {
        "messageId": "msg-789",
        "sender": "+123...",     <-- device's number
        "recipient": "+999...",  <-- who received it
        "deliveredAt": "..."
      }
    }
    """
    # Read the raw bytes first: the signature is computed over the body
    # exactly as sent, before any JSON parsing. Starlette caches this so
    # the later _parse() call still works.
    raw_body = await request.body()
    _verify_signature(request, raw_body)

    body = await _parse(request)
    if not body:
        return {"ok": True}

    event_type = body.get("event", "") or ""
    # Real payloads nest the data; keep the flat fallback for hand-rolled tests.
    payload = body.get("payload", body)
    if not isinstance(payload, dict):
        payload = body

    # `id` is the unique per-delivery event id; `messageId` is content-derived and
    # repeats. Deduplicate on the event id so a resent SMS is not swallowed.
    event_id = body.get("id") or ""
    msg_id = payload.get("messageId") or ""
    idem_key = f"wh-{event_id or msg_id or datetime.now(timezone.utc).timestamp()}"[:255]

    logger.info(
        "WEBHOOK: event=%s eventId=%s msgId=%s keys=%s",
        event_type, event_id, msg_id, sorted(payload.keys()),
    )

    if (await db.execute(
        select(WebhookEvent).where(WebhookEvent.idempotency_key == idem_key)
    )).scalar_one_or_none():
        logger.info("WEBHOOK: duplicate delivery %s ignored", idem_key)
        return {"ok": True, "duplicate": True}

    evt = WebhookEvent(
        event_type=event_type or "unknown",
        provider="smsgate",
        provider_event_id=msg_id or event_id,
        idempotency_key=idem_key,
        payload=json.dumps(body)[:100000],
        status="received",
    )
    db.add(evt)
    await db.flush()

    from app.services.sms_service import SMSService
    svc = SMSService(db)
    result = {"ok": True}

    try:
        if event_type in INBOUND_EVENTS:
            frm = payload.get("sender") or payload.get("from") or payload.get("phoneNumber") or ""
            txt = _inbound_text(event_type, payload)

            if frm and txt.strip():
                msg = await svc.process_inbound_message(frm, txt, {
                    "messageId": msg_id,
                    "eventId": event_id,
                    "deviceId": body.get("deviceId", ""),
                    "simNumber": payload.get("simNumber"),
                    "recipient": payload.get("recipient", ""),
                    "receivedAt": payload.get("receivedAt", ""),
                })
                await db.flush()
                result["stored"] = bool(msg)
                logger.info("WEBHOOK INBOUND: from=%s stored=%s text=%r",
                            frm, bool(msg), txt[:60])
            else:
                evt.error = f"missing sender/text (sender={frm!r})"
                logger.warning(
                    "WEBHOOK INBOUND: nothing to store. sender=%r text=%r keys=%s",
                    frm, txt, sorted(payload.keys()),
                )

        elif event_type in STATUS_EVENTS:
            st = STATUS_EVENTS[event_type]
            ts = svc._parse_ts(
                payload.get("deliveredAt") or payload.get("sentAt")
                or payload.get("failedAt") or payload.get("cancelledAt")
            )
            m = await svc.process_delivery_status(msg_id, st, ts)
            if m is not None and st == "failed" and payload.get("reason"):
                m.last_error = str(payload["reason"])[:500]
            await db.flush()
            result["matched"] = m is not None
            logger.info("WEBHOOK STATUS: %s -> %s (matched=%s)", msg_id, st, m is not None)

        elif event_type == "system:ping":
            logger.info("WEBHOOK PING: device alive %s", body.get("deviceId", ""))

        elif event_type == "app:started":
            logger.info("WEBHOOK APP STARTED: sims=%s", payload.get("simCards"))

        else:
            # Unknown/next-version event: try a generic inbound parse rather than
            # dropping a real message on the floor.
            frm = payload.get("sender") or payload.get("from") or ""
            txt = payload.get("message") or payload.get("text") or payload.get("body") or ""
            if frm and str(txt).strip():
                await svc.process_inbound_message(frm, str(txt), {
                    "messageId": msg_id, "receivedAt": payload.get("receivedAt", ""),
                })
                await db.flush()
                logger.info("WEBHOOK GENERIC(%s): stored from %s", event_type, frm)
            else:
                logger.info("WEBHOOK: ignoring unhandled event %r", event_type)

        evt.status = "processed"
        evt.processed_at = datetime.now(timezone.utc)
    except Exception as e:
        # Never 500 back at the device: it would retry this delivery for ~2 days.
        evt.status = "error"
        evt.error = str(e)[:1000]
        evt.processed_at = datetime.now(timezone.utc)
        logger.exception("WEBHOOK: failed to process %s", event_type)
        result["ok"] = True
        result["error"] = "processing failed, logged"

    await db.flush()
    return result

@router.get("/smsgateway")
async def webhook_get(): return {"ok": True}


# --------------------------------------------------------------------------
# CallGate (phone calls) — same envelope + HMAC signing as SMS-Gate.
# --------------------------------------------------------------------------

CALL_EVENTS = ("call:ringing", "call:started", "call:ended")


def _verify_call_signature(request, raw_body: bytes, secret: str) -> None:
    if not secret:
        if settings.SMSGATE_WEBHOOK_ALLOW_UNSIGNED and not settings.is_production:
            logger.warning("CallGate webhook signature check SKIPPED (unsigned allowed).")
            return
        logger.error("CallGate webhook rejected: signing secret not configured.")
        raise HTTPException(status_code=503, detail="Webhook signing not configured")
    from app.providers.smsgate import SMSGateProvider

    signature = request.headers.get("x-signature", "")
    timestamp = request.headers.get("x-timestamp", "")
    if not SMSGateProvider.validate_webhook_signature(raw_body, signature, timestamp, secret):
        logger.warning("CallGate webhook rejected: invalid signature")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


@router.post("/callgate")
async def callgate_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Receive call events from the CallGate Android app.

    Envelope (same as SMS-Gate):
    {
      "deviceId": "...", "event": "call:started", "id": "unique-event-id",
      "webhookId": "...", "payload": {"phoneNumber": "6505551212"}
    }
    """
    from app.models.call import CallLog
    from app.services.system_settings import get_callgate_secret

    raw_body = await request.body()
    _verify_call_signature(request, raw_body, await get_callgate_secret(db))

    body = await _parse(request)
    if not body:
        return {"ok": True}

    event_type = body.get("event", "") or ""
    payload = body.get("payload", body)
    if not isinstance(payload, dict):
        payload = body
    event_id = body.get("id") or ""
    phone = str(payload.get("phoneNumber") or payload.get("phone") or "").strip()
    idem_key = f"call-{event_id or (event_type + phone)}"[:255]

    logger.info("CALL WEBHOOK: event=%s eventId=%s phone=%s", event_type, event_id, phone)

    if (await db.execute(
        select(WebhookEvent).where(WebhookEvent.idempotency_key == idem_key)
    )).scalar_one_or_none():
        return {"ok": True, "duplicate": True}

    evt = WebhookEvent(
        event_type=event_type or "unknown", provider="callgate",
        provider_event_id=event_id or None, idempotency_key=idem_key,
        payload=json.dumps(body)[:100000], status="received",
    )
    db.add(evt)
    await db.flush()

    try:
        if event_type in CALL_EVENTS and phone:
            from app.utils.phone import normalize_nigerian_number as _norm
            norm = _norm(phone) or phone
            # Find the contact (exact, then fuzzy on last 9 digits).
            contact = (await db.execute(
                select(Contact).where(Contact.phone_number == norm))).scalar_one_or_none()
            if contact is None:
                digits = "".join(ch for ch in norm if ch.isdigit())[-9:]
                if digits:
                    contact = (await db.execute(
                        select(Contact).where(Contact.phone_number.ilike(f"%{digits}")))).scalars().first()

            if event_type == "call:ringing":
                # Incoming ring with no open outgoing call = inbound call.
                open_out = (await db.execute(
                    select(CallLog).where(
                        CallLog.direction == "outgoing",
                        CallLog.status.in_(("initiated", "ringing", "started")))
                    .order_by(CallLog.id.desc()).limit(1))).scalar_one_or_none()
                if open_out is not None:
                    open_out.status = "ringing"
                    open_out.provider_event_id = event_id or open_out.provider_event_id
                    open_out.device_id = body.get("deviceId") or open_out.device_id
                else:
                    db.add(CallLog(contact_id=contact.id if contact else None,
                                   phone_number=norm, direction="incoming",
                                   status="ringing", provider_event_id=event_id or None,
                                   device_id=body.get("deviceId")))
            elif event_type == "call:started":
                row = (await db.execute(
                    select(CallLog).where(CallLog.status.in_(("initiated", "ringing")))
                    .order_by(CallLog.id.desc()).limit(1))).scalar_one_or_none()
                if row is not None:
                    row.status = "started"
                    row.started_at = datetime.now(timezone.utc)
                    row.provider_event_id = event_id or row.provider_event_id
                    if contact is not None and row.contact_id is None:
                        row.contact_id = contact.id
                else:
                    db.add(CallLog(contact_id=contact.id if contact else None,
                                   phone_number=norm, direction="incoming",
                                   status="started", provider_event_id=event_id or None,
                                   device_id=body.get("deviceId"),
                                   started_at=datetime.now(timezone.utc)))
            elif event_type == "call:ended":
                row = (await db.execute(
                    select(CallLog).where(CallLog.status.in_(("initiated", "ringing", "started")))
                    .order_by(CallLog.id.desc()).limit(1))).scalar_one_or_none()
                now = datetime.now(timezone.utc)
                if row is not None:
                    row.status = "ended"
                    row.ended_at = now
                    if row.started_at:
                        s = row.started_at
                        if s.tzinfo is None:
                            s = s.replace(tzinfo=timezone.utc)
                        row.duration_seconds = max(0, int((now - s).total_seconds()))
                    if contact is not None and row.contact_id is None:
                        row.contact_id = contact.id
                else:
                    db.add(CallLog(contact_id=contact.id if contact else None,
                                   phone_number=norm, direction="incoming",
                                   status="ended", provider_event_id=event_id or None,
                                   device_id=body.get("deviceId"), ended_at=now))
        elif event_type not in CALL_EVENTS:
            logger.info("CALL WEBHOOK: ignoring unhandled event %r", event_type)
        evt.status = "processed"
        evt.processed_at = datetime.now(timezone.utc)
    except Exception as e:
        evt.status = "error"
        evt.error = str(e)[:1000]
        evt.processed_at = datetime.now(timezone.utc)
        logger.exception("CALL WEBHOOK: failed to process %s", event_type)

    await db.flush()
    return {"ok": True}


@router.get("/callgate")
async def callgate_webhook_get(): return {"ok": True}

# Legacy compat
@router.post("/smsgate/inbound")
async def legacy(request: Request, db: AsyncSession = Depends(get_db)):
    return await smsgateway_webhook(request, db)

@router.get("/logs")
async def logs(page: int=1, per_page: int=25, event_type: str=None,
               db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    q = select(WebhookEvent)
    if event_type: q = q.where(WebhookEvent.event_type == event_type)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar()or 0
    q = q.order_by(WebhookEvent.created_at.desc()).offset((page-1)*per_page).limit(per_page)
    evts = (await db.execute(q)).scalars().all()
    return {"total":total,"items":[{"id":e.id,"event_type":e.event_type,"provider":e.provider,
            "provider_event_id":e.provider_event_id,"status":e.status,"error":e.error,
            "created_at":e.created_at.isoformat()} for e in evts]}
