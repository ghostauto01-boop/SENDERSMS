"""Send SMS — uses send_sms_direct + scheduling."""
import json,logging,uuid,os
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter,Depends,HTTPException,Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.user import User
from app.security.auth import get_current_user
from app.models.contact import Contact
from app.models.contact_list import ContactListMember
from app.models.conversation import Conversation,Message
from app.models.scheduled import ScheduledMessage
from app.utils.phone import normalize_nigerian_number,count_sms_segments
from app.utils.templating import render_template
from app.config import settings

logger=logging.getLogger(__name__)
router=APIRouter()

@router.post("/")
async def send_sms_now(contact_id:Optional[int]=Query(None),phone_number:Optional[str]=Query(None),list_id:Optional[int]=Query(None),body:str=Query(...,min_length=1,max_length=1600),schedule_at:Optional[str]=Query(None),db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    from app.services.system_settings import get_sim_number
    sim=await get_sim_number(db)

    # SCHEDULED
    if schedule_at:
        try:sched_dt=datetime.fromisoformat(schedule_at)
        except Exception:raise HTTPException(400,"Invalid schedule_at format")
        if sched_dt<=datetime.now(timezone.utc):raise HTTPException(400,"schedule_at must be in the future")
        scheduled=[]
        if contact_id:
            r=await db.execute(select(Contact).where(Contact.id==contact_id));c=r.scalar_one_or_none()
            if not c:raise HTTPException(404,"Contact not found")
            if c.is_opted_out:raise HTTPException(400,"Contact opted out")
            sm=ScheduledMessage(contact_id=c.id,phone_number=c.phone_number,body=body,schedule_at=sched_dt,sim_number=sim);db.add(sm);scheduled.append(c.phone_number)
        elif phone_number:
            norm=normalize_nigerian_number(phone_number)
            if not norm:raise HTTPException(400,f"Invalid number:{phone_number}")
            sm=ScheduledMessage(phone_number=norm,body=body,schedule_at=sched_dt,sim_number=sim);db.add(sm);scheduled.append(norm)
        elif list_id:
            members=await db.execute(select(ContactListMember).where(ContactListMember.list_id==list_id))
            for m in members.scalars().all():
                cr=await db.execute(select(Contact).where(Contact.id==m.contact_id));cc=cr.scalar_one_or_none()
                if cc and not cc.is_opted_out:
                    sm=ScheduledMessage(contact_id=cc.id,phone_number=cc.phone_number,body=body,schedule_at=sched_dt,sim_number=sim);db.add(sm);scheduled.append(cc.phone_number)
        else:raise HTTPException(400,"Provide contact_id, phone_number, or list_id")
        await db.flush()
        return{"success":True,"scheduled":True,"count":len(scheduled),"schedule_at":schedule_at,"note":f"{len(scheduled)} message(s) scheduled"}

    # SEND NOW
    recipients=[]
    if contact_id:
        r=await db.execute(select(Contact).where(Contact.id==contact_id));c=r.scalar_one_or_none()
        if not c:raise HTTPException(404,"Contact not found")
        if c.is_opted_out:raise HTTPException(400,"Contact opted out")
        recipients.append(c)
    elif phone_number:
        norm=normalize_nigerian_number(phone_number)
        if not norm:raise HTTPException(400,f"Invalid:{phone_number}")
        r=await db.execute(select(Contact).where(Contact.phone_number==norm));c=r.scalar_one_or_none()
        if not c:c=Contact(phone_number=norm,country="Nigeria",lead_status="new",source="manual");db.add(c);await db.flush()
        if c.is_opted_out:raise HTTPException(400,"Contact opted out")
        recipients.append(c)
    elif list_id:
        members=await db.execute(select(ContactListMember).where(ContactListMember.list_id==list_id))
        for m in members.scalars().all():
            cr=await db.execute(select(Contact).where(Contact.id==m.contact_id));cc=cr.scalar_one_or_none()
            if cc and not cc.is_opted_out:recipients.append(cc)
        if not recipients:raise HTTPException(400,"List empty")
    else:raise HTTPException(400,"Provide contact_id, phone_number, or list_id")

    # Counted per recipient below: personalization changes the length, so a
    # single count taken from the raw template misreports segments (and cost)
    # for every contact.
    char_count,segment_count=count_sms_segments(body)
    from app.providers.smsgate import send_sms_direct
    results=[]

    for contact in recipients:
        msg=render_template(body,contact)
        char_count,segment_count=count_sms_segments(msg)
        cr=await db.execute(select(Conversation).where(Conversation.contact_id==contact.id).order_by(Conversation.id).limit(1));conv=cr.scalars().first()
        if not conv:conv=Conversation(contact_id=contact.id,status="active");db.add(conv);await db.flush()
        message=Message(conversation_id=conv.id,contact_id=contact.id,direction="outgoing",body=msg,segment_count=segment_count,char_count=char_count,status="sending",provider="smsgate",idempotency_key=f"direct-{contact.id}-{uuid.uuid4().hex[:8]}")
        db.add(message);await db.flush()
        r=await send_sms_direct(contact.phone_number,msg,sim)
        if r["success"]:message.status="sent";message.provider_message_id=r.get("provider_message_id","");message.sent_at=datetime.now(timezone.utc)
        else:message.status="failed";message.last_error=r.get("error","");message.failed_at=datetime.now(timezone.utc)
        message.provider_response=json.dumps(r.get("raw"))if r.get("raw")else None
        conv.message_count=(conv.message_count or 0)+1;conv.last_message_preview=msg[:100];conv.last_message_at=datetime.now(timezone.utc)
        contact.messages_sent=(contact.messages_sent or 0)+1;contact.last_contacted_at=datetime.now(timezone.utc)
        results.append({"contact_id":contact.id,"phone":contact.phone_number,"message_id":message.id,"provider_message_id":r.get("provider_message_id",""),"status":"sent"if r["success"]else"failed","error":r.get("error")if not r["success"]else None,"api_response":r.get("raw")})

    await db.flush()
    sc=sum(1 for x in results if x["status"]=="sent")
    return{"success":sc>0,"sent":sc,"failed":len(results)-sc,"total":len(results),"char_count":char_count,"segments":segment_count,"results":results,"sim_used":sim}

@router.get("/scheduled")
async def get_scheduled(page:int=1,per_page:int=50,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    q=select(ScheduledMessage).order_by(ScheduledMessage.schedule_at.desc()).offset((page-1)*per_page).limit(per_page)
    items=(await db.execute(q)).scalars().all()
    return{"items":[{"id":s.id,"phone":s.phone_number,"body":s.body[:100],"schedule_at":s.schedule_at.isoformat()if s.schedule_at else None,"status":s.status,"error":s.error,"executed_at":s.executed_at.isoformat()if s.executed_at else None}for s in items]}

@router.post("/scheduled/{sid}/cancel")
async def cancel_scheduled(sid:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(ScheduledMessage).where(ScheduledMessage.id==sid));s=r.scalar_one_or_none()
    if not s:raise HTTPException(404,"Not found")
    s.status="cancelled";await db.flush();return{"success":True}
