"""Inbox API — conversations, reply, status sync."""
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
            "contact_lead_status":contact.lead_status if contact else"","contact_business":contact.business_name if contact else"",
            "status":conv.status,"message_count":conv.message_count,"unread_count":conv.unread_count,
            "last_message_preview":conv.last_message_preview,
            "last_message_at":conv.last_message_at.isoformat() if conv.last_message_at else None,
            "created_at":conv.created_at.isoformat(),
            "contact":{"phone_number":contact.phone_number if contact else"","lead_status":contact.lead_status if contact else"",
                "business_name":contact.business_name if contact else"","first_name":contact.first_name if contact else"",
                "last_name":contact.last_name if contact else"","city":contact.city if contact else"",
                "state":contact.state if contact else"","website":contact.website if contact else"",
                "notes":contact.notes if contact else""} if contact else None})
    return {"total":total,"items":items}

@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = r.scalar_one_or_none()
    if not conv: raise HTTPException(404,"Not found")
    old_status = conv.status
    conv.status = "read"; conv.unread_count = 0
    cr = await db.execute(select(Contact).where(Contact.id == conv.contact_id));contact = cr.scalar_one_or_none()
    mr = await db.execute(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc()))
    messages = mr.scalars().all()
    await db.flush()
    return {"id":conv.id,"contact":{"id":contact.id if contact else None,"first_name":contact.first_name if contact else"",
        "last_name":contact.last_name if contact else"","business_name":contact.business_name if contact else"",
        "phone_number":contact.phone_number if contact else"","email":contact.email if contact else"",
        "lead_status":contact.lead_status if contact else"","city":contact.city if contact else"",
        "state":contact.state if contact else"","website":contact.website if contact else"",
        "industry":contact.industry if contact else"","notes":contact.notes if contact else"",
        "is_opted_out":contact.is_opted_out if contact else False} if contact else None,
        "status":conv.status,"sequence_paused":conv.sequence_paused,
        "was_unread":old_status=="unread",
        "messages":[{"id":m.id,"direction":m.direction,"body":m.body,"status":m.status,
            "segment_count":m.segment_count,"char_count":m.char_count,
            "sent_at":m.sent_at.isoformat() if m.sent_at else None,
            "delivered_at":m.delivered_at.isoformat() if m.delivered_at else None,
            "created_at":m.created_at.isoformat(),"provider_message_id":m.provider_message_id} for m in messages]}

@router.post("/conversations/{conversation_id}/reply")
async def send_reply(conversation_id:int,body:str=Query(...,min_length=1),
    db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = r.scalar_one_or_none()
    if not conv: raise HTTPException(404,"Not found")
    from app.services.sms_service import SMSService
    msg = await SMSService(db).send_message(contact_id=conv.contact_id,body=body)
    if not msg: raise HTTPException(500,"Gateway not configured")
    await db.flush()
    return {"success":True,"message_id":msg.id,"status":msg.status}

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

@router.post("/conversations/{conversation_id}/stop-sequence")
async def stop_seq(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404)
    conv.sequence_paused=True
    from app.services.sms_service import SMSService;await SMSService(db)._stop_contact_sequences(conv.contact_id)
    await db.flush();return{"success":True}

@router.post("/conversations/{conversation_id}/resume-sequence")
async def resume_seq(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404)
    conv.sequence_paused=False;await db.flush();return{"success":True}

@router.post("/poll-now")
async def poll_now(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    """Sync delivery statuses for all pending messages."""
    from app.models.conversation import Message
    from app.providers.smsgate import poll_status_for_ids
    msgs = await db.execute(
        select(Message).where(Message.provider_message_id.isnot(None),
                               Message.status.in_(("sent","sending","queued"))).limit(50))
    ids = [m.provider_message_id for m in msgs.scalars().all()]
    if not ids: return {"success":True,"processed":0,"details":"No pending messages"}
    results = await poll_status_for_ids(ids)
    count = 0
    for r in results:
        mr = await db.execute(select(Message).where(Message.provider_message_id == r["provider_message_id"]))
        m = mr.scalar_one_or_none()
        if m and m.status != r["status"]:
            m.status = r["status"]
            if r["status"] == "delivered":
                from datetime import datetime as dt, timezone as tz
                m.delivered_at = dt.now(tz.utc)
            count += 1
    await db.flush()
    return {"success":True,"processed":count,"total_checked":len(ids),"details":results[:10],"note":"Inbound SMS arrives via webhook. This syncs delivery statuses."}
