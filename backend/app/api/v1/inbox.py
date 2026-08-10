"""Inbox API — conversations, messages, manual inbox poll with debug."""
import json, logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.conversation import Conversation, Message
from app.models.contact import Contact
from app.models.user import User
from app.security.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/conversations")
async def list_conversations(
    page: int = Query(default=1, ge=1), per_page: int = Query(default=100, ge=1, le=500),
    status: Optional[str] = None, search: Optional[str] = None, campaign_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = select(Conversation)
    if status: query = query.where(Conversation.status == status)
    if campaign_id: query = query.where(Conversation.campaign_id == campaign_id)
    if search:
        query = query.join(Contact, Conversation.contact_id == Contact.id).where(or_(
            Contact.first_name.ilike(f"%{search}%"), Contact.last_name.ilike(f"%{search}%"),
            Contact.business_name.ilike(f"%{search}%"), Contact.phone_number.ilike(f"%{search}%"),
            Conversation.last_message_preview.ilike(f"%{search}%")))
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0
    query = query.order_by(Conversation.last_message_at.desc().nullslast()).offset((page-1)*per_page).limit(per_page)
    conversations = (await db.execute(query)).scalars().all()
    items = []
    for conv in conversations:
        cr = await db.execute(select(Contact).where(Contact.id == conv.contact_id))
        contact = cr.scalar_one_or_none()
        items.append({
            "id": conv.id, "contact_id": conv.contact_id,
            "contact_name": (contact.business_name or f"{contact.first_name or ''} {contact.last_name or ''}".strip() or contact.phone_number) if contact else "Unknown",
            "contact_phone": contact.phone_number if contact else "", "contact_lead_status": contact.lead_status if contact else "",
            "campaign_id": conv.campaign_id, "status": conv.status, "message_count": conv.message_count,
            "unread_count": conv.unread_count, "last_message_preview": conv.last_message_preview,
            "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
            "created_at": conv.created_at.isoformat(),
            "contact": {"phone_number": contact.phone_number if contact else "", "lead_status": contact.lead_status if contact else "",
                         "business_name": contact.business_name if contact else "", "city": contact.city if contact else "",
                         "state": contact.state if contact else "", "website": contact.website if contact else "",
                         "notes": contact.notes if contact else ""} if contact else None
        })
    return {"total": total, "items": items}

@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    r = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = r.scalar_one_or_none()
    if not conv: raise HTTPException(404, "Not found")
    conv.status = "read"; conv.unread_count = 0
    cr = await db.execute(select(Contact).where(Contact.id == conv.contact_id))
    contact = cr.scalar_one_or_none()
    mr = await db.execute(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc()))
    messages = mr.scalars().all()
    await db.flush()
    return {"id": conv.id, "contact": {"id": contact.id if contact else None, "first_name": contact.first_name if contact else "",
        "last_name": contact.last_name if contact else "", "business_name": contact.business_name if contact else "",
        "phone_number": contact.phone_number if contact else "", "email": contact.email if contact else "",
        "lead_status": contact.lead_status if contact else "", "city": contact.city if contact else "",
        "state": contact.state if contact else "", "website": contact.website if contact else "",
        "industry": contact.industry if contact else "", "notes": contact.notes if contact else "",
        "is_opted_out": contact.is_opted_out if contact else False} if contact else None,
        "campaign_id": conv.campaign_id, "status": conv.status, "sequence_paused": conv.sequence_paused,
        "messages": [{"id": m.id, "direction": m.direction, "body": m.body, "status": m.status,
            "segment_count": m.segment_count, "char_count": m.char_count,
            "sent_at": m.sent_at.isoformat() if m.sent_at else None,
            "delivered_at": m.delivered_at.isoformat() if m.delivered_at else None,
            "created_at": m.created_at.isoformat(), "provider_message_id": m.provider_message_id} for m in messages]}

@router.post("/conversations/{conversation_id}/reply")
async def send_reply(conversation_id: int, body: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    r = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = r.scalar_one_or_none()
    if not conv: raise HTTPException(404, "Not found")
    from app.services.sms_service import SMSService
    svc = SMSService(db)
    msg = await svc.send_message(contact_id=conv.contact_id, body=body, campaign_id=conv.campaign_id)
    if not msg: raise HTTPException(500, "Gateway not configured")
    await db.flush()
    return {"success": True, "message_id": msg.id, "status": msg.status}

@router.post("/conversations/{conversation_id}/mark-interested")
async def mark_interested(conversation_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    r = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = r.scalar_one_or_none()
    if not conv: raise HTTPException(404, "Not found")
    conv.status = "interested"
    cr = await db.execute(select(Contact).where(Contact.id == conv.contact_id))
    c = cr.scalar_one_or_none()
    if c: c.lead_status = "interested"
    await db.flush(); return {"success": True}

@router.post("/conversations/{conversation_id}/mark-not-interested")
async def mark_not_interested(conversation_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    r = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = r.scalar_one_or_none()
    if not conv: raise HTTPException(404, "Not found")
    conv.status = "not_interested"
    cr = await db.execute(select(Contact).where(Contact.id == conv.contact_id))
    c = cr.scalar_one_or_none()
    if c: c.lead_status = "not_interested"
    await db.flush(); return {"success": True}

@router.post("/conversations/{conversation_id}/mark-close")
async def close_conversation(conversation_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    r = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = r.scalar_one_or_none()
    if not conv: raise HTTPException(404, "Not found")
    conv.status = "closed"
    cr = await db.execute(select(Contact).where(Contact.id == conv.contact_id))
    c = cr.scalar_one_or_none()
    if c: c.lead_status = "closed"
    await db.flush(); return {"success": True}

@router.post("/conversations/{conversation_id}/stop-sequence")
async def stop_sequence(conversation_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    r = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = r.scalar_one_or_none()
    if not conv: raise HTTPException(404, "Not found")
    conv.sequence_paused = True
    from app.services.sms_service import SMSService
    await SMSService(db)._stop_contact_sequences(conv.contact_id)
    await db.flush(); return {"success": True}

@router.post("/conversations/{conversation_id}/resume-sequence")
async def resume_sequence(conversation_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    r = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = r.scalar_one_or_none()
    if not conv: raise HTTPException(404, "Not found")
    conv.sequence_paused = False
    await db.flush(); return {"success": True}

# MANUAL POLL — EXACT same pattern as send_sms for reliability
@router.post("/poll-now")
async def poll_inbox_now(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Manually poll SMS-Gate.app inbox. Uses EXACT same auth pattern as SMS sending."""
    import base64, httpx
    from app.config import settings

    u = settings.SMSGATE_USERNAME or ""
    p = settings.SMSGATE_PASSWORD or ""
    if not u or not p:
        return {"success": False, "error": "Gateway credentials not set", "total_found": 0, "processed": 0}

    auth = base64.b64encode(f"{u}:{p}".encode()).decode()
    headers = {"Content-Type": "application/json", "Authorization": f"Basic {auth}"}
    url = "https://api.sms-gate.app/3rdparty/v1/inbox"

    debug_info = {"url": url, "auth_user": u[:4] + "..."}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
            resp = await client.get(url, headers=headers)
            status = resp.status_code
            raw_text = resp.text

            logger.info(f"INBOX POLL: HTTP {status}, body_len={len(raw_text)}")

            if status != 200:
                return {"success": False, "error": f"HTTP {status}", "total_found": 0, "processed": 0,
                        "debug": {"http_status": status, "response": raw_text[:500], "url": url}}

            try:
                data = resp.json()
            except:
                return {"success": False, "error": "Could not parse JSON response", "total_found": 0, "processed": 0,
                        "debug": {"http_status": status, "response": raw_text[:500]}}

            # Handle various response formats
            messages = []
            if isinstance(data, list):
                messages = data
            elif isinstance(data, dict):
                messages = data.get("messages") or data.get("data") or data.get("inbox") or []
                if not messages and "id" in data:
                    messages = [data]  # single message object

            logger.info(f"INBOX POLL: parsed {len(messages)} messages")
            debug_info["raw_count"] = len(messages)

            from app.services.sms_service import SMSService
            svc = SMSService(db)
            processed = []
            errors = []

            for msg in messages[:20]:
                snd = (msg.get("sender") or msg.get("from") or msg.get("phoneNumber")
                       or msg.get("number") or msg.get("address") or "")
                txt = (msg.get("text") or msg.get("message") or msg.get("body")
                       or msg.get("content") or "")
                mid = msg.get("messageId") or msg.get("id") or msg.get("_id") or ""

                if snd and txt and txt.strip() and mid:
                    try:
                        existing = await db.execute(
                            select(Message).where(Message.idempotency_key == f"inbound-{mid}"))
                        if not existing.scalar_one_or_none():
                            result = await svc.process_inbound_message(snd, txt, {"messageId": mid})
                            processed.append({"from": snd, "text": txt[:80], "id": mid, "ok": result is not None})
                            if result:
                                logger.info(f"INBOX: processed message from {snd}")
                    except Exception as e2:
                        errors.append({"from": snd, "error": str(e2)[:100]})
                elif snd and txt and txt.strip() and not mid:
                    # No message ID — still process but use a synthetic key
                    try:
                        result = await svc.process_inbound_message(snd, txt)
                        if result:
                            processed.append({"from": snd, "text": txt[:80], "id": "synthetic", "ok": True})
                    except Exception as e2:
                        errors.append({"from": snd, "error": str(e2)[:100]})

            await db.flush()
            debug_info["processed"] = len(processed)
            debug_info["errors"] = errors

            return {"success": True, "total_found": len(messages), "processed": len(processed),
                    "details": processed, "debug": debug_info}

    except httpx.ConnectError:
        return {"success": False, "error": "Cannot connect to api.sms-gate.app — check internet", "total_found": 0}
    except httpx.TimeoutException:
        return {"success": False, "error": "Timed out — phone may be offline", "total_found": 0}
    except Exception as e:
        return {"success": False, "error": str(e)[:500], "total_found": 0, "debug": debug_info}
