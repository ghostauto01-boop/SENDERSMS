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

# ======================== REAL SYNC: INBOX EXPORT + WEBHOOKS ========================
# SMS-Gate.app's GET /messages does NOT return text/sender for received SMS.
# The correct receive path is:
#   1. Register webhook for "sms:received"
#   2. Call POST /messages/inbox/export with deviceId + time range
#   3. The device pushes ALL messages as webhooks to your registered URL
#   4. Each webhook has FULL content: {payload: {messageId, message, sender, ...}}

import os
DEVICE_ID_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "..", ".device_id")

def _get_cached_device_id():
    try:
        with open(DEVICE_ID_FILE) as f:
            return f.read().strip()
    except: return ""

def _cache_device_id(device_id: str):
    try:
        with open(DEVICE_ID_FILE, "w") as f:
            f.write(device_id)
    except: pass

@router.get("/device-info")
async def get_device_info():
    """Get connected devices from SMS-Gate.app."""
    from app.providers.smsgate import get_devices_direct
    devices = await get_devices_direct()
    cached = _get_cached_device_id()
    return {
        "success": True,
        "devices": devices,
        "cached_device_id": cached,
        "device_count": len(devices),
    }

@router.post("/sync-full")
async def sync_full_inbox(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    """
    SMART sync using the CORRECT SMS-Gate.app receive mechanism:
    
    1. Get device ID from GET /devices (cache it to .device_id file)
    2. Register webhook for sms:received event
    3. Call POST /messages/inbox/export to trigger device to push all messages as webhooks
    4. Also poll GET /messages for outgoing status updates
    
    Inbound messages arrive via webhook at /api/v1/webhooks/smsgateway
    with FULL content: sender, message text, timestamp, simNumber.
    """
    import httpx
    from app.providers.smsgate import get_devices_direct, export_inbox_direct, poll_status_for_ids, register_webhook_direct

    stats = {
        "success": True,
        "device": None,
        "export_triggered": False,
        "export_error": None,
        "webhook_registered": False,
        "outgoing_updated": 0,
        "note": "",
        "details": [],
    }

    # Step 1: Get device ID
    devices = await get_devices_direct()
    device_id = None

    if devices:
        device_id = devices[0].get("id") or devices[0].get("deviceId", "")
        if device_id:
            _cache_device_id(device_id)
    else:
        device_id = _get_cached_device_id()

    if not device_id:
        stats["success"] = False
        stats["error"] = "No device found. Make sure your Android phone is connected to SMS-Gate.app cloud."
        stats["note"] = "Check the SMS-Gate.app app on your phone — it must be online and connected."
        return stats

    stats["device"] = device_id[:16] + "..."

    # Step 2: Register webhook
    wh_result = await register_webhook_direct("https://sendsms-api.onrender.com/api/v1/webhooks/smsgateway")
    stats["webhook_registered"] = wh_result.get("success", False)

    # Step 3: Trigger inbox export — this makes the device push ALL messages as webhooks
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    since = (_dt.now(_tz.utc) - _td(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    until = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    export_result = await export_inbox_direct(device_id, since=since, until=until)
    stats["export_triggered"] = export_result.get("success", False)
    stats["export_http"] = export_result.get("http")
    stats["export_response"] = export_result.get("body", export_result.get("error", ""))[:300]

    if export_result.get("success"):
        stats["note"] = (
            "✅ Inbox export triggered! Your Android device is now pushing all SMS messages "
            "to the webhook. Messages will appear in your inbox within seconds. "
            "Send a test SMS to your phone to verify."
        )
    else:
        stats["note"] = (
            f"⚠️ Inbox export returned HTTP {stats['export_http']}. "
            "The webhook is registered — new incoming SMS will arrive automatically. "
            "Try sending an SMS to your phone now to test."
        )

    # Step 4: Also poll outgoing statuses as a bonus
    try:
        from app.models.conversation import Message
        sent_msgs = await db.execute(
            select(Message).where(
                Message.direction == "outgoing",
                Message.provider_message_id.isnot(None),
                Message.status.in_(("sent", "sending", "queued"))
            ).limit(100)
        )
        ids = [m.provider_message_id for m in sent_msgs.scalars().all()]
        if ids:
            statuses = await poll_status_for_ids(ids)
            for r in statuses:
                mr = await db.execute(select(Message).where(Message.provider_message_id == r["provider_message_id"]))
                m = mr.scalar_one_or_none()
                if m and m.status != r["status"]:
                    m.status = r["status"]
                    if r["status"] == "delivered":
                        m.delivered_at = dt.now(tz.utc)
                    stats["outgoing_updated"] += 1
                    stats["details"].append({"type": "status_update", "id": r["provider_message_id"][:20], "new_status": r["status"]})
    except Exception as e:
        logger.warning(f"Status poll error: {e}")

    await db.flush()

    stats["webhook_url"] = "https://sendsms-api.onrender.com/api/v1/webhooks/smsgateway"
    stats["total_api_messages"] = 0  # Inbound comes via webhooks, not this poll

    return stats

@router.post("/poll-debug")
async def poll_debug(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    """
    DEBUG endpoint: tests device connectivity, webhook status, and shows raw API responses.
    """
    import httpx
    from app.providers.smsgate import get_devices_direct, register_webhook_direct, list_webhooks_direct

    result = {
        "success": True,
        "devices": None,
        "webhooks": None,
        "webhook_registration": None,
        "inbox_export_test": None,
        "messages_poll": None,
        "note": "",
    }

    # Get devices
    devices = await get_devices_direct()
    result["devices"] = {
        "count": len(devices),
        "list": devices[:5] if devices else [],
    }
    if devices:
        device_id = devices[0].get("id") or devices[0].get("deviceId", "")
        result["device_id"] = device_id
        _cache_device_id(device_id)
    else:
        result["device_id"] = _get_cached_device_id()

    # Get webhooks
    wh = await list_webhooks_direct()
    result["webhooks"] = wh

    # Register webhook
    reg = await register_webhook_direct("https://sendsms-api.onrender.com/api/v1/webhooks/smsgateway")
    result["webhook_registration"] = reg

    # Test messages poll (for outgoing statuses)
    u = (settings.SMSGATE_USERNAME or "").strip()
    p = (settings.SMSGATE_PASSWORD or "").strip()
    if u and p:
        auth = base64.b64encode(f"{u}:{p}".encode()).decode()
        headers = {"Content-Type": "application/json", "Authorization": f"Basic {auth}"}
        base = "https://api.sms-gate.app/3rdparty/v1"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
                r = await client.get(f"{base}/messages?limit=10", headers=headers)
                result["messages_poll"] = {
                    "http": r.status_code,
                    "body_preview": r.text[:1000],
                    "body_length": len(r.text),
                }
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list):
                        result["messages_poll"]["count"] = len(data)
                        if data:
                            result["messages_poll"]["first_keys"] = sorted(data[0].keys()) if isinstance(data[0], dict) else str(type(data[0]))
                            result["messages_poll"]["first_message"] = {k: str(v)[:100] for k, v in (data[0].items() if isinstance(data[0], dict) else [])}
        except Exception as e:
            result["messages_poll"] = {"error": str(e)[:500]}

    # Summary
    issues = []
    if not devices:
        issues.append("NO DEVICES: Your Android phone is not connected. Open SMS-Gate.app app → check it's online.")
    if not reg.get("success"):
        issues.append("WEBHOOK NOT REGISTERED: Check credentials.")
    if issues:
        result["note"] = " | ".join(issues)
    else:
        result["note"] = "Everything looks good. Webhook is registered and device is connected. Incoming SMS will arrive via webhook at /api/v1/webhooks/smsgateway"

    return result

@router.post("/poll-now")
async def poll_now(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    """Sync inbox — triggers inbox export + status poll."""
    return await sync_full_inbox(db, cu)
