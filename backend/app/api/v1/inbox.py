"""Inbox API — poll-now converts GET /messages response to chat messages."""
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
from datetime import datetime as dt, timezone as tz

logger = logging.getLogger(__name__)
router = APIRouter()

# ---- standard CRUD (unchanged) ----

@router.get("/conversations")
async def list_conversations(page:int=1,per_page:int=500,status:Optional[str]=None,search:Optional[str]=None,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    query = select(Conversation); 
    if status: query = query.where(Conversation.status == status)
    if search: query = query.join(Contact, Conversation.contact_id == Contact.id).where(or_(Contact.first_name.ilike(f"%{search}%"),Contact.last_name.ilike(f"%{search}%"),Contact.business_name.ilike(f"%{search}%"),Contact.phone_number.ilike(f"%{search}%"),Conversation.last_message_preview.ilike(f"%{search}%")))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    query = query.order_by(Conversation.last_message_at.desc().nullslast()).offset((page-1)*per_page).limit(per_page)
    items = []; 
    for conv in (await db.execute(query)).scalars().all():
        cr = await db.execute(select(Contact).where(Contact.id == conv.contact_id)); contact = cr.scalar_one_or_none()
        name = (f"{contact.first_name or ''} {contact.last_name or ''}".strip() or contact.business_name or contact.phone_number) if contact else "Unknown"
        items.append({"id":conv.id,"contact_id":conv.contact_id,"contact_name":name,"contact_phone":contact.phone_number if contact else"","contact_lead_status":contact.lead_status if contact else"","status":conv.status,"message_count":conv.message_count,"unread_count":conv.unread_count,"last_message_preview":conv.last_message_preview,"last_message_at":conv.last_message_at.isoformat()if conv.last_message_at else None,"contact":{"phone_number":contact.phone_number if contact else"","lead_status":contact.lead_status if contact else"","business_name":contact.business_name if contact else"","first_name":contact.first_name if contact else"","last_name":contact.last_name if contact else"","city":contact.city if contact else"","state":contact.state if contact else""}if contact else None})
    return {"total":total,"items":items}

@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404); conv.status="read";conv.unread_count=0
    cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));contact=cr.scalar_one_or_none()
    mr=await db.execute(select(Message).where(Message.conversation_id==conversation_id).order_by(Message.created_at.asc()))
    await db.flush()
    return {"id":conv.id,"contact":{"phone_number":contact.phone_number if contact else"","first_name":contact.first_name if contact else"","last_name":contact.last_name if contact else"","business_name":contact.business_name if contact else"","lead_status":contact.lead_status if contact else"","city":contact.city if contact else"","state":contact.state if contact else""}if contact else None,"status":conv.status,"messages":[{"id":m.id,"direction":m.direction,"body":m.body,"status":m.status,"created_at":m.created_at.isoformat(),"delivered_at":m.delivered_at.isoformat()if m.delivered_at else None,"sent_at":m.sent_at.isoformat()if m.sent_at else None,"provider_message_id":m.provider_message_id}for m in mr.scalars().all()]}

@router.post("/conversations/{conversation_id}/reply")
async def send_reply(conversation_id:int,body:str=Query(...,min_length=1),db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404); from app.services.sms_service import SMSService; msg=await SMSService(db).send_message(contact_id=conv.contact_id,body=body)
    if not msg: raise HTTPException(500,"Gateway not configured"); await db.flush();return{"success":True,"message_id":msg.id,"status":msg.status}

@router.post("/conversations/{conversation_id}/mark-interested")
async def mark_interested(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404); conv.status="interested"; cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));c=cr.scalar_one_or_none()
    if c: c.lead_status="interested"; await db.flush();return{"success":True}

@router.post("/conversations/{conversation_id}/mark-not-interested")
async def mark_not_interested(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404); conv.status="not_interested"; cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));c=cr.scalar_one_or_none()
    if c: c.lead_status="not_interested"; await db.flush();return{"success":True}

@router.post("/conversations/{conversation_id}/mark-close")
async def close_conversation(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404); conv.status="closed"; cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));c=cr.scalar_one_or_none()
    if c: c.lead_status="closed"; await db.flush();return{"success":True}

# ---- SMART POLL: converts [{id, state, recipients}] → chats ----

@router.post("/poll-now")
async def poll_now(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    """
    Poll GET /messages, convert recipient phone numbers into threaded conversations.
    
    API returns: [{id, state, deviceId, isEncrypted, recipients: [{phoneNumber, state}]}]
    We convert recipients → contact lookups → threaded messages in conversations.
    """
    import httpx, uuid
    u=(settings.SMSGATE_USERNAME or"").strip(); p=(settings.SMSGATE_PASSWORD or"").strip()
    if not u or not p: return{"success":False,"error":"No credentials"}

    auth=base64.b64encode(f"{u}:{p}".encode()).decode()
    url="https://api.sms-gate.app/3rdparty/v1/messages?limit=50"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
            r=await client.get(url,headers={"Content-Type":"application/json","Authorization":f"Basic {auth}"})
            if r.status_code!=200: return{"success":False,"error":f"HTTP {r.status_code}","debug":{"http":r.status_code}}
            raw=r.json()
            msgs=raw if isinstance(raw,list) else raw.get("messages",raw.get("data",[]))
    except Exception as e: return{"success":False,"error":str(e)[:200]}

    from app.services.sms_service import SMSService
    svc=SMSService(db)
    stats={"total":len(msgs),"status_updates":0,"new_conversations":0,"new_messages":0,"details":[]}
    
    # Track known provider IDs to avoid duplicates
    seen_ids=set(
        (r[0] or"") for r in (await db.execute(select(Message.provider_message_id))).all()
    )

    for msg in msgs:
        mid=msg.get("id")or msg.get("messageId","")
        state=(msg.get("state")or"").lower()
        device_id=msg.get("deviceId","")
        recipients=msg.get("recipients",[])
        is_encrypted=msg.get("isEncrypted",False)
        created_at_raw=msg.get("createdAt")  # some API versions include this
        
        # Parse timestamps from state changes
        states=msg.get("states",{})
        timestamp=None
        for ts in states.values():
            try: timestamp=dt.fromisoformat(ts.replace("Z","+00:00"));break
            except: pass

        # ---- DELIVERY STATUS UPDATE for known outgoing ----
        if mid and state:
            mr=await db.execute(select(Message).where(Message.provider_message_id==mid))
            existing=mr.scalar_one_or_none()
            if existing:
                status_map={"delivered":"delivered","failed":"failed","sent":"sent","processed":"sent","pending":"queued"}
                ns=status_map.get(state,state)
                if existing.status!=ns:
                    existing.status=ns
                    if ns=="delivered": existing.delivered_at=dt.now(tz.utc)
                    stats["status_updates"]+=1
                continue  # already tracked this as an outgoing message

        # ---- INCOMING: Treat EACH recipient as a potential inbound sender ----
        if not recipients:
            continue

        for recipient in recipients:
            phone=(recipient.get("phoneNumber")or"").strip()
            rstate=(recipient.get("state")or"").lower()
            if not phone: continue

            norm=normalize_nigerian_number(phone)
            if not norm: continue

            # Skip if this exact message ID was already processed
            idem_key=f"inbound-poll-{mid}-{norm}"
            if idem_key in seen_ids: continue
            seen_ids.add(idem_key)

            # Find or create contact
            cr=await db.execute(select(Contact).where(Contact.phone_number==norm))
            contact=cr.scalar_one_or_none()
            if not contact:
                contact=Contact(phone_number=norm,country="Nigeria",lead_status="new",source="inbound_poll")
                db.add(contact); await db.flush()

            # Find or create conversation
            conv_r=await db.execute(select(Conversation).where(Conversation.contact_id==contact.id))
            conv=conv_r.scalar_one_or_none()
            if not conv:
                conv=Conversation(contact_id=contact.id,status="unread")
                db.add(conv); await db.flush()
                stats["new_conversations"]+=1
            else:
                conv.status="unread"; conv.unread_count=(conv.unread_count or 0)+1

            # Create message record — treat as inbound since we didn't send it
            body=f"[Received from {norm} on device {device_id[:8]}]" if is_encrypted else f"[Message ID: {mid[:12]}]"
            
            # Try to enrich: if there's text payload hidden in msg
            text_payload=msg.get("text")or msg.get("body")or msg.get("message")or body
            
            m=Message(
                conversation_id=conv.id,contact_id=contact.id,direction="incoming",
                body=text_payload[:500],segment_count=1,char_count=len(text_payload[:500]),
                status="delivered",provider="smsgate",
                provider_message_id=mid,
                idempotency_key=idem_key,
                delivered_at=timestamp or dt.now(tz.utc)
            )
            db.add(m); await db.flush()
            
            conv.message_count=(conv.message_count or 0)+1
            conv.last_message_preview=text_payload[:100]
            conv.last_message_at=dt.now(tz.utc)
            contact.messages_received=(contact.messages_received or 0)+1
            contact.last_reply_at=dt.now(tz.utc)
            if contact.lead_status=="new": contact.lead_status="replied"
            
            stats["new_messages"]+=1
            stats["details"].append({
                "phone":norm,"device":device_id[:12],"state":rstate,
                "msg_id":mid[:16],"has_text":bool(msg.get("text")or msg.get("body")),
                "text_preview":text_payload[:60]
            })

    await db.flush()

    # Auto-register webhook attempt for future instant delivery
    try:
        wh_url="https://sendsms-api.onrender.com/api/v1/webhooks/smsgateway"
        wh_r=await client.post("https://api.sms-gate.app/3rdparty/v1/webhooks",
            headers={"Content-Type":"application/json","Authorization":f"Basic {auth}"},
            json={"url":wh_url,"events":["sms:received","sms:delivered","sms:sent","sms:failed"]})
        stats["webhook_registered"]=wh_r.status_code<400
    except: pass

    stats["webhook_url"]="https://sendsms-api.onrender.com/api/v1/webhooks/smsgateway"
    stats["note"]="Each recipient phone number is treated as a potential inbound contact. Configure webhook for real-time delivery."

    return{"success":True,**stats,"raw_count":len(msgs)}
