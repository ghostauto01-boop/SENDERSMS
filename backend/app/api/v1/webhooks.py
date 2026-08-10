"""Webhook handler — receives SMS-Gate.app events. Parses nested payload format."""
import json, logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.webhook import WebhookEvent
from app.models.user import User
from app.security.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

async def _parse(req: Request) -> dict:
    ct = (req.headers.get("content-type","")or"").lower()
    body = {}
    try:
        if "json" in ct: body = await req.json()
        else:
            raw = await req.body()
            try: body = json.loads(raw)
            except: 
                from urllib.parse import parse_qs
                try: body = {k:v[0]if isinstance(v,list)and len(v)==1 else v for k,v in parse_qs(raw.decode("utf-8",errors="replace")).items()}
                except: pass
    except: pass
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
    body = await _parse(request)
    if not body: return {"ok": True}
    
    event_type = body.get("event", "")
    # The actual message data is nested under payload
    payload = body.get("payload", body)  # if no payload key, use body directly (backward compat)
    if not isinstance(payload, dict):
        payload = body
    
    msg_id = payload.get("messageId") or body.get("id") or ""
    idem_key = f"wh-{msg_id}" if msg_id else f"wh-{datetime.now(timezone.utc).timestamp()}"
    
    logger.info(f"WEBHOOK: event={event_type} msgId={msg_id} keys={sorted(payload.keys())}")
    
    # Duplicate check
    if (await db.execute(select(WebhookEvent).where(WebhookEvent.idempotency_key == idem_key))).scalar_one_or_none():
        return {"ok": True}
    
    evt = WebhookEvent(
        event_type=event_type or "inbound_sms",
        provider="smsgate",
        provider_event_id=msg_id,
        idempotency_key=idem_key,
        payload=json.dumps(body),
        status="received"
    )
    db.add(evt); await db.flush()
    
    # ── HANDLE BY EVENT TYPE ──
    
    if event_type in ("sms:received", "sms:data-received", "mms:received"):
        # INBOUND: extract sender and message from payload
        frm = payload.get("sender") or payload.get("from") or payload.get("phoneNumber") or ""
        txt = payload.get("message") or payload.get("text") or payload.get("body") or ""
        
        if frm and txt and txt.strip():
            from app.services.sms_service import SMSService
            svc = SMSService(db)
            await svc.process_inbound_message(frm, txt, {
                "messageId": msg_id,
                "deviceId": body.get("deviceId", ""),
                "simNumber": payload.get("simNumber"),
                "recipient": payload.get("recipient", ""),
                "receivedAt": payload.get("receivedAt", ""),
            })
            await db.flush()
            logger.info(f"WEBHOOK INBOUND: from={frm} text={txt[:50]}")
        else:
            logger.warning(f"WEBHOOK INBOUND: missing sender/text. sender={frm!r} text={txt!r} payload_keys={sorted(payload.keys())}")
    
    elif event_type in ("sms:delivered", "sms:failed", "sms:sent"):
        # OUTBOUND STATUS UPDATE
        from app.models.conversation import Message
        st_map = {
            "sms:delivered": "delivered",
            "sms:failed": "failed",
            "sms:sent": "sent",
        }
        st = st_map.get(event_type, "unknown")
        
        if msg_id:
            mr = await db.execute(select(Message).where(Message.provider_message_id == msg_id))
            m = mr.scalar_one_or_none()
            if m:
                m.status = st
                if st == "delivered":
                    m.delivered_at = datetime.now(timezone.utc)
                elif st == "failed":
                    m.failed_at = datetime.now(timezone.utc)
                await db.flush()
                logger.info(f"WEBHOOK STATUS: {msg_id} -> {st}")
    
    elif event_type == "system:ping":
        logger.info("WEBHOOK PING: device is alive")
    
    else:
        # Unknown event — try generic inbound parsing
        frm = payload.get("sender") or payload.get("from") or payload.get("phoneNumber") or ""
        txt = payload.get("message") or payload.get("text") or payload.get("body") or ""
        if frm and txt and txt.strip():
            from app.services.sms_service import SMSService
            svc = SMSService(db)
            await svc.process_inbound_message(frm, txt, {"messageId": msg_id})
            await db.flush()
            logger.info(f"WEBHOOK GENERIC: from={frm}")
    
    evt.status = "processed"; evt.processed_at = datetime.now(timezone.utc)
    await db.flush()
    return {"ok": True}

@router.get("/smsgateway")
async def webhook_get(): return {"ok": True}

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
