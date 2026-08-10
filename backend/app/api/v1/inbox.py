"""Inbox API — full SMS sync with proper chat threading."""
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

@router.get("/conversations")
async def list_conversations(page:int=1,per_page:int=500,status:Optional[str]=None,search:Optional[str]=None,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    query = select(Conversation)
    if status: query = query.where(Conversation.status == status)
    if search: query = query.join(Contact, Conversation.contact_id == Contact.id).where(or_(Contact.first_name.ilike(f"%{search}%"),Contact.last_name.ilike(f"%{search}%"),Contact.business_name.ilike(f"%{search}%"),Contact.phone_number.ilike(f"%{search}%"),Conversation.last_message_preview.ilike(f"%{search}%")))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    query = query.order_by(Conversation.last_message_at.desc().nullslast()).offset((page-1)*per_page).limit(per_page)
    items = []
    for conv in (await db.execute(query)).scalars().all():
        cr = await db.execute(select(Contact).where(Contact.id == conv.contact_id)); contact = cr.scalar_one_or_none()
        name = (f"{contact.first_name or ''} {contact.last_name or ''}".strip() or contact.business_name or contact.phone_number) if contact else "Unknown"
        items.append({"id":conv.id,"contact_id":conv.contact_id,"contact_name":name,"contact_phone":contact.phone_number if contact else"","contact_lead_status":contact.lead_status if contact else"","status":conv.status,"message_count":conv.message_count,"unread_count":conv.unread_count,"last_message_preview":conv.last_message_preview,"last_message_at":conv.last_message_at.isoformat()if conv.last_message_at else None,"created_at":conv.created_at.isoformat(),"contact":{"phone_number":contact.phone_number if contact else"","lead_status":contact.lead_status if contact else"","business_name":contact.business_name if contact else"","first_name":contact.first_name if contact else"","last_name":contact.last_name if contact else"","city":contact.city if contact else"","state":contact.state if contact else""}if contact else None})
    return {"total":total,"items":items}

@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404)
    conv.status="read";conv.unread_count=0
    cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));contact=cr.scalar_one_or_none()
    mr=await db.execute(select(Message).where(Message.conversation_id==conversation_id).order_by(Message.created_at.asc()))
    await db.flush()
    return {"id":conv.id,"contact":{"id":contact.id if contact else None,"phone_number":contact.phone_number if contact else"","first_name":contact.first_name if contact else"","last_name":contact.last_name if contact else"","business_name":contact.business_name if contact else"","lead_status":contact.lead_status if contact else"","city":contact.city if contact else"","state":contact.state if contact else"","email":contact.email if contact else"","website":contact.website if contact else"","notes":contact.notes if contact else""}if contact else None,"status":conv.status,"sequence_paused":conv.sequence_paused,"messages":[{"id":m.id,"direction":m.direction,"body":m.body,"status":m.status,"created_at":m.created_at.isoformat(),"sent_at":m.sent_at.isoformat()if m.sent_at else None,"delivered_at":m.delivered_at.isoformat()if m.delivered_at else None,"provider_message_id":m.provider_message_id}for m in mr.scalars().all()]}

@router.post("/conversations/{conversation_id}/reply")
async def send_reply(conversation_id:int,body:str=Query(...,min_length=1),db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404)
    from app.services.sms_service import SMSService
    msg=await SMSService(db).send_message(contact_id=conv.contact_id,body=body)
    if not msg: raise HTTPException(500,"Gateway not configured")
    await db.flush();return{"success":True,"message_id":msg.id,"status":msg.status}

@router.post("/conversations/{conversation_id}/mark-interested")
async def mark_interested(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404);conv.status="interested"
    cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));c=cr.scalar_one_or_none()
    if c:c.lead_status="interested";await db.flush();return{"success":True}

@router.post("/conversations/{conversation_id}/mark-not-interested")
async def mark_not_interested(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404);conv.status="not_interested"
    cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));c=cr.scalar_one_or_none()
    if c:c.lead_status="not_interested";await db.flush();return{"success":True}

@router.post("/conversations/{conversation_id}/mark-close")
async def close_conversation(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404);conv.status="closed"
    cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));c=cr.scalar_one_or_none()
    if c:c.lead_status="closed";await db.flush();return{"success":True}

@router.post("/conversations/{conversation_id}/stop-sequence")
async def stop_seq(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404);conv.sequence_paused=True
    from app.services.sms_service import SMSService;await SMSService(db)._stop_contact_sequences(conv.contact_id)
    await db.flush();return{"success":True}

@router.post("/conversations/{conversation_id}/resume-sequence")
async def resume_seq(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404);conv.sequence_paused=False;await db.flush();return{"success":True}

# ======================== SMART FULL SYNC ========================

@router.post("/sync-full")
async def sync_full_inbox(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    """
    FULL inbox sync: pulls EVERY message from SMS-Gate.app and imports them.
    Figures out direction by comparing phone numbers against known outgoing messages.
    Threads everything into proper conversations with correct timestamps.
    """
    import httpx, uuid
    u=(settings.SMSGATE_USERNAME or"").strip();p=(settings.SMSGATE_PASSWORD or"").strip()
    if not u or not p: return{"success":False,"error":"Credentials not set"}

    auth=base64.b64encode(f"{u}:{p}".encode()).decode()
    headers={"Content-Type":"application/json","Authorization":f"Basic {auth}"}
    base="https://api.sms-gate.app/3rdparty/v1"

    # Step 1: Fetch ALL messages from SMS-Gate.app
    # Fetch messages with proper pagination and fallback
    all_msgs = []
    raw_response_debug = {}
    max_limit = 50  # API max limit
    for attempt in [max_limit, 25, 10]:  # try smaller limits if max fails
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60)) as client:
                r=await client.get(f"{base}/messages?limit={attempt}",headers=headers)
                raw_response_debug = {"http":r.status_code,"body_len":len(r.text),"body_preview":r.text[:1000]}
                if r.status_code==200:
                    raw=r.json()
                    raw_response_debug["json_type"]=type(raw).__name__
                    if isinstance(raw,list):
                        all_msgs=raw
                        raw_response_debug["is_list"]=True
                    elif isinstance(raw,dict):
                        raw_response_debug["top_keys"]=list(raw.keys())
                        # Try many envelope keys (different API versions use different names)
                        for key in ["messages","data","items","content","results","records","messageList","list","entries"]:
                            val=raw.get(key)
                            if isinstance(val,list):
                                all_msgs=val
                                raw_response_debug["envelope_key"]=key
                                break
                        if not all_msgs:
                            # Maybe the whole dict IS the messages keyed by ID
                            all_msgs=list(raw.values()) if all(isinstance(v,dict) for v in raw.values()) else []
                            if all_msgs:
                                raw_response_debug["used_values_as_messages"]=True
                    break
                elif r.status_code==400:
                    if attempt==10:
                        return{"success":False,"error":"HTTP 400 — limit validation failed","debug":raw_response_debug}
                    continue
                else:
                    return{"success":False,"error":f"HTTP {r.status_code}","debug":raw_response_debug}
        except Exception as e:
            if attempt==10:
                return{"success":False,"error":str(e)[:300],"debug":raw_response_debug}
    if not all_msgs:
        return{"success":True,"total_api_messages":0,"note":"No messages returned from API","debug":raw_response_debug}

    logger.info(f"SYNC: fetched {len(all_msgs)} messages from API. Response debug: {raw_response_debug}")

    # Step 2: Build a set of phone numbers WE sent to (outgoing recipients)
    outgoing_phones=set()
    known_provider_ids=set()
    sent_msgs=await db.execute(select(Message).where(Message.direction=="outgoing",Message.provider_message_id.isnot(None)))
    for m in sent_msgs.scalars().all():
        known_provider_ids.add(m.provider_message_id)

    # Also track which phones we've sent to
    sent_contacts=await db.execute(select(Contact).where(Contact.messages_sent>0))
    for c in sent_contacts.scalars().all():
        outgoing_phones.add(c.phone_number)

    stats={"total_api_messages":len(all_msgs),"outgoing_updated":0,"new_inbound":0,"new_contacts":0,"new_conversations":0,"skipped":0,"details":[]}

    # Step 3: Process each API message
    unhandled_count = 0
    for msg_idx, msg in enumerate(all_msgs):
        if not isinstance(msg, dict):
            logger.warning(f"SYNC: msg[{msg_idx}] is not a dict: {type(msg)}")
            unhandled_count += 1
            continue

        mid=str(msg.get("id")or msg.get("messageId",""))
        state_raw=(msg.get("state")or"").lower()
        device_id=str(msg.get("deviceId",""))
        recipients=msg.get("recipients",[])
        states_dict=msg.get("states",{}) or {}
        text_body=str(msg.get("text")or msg.get("body")or msg.get("message",""))
        sender=str(msg.get("sender")or msg.get("from",""))

        # Normalize recipients to always be a list of dicts
        if not isinstance(recipients, list):
            recipients = []

        # Extract best timestamp
        ts=None
        for t_str in states_dict.values():
            try:ts=dt.fromisoformat(str(t_str).replace("Z","+00:00"));break
            except:pass
        if not ts:
            for ts_field in ["createdAt","created_at","timestamp","receivedAt","received_at","sentAt","sent_at","updatedAt"]:
                try:ts=dt.fromisoformat(str(msg.get(ts_field,"")).replace("Z","+00:00"));break
                except:pass
        if not ts:ts=dt.now(tz.utc)

        handled = False

        # --- CASE 1: Known outgoing message (update status) ---
        if mid and mid in known_provider_ids:
            status_map={"delivered":"delivered","failed":"failed","sent":"sent","processed":"sent","pending":"queued","sending":"sent"}
            ns=status_map.get(state_raw,state_raw)
            mr=await db.execute(select(Message).where(Message.provider_message_id==mid))
            existing=mr.scalar_one_or_none()
            if existing and existing.status!=ns:
                existing.status=ns
                if ns=="delivered":existing.delivered_at=dt.now(tz.utc)
                elif ns=="failed":existing.failed_at=dt.now(tz.utc)
                stats["outgoing_updated"]+=1
                stats["details"].append({"type":"status_update","id":mid,"new_status":ns})
            handled = True
            continue

        # --- CASE 2: Has sender + text → real inbound message ---
        if sender and text_body and text_body.strip():
            norm=normalize_nigerian_number(sender)
            if norm:
                idem=f"inbound-{mid}" if mid else f"inbound-{uuid.uuid4()}"
                dup=await db.execute(select(Message).where(Message.idempotency_key==idem))
                if not dup.scalar_one_or_none():
                    cr=await db.execute(select(Contact).where(Contact.phone_number==norm))
                    contact=cr.scalar_one_or_none()
                    if not contact:
                        contact=Contact(phone_number=norm,country="Nigeria",lead_status="new",source="inbound_sync")
                        db.add(contact);await db.flush();stats["new_contacts"]+=1

                    conv_r=await db.execute(select(Conversation).where(Conversation.contact_id==contact.id))
                    conv=conv_r.scalar_one_or_none()
                    if not conv:
                        conv=Conversation(contact_id=contact.id,status="unread")
                        db.add(conv);await db.flush();stats["new_conversations"]+=1
                    else:
                        conv.status="unread";conv.unread_count=(conv.unread_count or 0)+1

                    m=Message(conversation_id=conv.id,contact_id=contact.id,direction="incoming",
                        body=text_body[:1000],segment_count=1,char_count=len(text_body[:1000]),
                        status="delivered",provider="smsgate",provider_message_id=mid or None,
                        idempotency_key=idem,delivered_at=ts,created_at=ts)
                    db.add(m);await db.flush()
                    conv.message_count=(conv.message_count or 0)+1
                    conv.last_message_preview=text_body[:100];conv.last_message_at=ts
                    contact.messages_received=(contact.messages_received or 0)+1
                    contact.last_reply_at=ts
                    if contact.lead_status=="new":contact.lead_status="replied"
                    stats["new_inbound"]+=1
                    stats["details"].append({"type":"real_inbound","from":sender,"text":text_body[:60],"time":ts.isoformat()})
                else:
                    stats["skipped"]+=1
                handled = True
                continue

        # --- CASE 3: Process recipients list ---
        if recipients:
            for recipient in recipients:
                # Handle both dict and string recipients
                if isinstance(recipient, dict):
                    phone=str(recipient.get("phoneNumber") or recipient.get("phone") or recipient.get("number") or "").strip()
                    rstate=(recipient.get("state")or"").lower()
                elif isinstance(recipient, str):
                    phone=recipient.strip()
                    rstate=state_raw
                else:
                    continue

                if not phone: continue
                norm=normalize_nigerian_number(phone)
                if not norm: continue

                idem=f"sync-{mid}-{norm}" if mid else f"sync-{uuid.uuid4()}-{norm}"
                dup=await db.execute(select(Message).where(Message.idempotency_key==idem))
                if dup.scalar_one_or_none():
                    stats["skipped"]+=1;continue

                cr=await db.execute(select(Contact).where(Contact.phone_number==norm))
                contact=cr.scalar_one_or_none()
                if not contact:
                    contact=Contact(phone_number=norm,country="Nigeria",lead_status="new",source="inbound_sync")
                    db.add(contact);await db.flush();stats["new_contacts"]+=1

                conv_r=await db.execute(select(Conversation).where(Conversation.contact_id==contact.id))
                conv=conv_r.scalar_one_or_none()
                if not conv:
                    conv=Conversation(contact_id=contact.id,status="unread")
                    db.add(conv);await db.flush();stats["new_conversations"]+=1
                else:
                    conv.status="unread";conv.unread_count=(conv.unread_count or 0)+1

                # Build best body from available data
                if text_body and text_body.strip():
                    body=text_body[:1000]
                else:
                    body=f"[SMS via device {device_id[:8]}]" if device_id else "[SMS message]"

                m=Message(conversation_id=conv.id,contact_id=contact.id,
                    direction="incoming",body=body,segment_count=1,
                    char_count=len(body),status="delivered",provider="smsgate",
                    provider_message_id=mid or None,idempotency_key=idem,
                    delivered_at=ts,created_at=ts)
                db.add(m);await db.flush()
                conv.message_count=(conv.message_count or 0)+1
                conv.last_message_preview=body[:100];conv.last_message_at=ts
                contact.messages_received=(contact.messages_received or 0)+1
                contact.last_reply_at=ts
                if contact.lead_status=="new":contact.lead_status="replied"
                stats["new_inbound"]+=1
                stats["details"].append({"type":"recipient_sync","phone":norm,"device":device_id[:12],"state":rstate,"time":ts.isoformat()})
            handled = True
            continue

        # --- CASE 4: Catch-all — message has data we haven't handled ---
        # Try to extract ANY phone number and ANY text from the message
        if not handled:
            # Try all possible phone fields
            possible_phones = []
            for pf in ["phoneNumber","phone","number","from","sender","to","recipient","address"]:
                val = msg.get(pf,"")
                if val and str(val).strip():
                    possible_phones.append(str(val).strip())

            # Try phone from single recipient
            if not possible_phones and isinstance(recipients, list) and len(recipients)==0:
                # Already checked, no recipients
                pass

            # Try phone from nested objects
            if not possible_phones:
                for pf in ["payload","data","message","sms"]:
                    nested = msg.get(pf)
                    if isinstance(nested, dict):
                        for nf in ["phoneNumber","phone","from","sender","to"]:
                            nv = nested.get(nf,"")
                            if nv:
                                possible_phones.append(str(nv).strip())

            if possible_phones:
                phone = possible_phones[0]
                norm = normalize_nigerian_number(phone)
                if norm:
                    idem = f"catchall-{mid}-{norm}" if mid else f"catchall-{uuid.uuid4()}-{norm}"
                    dup = await db.execute(select(Message).where(Message.idempotency_key==idem))
                    if not dup.scalar_one_or_none():
                        cr = await db.execute(select(Contact).where(Contact.phone_number==norm))
                        contact = cr.scalar_one_or_none()
                        if not contact:
                            contact = Contact(phone_number=norm, country="Nigeria", lead_status="new", source="inbound_sync")
                            db.add(contact); await db.flush(); stats["new_contacts"] += 1

                        conv_r = await db.execute(select(Conversation).where(Conversation.contact_id==contact.id))
                        conv = conv_r.scalar_one_or_none()
                        if not conv:
                            conv = Conversation(contact_id=contact.id, status="unread")
                            db.add(conv); await db.flush(); stats["new_conversations"] += 1
                        else:
                            conv.status = "unread"; conv.unread_count = (conv.unread_count or 0) + 1

                        body = text_body or f"[SMS via device {device_id[:8]}]" if device_id else "[SMS message]"
                        m = Message(conversation_id=conv.id, contact_id=contact.id,
                            direction="incoming", body=body[:1000], segment_count=1,
                            char_count=len(body[:1000]), status="delivered", provider="smsgate",
                            provider_message_id=mid or None, idempotency_key=idem,
                            delivered_at=ts, created_at=ts)
                        db.add(m); await db.flush()
                        conv.message_count = (conv.message_count or 0) + 1
                        conv.last_message_preview = body[:100]; conv.last_message_at = ts
                        contact.messages_received = (contact.messages_received or 0) + 1
                        contact.last_reply_at = ts
                        if contact.lead_status == "new": contact.lead_status = "replied"
                        stats["new_inbound"] += 1
                        stats["details"].append({"type":"catchall","phone":norm,"time":ts.isoformat()})
                    else:
                        stats["skipped"] += 1
                    handled = True

        if not handled:
            unhandled_count += 1
            logger.warning(f"SYNC: unhandled msg[{msg_idx}] keys={sorted(msg.keys())} id={mid[:40]}")

    await db.flush()

    if unhandled_count:
        stats["unhandled"] = unhandled_count
        stats["note"] = f"Full sync: {stats['new_inbound']} inbound, {stats['outgoing_updated']} updated, {unhandled_count} unhandled. Use /poll-debug to inspect raw API data."

    # Auto-register webhook
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
            await client.post(f"{base}/webhooks",headers=headers,json={"url":"https://sendsms-api.onrender.com/api/v1/webhooks/smsgateway","events":["sms:received","sms:delivered","sms:sent","sms:failed"]})
        stats["webhook_registered"]=True
    except:stats["webhook_registered"]=False

    stats["webhook_url"]="https://sendsms-api.onrender.com/api/v1/webhooks/smsgateway"
    if not stats.get("note"):
        stats["note"]="Full sync complete. Click Poll Now to update, or configure webhook for instant delivery."
    stats["debug"] = raw_response_debug

    return{"success":True,**stats}

@router.post("/poll-debug")
async def poll_debug(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    """
    DEBUG endpoint: returns RAW SMS-Gate.app API response + processing trace.
    Shows exactly what the API returned and why each message was/wasn't processed.
    """
    import httpx
    u=(settings.SMSGATE_USERNAME or"").strip();p=(settings.SMSGATE_PASSWORD or"").strip()
    if not u or not p: return{"success":False,"error":"Credentials not set"}

    auth=base64.b64encode(f"{u}:{p}".encode()).decode()
    headers={"Content-Type":"application/json","Authorization":f"Basic {auth}"}
    base="https://api.sms-gate.app/3rdparty/v1"

    raw_responses={}
    all_msgs=[]

    # Try multiple limits and show raw response for each
    for limit in [50,25,10,5]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
                r=await client.get(f"{base}/messages?limit={limit}",headers=headers)
                raw_responses[f"limit_{limit}"]={
                    "http":r.status_code,
                    "headers":dict(r.headers),
                    "body_preview":r.text[:2000],
                    "body_length":len(r.text)
                }
                if r.status_code==200:
                    data=r.json()
                    raw_responses[f"limit_{limit}"]["json_type"]=type(data).__name__
                    if isinstance(data,list):
                        raw_responses[f"limit_{limit}"]["is_list"]=True
                        raw_responses[f"limit_{limit}"]["count"]=len(data)
                        all_msgs=data
                    elif isinstance(data,dict):
                        raw_responses[f"limit_{limit}"]["top_keys"]=list(data.keys())
                        # Try many possible envelope keys
                        for key in ["messages","data","items","content","results","records","messageList"]:
                            if key in data:
                                raw_responses[f"limit_{limit}"][f"envelope_{key}"]=len(data[key])
                        all_msgs=data.get("messages",data.get("data",data.get("items",data.get("content",data.get("results",[])))))
                    break
        except Exception as e:
            raw_responses[f"limit_{limit}"]={"error":str(e)[:500]}

    # Show first 2 messages in full
    sample_msgs=[]
    for msg in all_msgs[:2]:
        sample_msgs.append({
            "keys":sorted(msg.keys()) if isinstance(msg,dict) else "NOT_A_DICT",
            "full":{k:(str(v)[:200] if not isinstance(v,(list,dict)) else (f"list[{len(v)}]" if isinstance(v,list) else f"dict{list(v.keys())[:5]}")) for k,v in (msg.items() if isinstance(msg,dict) else [])}
        })

    # Now trace processing of each message
    trace=[]
    known_provider_ids=set()
    sent_msgs=await db.execute(select(Message).where(Message.direction=="outgoing",Message.provider_message_id.isnot(None)))
    for m in sent_msgs.scalars().all():
        known_provider_ids.add(m.provider_message_id)

    for i,msg in enumerate(all_msgs[:5]):  # trace first 5
        if not isinstance(msg,dict):
            trace.append({"index":i,"error":"not a dict","type":type(msg).__name__})
            continue
        mid=msg.get("id") or msg.get("messageId","")
        sender=msg.get("sender") or msg.get("from") or""
        text_body=msg.get("text") or msg.get("body") or msg.get("message","")
        recipients=msg.get("recipients",[])
        state=msg.get("state","")

        entry={
            "index":i,
            "id":str(mid)[:50],
            "state":state,
            "has_sender":bool(sender),
            "has_text":bool(text_body),
            "recipients_count":len(recipients) if isinstance(recipients,list) else f"not_list({type(recipients).__name__})",
            "in_known_ids":mid in known_provider_ids,
        }

        # Show first recipient format
        if isinstance(recipients,list) and recipients:
            r0=recipients[0]
            entry["first_recipient_type"]=type(r0).__name__
            if isinstance(r0,dict):
                entry["first_recipient_keys"]=sorted(r0.keys())
                entry["first_recipient_phone"]=str(r0.get("phoneNumber",r0.get("phone","MISSING")))
            elif isinstance(r0,str):
                entry["first_recipient_value"]=r0

        # What case would match?
        if mid in known_provider_ids:
            entry["would_match"]="CASE1_outgoing_update"
        elif sender and text_body and text_body.strip():
            entry["would_match"]="CASE2_real_inbound"
        elif isinstance(recipients,list) and recipients:
            # Check if phone numbers would normalize
            r0=recipients[0]
            phone=None
            if isinstance(r0,dict):
                phone=r0.get("phoneNumber",r0.get("phone",""))
            elif isinstance(r0,str):
                phone=r0
            if phone:
                norm=normalize_nigerian_number(str(phone).strip())
                entry["phone_sample"]=str(phone)[:20]
                entry["normalizes"]=norm is not None
                entry["normalized"]=norm
                entry["would_match"]="CASE3_recipients"
            else:
                entry["would_match"]="CASE3_but_no_phone"
        else:
            entry["would_match"]="NONE_all_zero"

        trace.append(entry)

    return{
        "success":True,
        "raw_responses":raw_responses,
        "total_api_messages":len(all_msgs),
        "sample_messages":sample_msgs,
        "known_outgoing_ids_count":len(known_provider_ids),
        "known_outgoing_ids_sample":list(known_provider_ids)[:5],
        "processing_trace":trace,
        "note":"Use this to diagnose why messages aren't being imported. Check 'would_match' and 'normalizes' fields."
    }

@router.post("/poll-now")
async def poll_now(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    """Lightweight incremental poll — calls sync-full internally."""
    return await sync_full_inbox(db,cu)
