"""Inbox API — Poll messages via GET /3rdparty/v1/messages."""
import logging
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

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/conversations")
async def list_conversations(page:int=1,per_page:int=100,status:Optional[str]=None,search:Optional[str]=None,campaign_id:Optional[int]=None,
    db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    query = select(Conversation)
    if status:query=query.where(Conversation.status==status)
    if campaign_id:query=query.where(Conversation.campaign_id==campaign_id)
    if search:query=query.join(Contact,Conversation.contact_id==Contact.id).where(or_(Contact.first_name.ilike(f"%{search}%"),Contact.last_name.ilike(f"%{search}%"),Contact.business_name.ilike(f"%{search}%"),Contact.phone_number.ilike(f"%{search}%"),Conversation.last_message_preview.ilike(f"%{search}%")))
    total=(await db.execute(select(func.count()).select_from(query.subquery()))).scalar()or 0
    query=query.order_by(Conversation.last_message_at.desc().nullslast()).offset((page-1)*per_page).limit(per_page)
    convs=(await db.execute(query)).scalars().all()
    items=[]
    for conv in convs:
        cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));contact=cr.scalar_one_or_none()
        items.append({"id":conv.id,"contact_id":conv.contact_id,"contact_name":(contact.business_name or f"{contact.first_name or ''} {contact.last_name or ''}".strip() or contact.phone_number)if contact else"Unknown","contact_phone":contact.phone_number if contact else"","contact_lead_status":contact.lead_status if contact else"","campaign_id":conv.campaign_id,"status":conv.status,"message_count":conv.message_count,"unread_count":conv.unread_count,"last_message_preview":conv.last_message_preview,"last_message_at":conv.last_message_at.isoformat()if conv.last_message_at else None,"created_at":conv.created_at.isoformat()})
    return {"total":total,"items":items}

@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv:raise HTTPException(404,"Not found")
    conv.status="read";conv.unread_count=0
    cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));contact=cr.scalar_one_or_none()
    mr=await db.execute(select(Message).where(Message.conversation_id==conversation_id).order_by(Message.created_at.asc()));messages=mr.scalars().all()
    await db.flush()
    return {"id":conv.id,"contact":{"id":contact.id if contact else None,"first_name":contact.first_name if contact else"","last_name":contact.last_name if contact else"","business_name":contact.business_name if contact else"","phone_number":contact.phone_number if contact else"","email":contact.email if contact else"","lead_status":contact.lead_status if contact else"","city":contact.city if contact else"","state":contact.state if contact else"","website":contact.website if contact else"","industry":contact.industry if contact else"","notes":contact.notes if contact else"","is_opted_out":contact.is_opted_out if contact else False}if contact else None,"campaign_id":conv.campaign_id,"status":conv.status,"sequence_paused":conv.sequence_paused,"messages":[{"id":m.id,"direction":m.direction,"body":m.body,"status":m.status,"segment_count":m.segment_count,"char_count":m.char_count,"sent_at":m.sent_at.isoformat()if m.sent_at else None,"delivered_at":m.delivered_at.isoformat()if m.delivered_at else None,"created_at":m.created_at.isoformat(),"provider_message_id":m.provider_message_id}for m in messages]}

@router.post("/conversations/{conversation_id}/reply")
async def send_reply(conversation_id:int,body:str=Query(...,min_length=1),db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv:raise HTTPException(404,"Not found")
    from app.services.sms_service import SMSService;svc=SMSService(db)
    msg=await svc.send_message(contact_id=conv.contact_id,body=body,campaign_id=conv.campaign_id)
    if not msg:raise HTTPException(500,"Gateway not configured")
    await db.flush();return{"success":True,"message_id":msg.id,"status":msg.status}

@router.post("/conversations/{conversation_id}/mark-interested")
async def mark_interested(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv:raise HTTPException(404,"Not found")
    conv.status="interested";cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));c=cr.scalar_one_or_none()
    if c:c.lead_status="interested"
    await db.flush();return{"success":True}

@router.post("/conversations/{conversation_id}/mark-not-interested")
async def mark_not_interested(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv:raise HTTPException(404,"Not found")
    conv.status="not_interested";cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));c=cr.scalar_one_or_none()
    if c:c.lead_status="not_interested"
    await db.flush();return{"success":True}

@router.post("/conversations/{conversation_id}/mark-close")
async def close_conversation(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv:raise HTTPException(404,"Not found")
    conv.status="closed";cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));c=cr.scalar_one_or_none()
    if c:c.lead_status="closed"
    await db.flush();return{"success":True}

@router.post("/conversations/{conversation_id}/stop-sequence")
async def stop_seq(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv:raise HTTPException(404,"Not found")
    conv.sequence_paused=True;from app.services.sms_service import SMSService;await SMSService(db)._stop_contact_sequences(conv.contact_id)
    await db.flush();return{"success":True}

@router.post("/conversations/{conversation_id}/resume-sequence")
async def resume_seq(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv:raise HTTPException(404,"Not found")
    conv.sequence_paused=False;await db.flush();return{"success":True}

# Poll messages via GET /3rdparty/v1/messages
@router.post("/poll-now")
async def poll_now(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    """Poll GET /3rdparty/v1/messages for incoming SMS."""
    from app.providers.smsgate import poll_messages_direct
    result = await poll_messages_direct()
    if not result.get("success"):
        return {"success":False,"error":result.get("error","Failed to poll messages"),"processed":0}
    
    messages = result.get("messages",[])
    from app.services.sms_service import SMSService
    svc = SMSService(db)
    processed = []
    count = 0
    
    for msg in messages:
        direction = msg.get("direction","")
        snd = msg.get("sender") or msg.get("from") or msg.get("phoneNumber","")
        txt = msg.get("text") or msg.get("body") or msg.get("message","")
        mid = msg.get("id") or msg.get("messageId","")
        status = msg.get("status","")
        
        # Update delivery status for outgoing
        if mid and status and direction == "outgoing":
            mr = await db.execute(select(Message).where(Message.provider_message_id == mid))
            existing = mr.scalar_one_or_none()
            if existing and existing.status != status:
                existing.status = status
                count += 1
            continue
        
        # Incoming
        if snd and txt and txt.strip():
            norm = normalize_nigerian_number(snd)
            if not norm: continue
            idem = f"inbound-{mid}" if mid else f"inbound-poll-{norm}"
            ex = await db.execute(select(Message).where(Message.idempotency_key == idem))
            if not ex.scalar_one_or_none():
                r = await svc.process_inbound_message(snd, txt, {"messageId": mid} if mid else {})
                if r:
                    processed.append({"from": snd, "text": txt[:80]})
                    count += 1
    
    await db.flush()
    return {"success": True, "total_found": len(messages), "processed": count, "details": processed[:10]}
