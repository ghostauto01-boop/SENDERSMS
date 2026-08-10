"""Webhook handler — receives SMS-Gate.app events. EXACT BULKSMS pattern for inbox replies."""
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
    """Receive SMS events from SMS-Gate.app. Handles inbound SMS + delivery status."""
    body = await _parse(request)
    if not body: return {"ok": True}
    
    msg_id = body.get("id") or body.get("message_id") or body.get("messageId","")
    idem_key = f"wh-{msg_id}" if msg_id else f"wh-{datetime.now(timezone.utc).timestamp()}"
    
    # Duplicate check
    if (await db.execute(select(WebhookEvent).where(WebhookEvent.idempotency_key == idem_key))).scalar_one_or_none():
        return {"ok": True}
    
    evt = WebhookEvent(event_type="inbound_sms", provider="smsgate", provider_event_id=msg_id,
                       idempotency_key=idem_key, payload=json.dumps(body), status="received")
    db.add(evt); await db.flush()
    
    # ── DELIVERY STATUS UPDATE ──
    status_raw = str(body.get("status","")).lower()
    if msg_id and status_raw:
        from app.models.conversation import Message
        if "deliver" in status_raw: st = "delivered"
        elif "fail" in status_raw: st = "failed"
        elif "sent" in status_raw: st = "sent"
        else: st = "unknown"
        mr = await db.execute(select(Message).where(Message.provider_message_id == msg_id))
        m = mr.scalar_one_or_none()
        if m:
            m.status = st
            if st == "delivered": m.delivered_at = datetime.now(timezone.utc)
            elif st == "failed": m.failed_at = datetime.now(timezone.utc)
            await db.flush()
    
    # ── INBOUND SMS — SAME PATTERN AS SENDING ──
    frm = body.get("from") or body.get("sender") or body.get("number","")
    txt = body.get("text") or body.get("message") or body.get("body","")
    
    if frm and txt and txt.strip():
        from app.services.sms_service import SMSService
        svc = SMSService(db)
        await svc.process_inbound_message(frm, txt, {"messageId": msg_id})
        await db.flush()
    
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
