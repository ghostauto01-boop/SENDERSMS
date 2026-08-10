"""Inbox API — conversations, reply, webhook-based poll."""
import logging, base64
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.conversation import Conversation, Message
from app.models.contact import Contact
from app.models.user import User
from app.security.auth import get_current_user
from app.utils.phone import normalize_nigerian_number
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/conversations")
async def list_conversations(page:int=1,per_page:int=500,status:Optional[str]=None,search:Optional[str]=None,
    db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    query = select(Conversation)
    if status: query = query.where(Conversation.status == status)
    if search:
        query = query.join(Contact, Conversation.contact_id == Contact.id).where(or_(
            Contact.first_name.ilike(f"%{search}%"), Contact.last_name.ilike(f"%{search}%"),
            Contact.business_name.ilike(f"%{search}%"), Contact.phone_number.ilike(f"%{search}%"),
            Conversation.last_message_preview.ilike(f"%{search}%")))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    query = query.order_by(Conversation.last_message_at.desc().nullslast()).offset((page-1)*per_page).limit(per_page)
    convs = (await db.execute(query)).scalars().all()
    items = []
    for conv in convs:
        cr = await db.execute(select(Contact).where(Contact.id == conv.contact_id))
        contact = cr.scalar_one_or_none()
        name = (f"{contact.first_name or ''} {contact.last_name or ''}".strip() or contact.business_name or contact.phone_number) if contact else "Unknown"
        items.append({"id":conv.id,"contact_id":conv.contact_id,"contact_name":name,"contact_phone":contact.phone_number if contact else"",
            "contact_lead_status":contact.lead_status if contact else"","status":conv.status,
            "message_count":conv.message_count,"unread_count":conv.unread_count,
            "last_message_preview":conv.last_message_preview,
            "last_message_at":conv.last_message_at.isoformat() if conv.last_message_at else None,
            "contact":{"phone_number":contact.phone_number if contact else"","lead_status":contact.lead_status if contact else"",
                "business_name":contact.business_name if contact else"","first_name":contact.first_name if contact else"",
                "last_name":contact.last_name if contact else"","city":contact.city if contact else"",
                "state":contact.state if contact else"","website":contact.website if contact else""} if contact else None})
    return {"total":total,"items":items}

@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404,"Not found")
    conv.status="read";conv.unread_count=0
    cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));contact=cr.scalar_one_or_none()
    mr=await db.execute(select(Message).where(Message.conversation_id==conversation_id).order_by(Message.created_at.asc()))
    await db.flush()
    return {"id":conv.id,"contact":{"phone_number":contact.phone_number if contact else"","first_name":contact.first_name if contact else"","last_name":contact.last_name if contact else"","business_name":contact.business_name if contact else"","lead_status":contact.lead_status if contact else"","city":contact.city if contact else"","state":contact.state if contact else""} if contact else None,
        "status":conv.status,"sequence_paused":conv.sequence_paused,
        "messages":[{"id":m.id,"direction":m.direction,"body":m.body,"status":m.status,
            "created_at":m.created_at.isoformat(),"delivered_at":m.delivered_at.isoformat() if m.delivered_at else None,
            "sent_at":m.sent_at.isoformat() if m.sent_at else None,"provider_message_id":m.provider_message_id} for m in mr.scalars().all()]}

@router.post("/conversations/{conversation_id}/reply")
async def send_reply(conversation_id:int,body:str=Query(...,min_length=1),
    db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404)
    from app.services.sms_service import SMSService
    msg=await SMSService(db).send_message(contact_id=conv.contact_id,body=body)
    if not msg: raise HTTPException(500,"Gateway not configured")
    await db.flush();return{"success":True,"message_id":msg.id,"status":msg.status}

@router.post("/conversations/{conversation_id}/mark-interested")
async def mark_interested(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404)
    conv.status="interested"
    cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));c=cr.scalar_one_or_none()
    if c: c.lead_status="interested"
    await db.flush();return{"success":True}

@router.post("/conversations/{conversation_id}/mark-not-interested")
async def mark_not_interested(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404)
    conv.status="not_interested"
    cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));c=cr.scalar_one_or_none()
    if c: c.lead_status="not_interested"
    await db.flush();return{"success":True}

@router.post("/conversations/{conversation_id}/mark-close")
async def close_conversation(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404)
    conv.status="closed"
    cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));c=cr.scalar_one_or_none()
    if c: c.lead_status="closed"
    await db.flush();return{"success":True}

@router.post("/poll-now")
async def poll_now(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    """
    Poll GET /messages and sync both delivery statuses AND incoming SMS.
    Matches phone numbers to find replies (sms-gate.app /messages list doesn't have direction field).
    """
    import httpx
    u = (settings.SMSGATE_USERNAME or "").strip()
    p = (settings.SMSGATE_PASSWORD or "").strip()
    if not u or not p: return {"success":False,"error":"No credentials"}

    auth = base64.b64encode(f"{u}:{p}".encode()).decode()
    url = "https://api.sms-gate.app/3rdparty/v1/messages?limit=50"

    debug = {"url":url,"step":"fetching"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
            r = await client.get(url, headers={"Content-Type":"application/json","Authorization":f"Basic {auth}"})
            debug["http"] = r.status_code
            if r.status_code != 200:
                return {"success":False,"error":f"HTTP {r.status_code}","debug":debug}
            data = r.json()
            msgs = data if isinstance(data, list) else data.get("messages",data.get("data",[]))
            debug["total_messages"] = len(msgs)
    except Exception as e:
        return {"success":False,"error":str(e)[:200],"debug":debug}

    from app.services.sms_service import SMSService
    svc = SMSService(db)
    updated = []
    inbound = []
    status_updates = 0

    # First pass: update delivery statuses for known outgoing messages
    for msg in msgs:
        mid = msg.get("id") or msg.get("messageId","")
        state = (msg.get("state") or "").lower()
        recips = msg.get("recipients", [])

        if mid and state:
            # Update delivery status
            mr = await db.execute(select(Message).where(Message.provider_message_id == mid))
            existing = mr.scalar_one_or_none()
            if existing:
                status_map = {"delivered":"delivered","failed":"failed","sent":"sent","processed":"sent","pending":"queued"}
                new_status = status_map.get(state, state)
                if existing.status != new_status:
                    existing.status = new_status
                    if new_status == "delivered":
                        from datetime import datetime as dt, timezone as tz
                        existing.delivered_at = dt.now(tz.utc)
                    status_updates += 1

    # Second pass: find INCOMING messages by checking phone numbers against known contacts
    known_phones = set()
    contacts_result = await db.execute(select(Contact).all())
    for c in contacts_result.scalars().all():
        known_phones.add(c.phone_number)

    for msg in msgs:
        mid = msg.get("id") or msg.get("messageId","")
        recips = msg.get("recipients", [])
        state = (msg.get("state") or "").lower()

        # Inbound SMS from SMS-Gate.app webhook format: has sender + text
        snd = msg.get("sender") or msg.get("from","")
        txt = msg.get("text") or msg.get("body") or msg.get("message","")

        if snd and txt and txt.strip():
            # This is a message with sender info — incoming
            norm = normalize_nigerian_number(snd)
            if not norm: continue
            idem = f"inbound-{mid}" if mid else f"inbound-{norm}-{hash(txt)}"
            ex = await db.execute(select(Message).where(Message.idempotency_key == idem))
            if not ex.scalar_one_or_none():
                result = await svc.process_inbound_message(snd, txt, {"messageId": mid} if mid else {})
                if result:
                    inbound.append({"from":snd,"text":txt[:80],"id":mid})

        # Also check recipients for incoming patterns
        for recipient in recips:
            phone = recipient.get("phoneNumber","")
            rstate = (recipient.get("state") or "").lower()
            # If this is a message we didn't send (no matching outgoing), treat as inbound
            if phone and not snd:
                norm = normalize_nigerian_number(phone)
                if norm and norm in known_phones:
                    # This might be an incoming — check if it has text
                    pass  # Can't extract text from recipients-only format

    await db.flush()

    debug["status_updates"] = status_updates
    debug["inbound_found"] = len(inbound)
    debug["webhook_url"] = "https://sendsms-api.onrender.com/api/v1/webhooks/smsgateway"

    return {
        "success": True,
        "status_updates": status_updates,
        "inbound": len(inbound),
        "total_checked": len(msgs),
        "details": inbound[:20],
        "debug": debug,
        "note": "Inbound SMS arrive via webhook. Configure in SMS-Gate.app → Settings → Webhooks."
    }
