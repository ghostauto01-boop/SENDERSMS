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
from app.utils.naming import contact_display_name
from datetime import datetime as dt, timezone as tz

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/conversations")
async def list_conversations(page:int=1,per_page:int=500,status:Optional[str]=None,search:Optional[str]=None,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    query = select(Conversation)
    # "all" is what the UI sends for the default tab; treat it as no filter
    # rather than as a literal status nothing will ever match.
    if status and status != "all":
        if status == "unread":
            query = query.where(or_(Conversation.status == "unread", Conversation.unread_count > 0))
        else:
            query = query.where(Conversation.status == status)
    if search: query = query.join(Contact, Conversation.contact_id == Contact.id).where(or_(Contact.first_name.ilike(f"%{search}%"),Contact.last_name.ilike(f"%{search}%"),Contact.business_name.ilike(f"%{search}%"),Contact.phone_number.ilike(f"%{search}%"),Conversation.last_message_preview.ilike(f"%{search}%")))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    query = query.order_by(Conversation.last_message_at.desc().nullslast()).offset((page-1)*per_page).limit(per_page)
    items = []
    for conv in (await db.execute(query)).scalars().all():
        cr = await db.execute(select(Contact).where(Contact.id == conv.contact_id)); contact = cr.scalar_one_or_none()
        name = contact_display_name(contact)
        items.append({"id":conv.id,"contact_id":conv.contact_id,"contact_name":name,"contact_phone":contact.phone_number if contact else"","contact_lead_status":contact.lead_status if contact else"","status":conv.status,"message_count":conv.message_count,"unread_count":conv.unread_count,"last_message_preview":conv.last_message_preview,"last_message_at":conv.last_message_at.isoformat()if conv.last_message_at else None,"created_at":conv.created_at.isoformat(),"contact":{"phone_number":contact.phone_number if contact else"","lead_status":contact.lead_status if contact else"","business_name":contact.business_name if contact else"","first_name":contact.first_name if contact else"","last_name":contact.last_name if contact else"","city":contact.city if contact else"","state":contact.state if contact else""}if contact else None})
    return {"total":total,"items":items}

@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    r=await db.execute(select(Conversation).where(Conversation.id==conversation_id));conv=r.scalar_one_or_none()
    if not conv: raise HTTPException(404)
    # Opening a thread clears the unread badge, but must NOT overwrite a
    # deliberate label. Previously every read reset status to "read", so
    # Interested/Not interested/Closed vanished the moment you clicked in,
    # and Mark unread was undone by the very next poll.
    conv.unread_count=0
    if conv.status in(None,"","unread"):conv.status="read"
    cr=await db.execute(select(Contact).where(Contact.id==conv.contact_id));contact=cr.scalar_one_or_none()
    mr=await db.execute(select(Message).where(Message.conversation_id==conversation_id).order_by(Message.created_at.asc()))
    await db.flush()
    return {"id":conv.id,"contact":{"id":contact.id if contact else None,"phone_number":contact.phone_number if contact else"","first_name":contact.first_name if contact else"","last_name":contact.last_name if contact else"","business_name":contact.business_name if contact else"","lead_status":contact.lead_status if contact else"","city":contact.city if contact else"","state":contact.state if contact else"","email":contact.email if contact else"","website":contact.website if contact else"","notes":contact.notes if contact else""}if contact else None,"status":conv.status,"sequence_paused":conv.sequence_paused,"messages":[{"id":m.id,"direction":m.direction,"body":m.body,"status":m.status,"created_at":m.created_at.isoformat(),"sent_at":m.sent_at.isoformat()if m.sent_at else None,"delivered_at":m.delivered_at.isoformat()if m.delivered_at else None,"failed_at":m.failed_at.isoformat()if m.failed_at else None,"last_error":m.last_error,"segment_count":m.segment_count,"provider_message_id":m.provider_message_id}for m in mr.scalars().all()]}

@router.post("/conversations/{conversation_id}/reply")
async def send_reply(conversation_id:int,body:str=Query(...,min_length=1),db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    """Send an outbound reply in a conversation via SMS-Gate.

    Reports the true outcome: `send_message` returns None when the contact is
    opted out or suppressed, and returns a *failed* message when the gateway
    rejected it. Previously both cases looked like a success to the UI.
    """
    conv=await _get_conv(db,conversation_id)
    from app.services.sms_service import SMSService
    msg=await SMSService(db).send_message(contact_id=conv.contact_id,body=body)
    if not msg:
        raise HTTPException(409,"Cannot send: the contact has opted out or is on the suppression list.")
    await db.flush()
    if msg.status=="failed":
        return {"success":False,"message_id":msg.id,"status":msg.status,
                "error":msg.last_error or "The SMS gateway rejected the message."}
    return {"success":True,"message_id":msg.id,"status":msg.status,
            "provider_message_id":msg.provider_message_id}

async def _get_conv(db, conversation_id: int) -> Conversation:
    conv = (await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return conv


async def _set_status(db, conversation_id: int, conv_status: str, lead_status: str):
    """Update a conversation and mirror the outcome onto the contact."""
    conv = await _get_conv(db, conversation_id)
    conv.status = conv_status
    contact = (await db.execute(
        select(Contact).where(Contact.id == conv.contact_id)
    )).scalar_one_or_none()
    if contact:
        contact.lead_status = lead_status
    await db.flush()
    return {"success": True, "status": conv.status}


@router.post("/conversations/{conversation_id}/mark-interested")
async def mark_interested(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    return await _set_status(db, conversation_id, "interested", "interested")

@router.post("/conversations/{conversation_id}/mark-not-interested")
async def mark_not_interested(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    return await _set_status(db, conversation_id, "not_interested", "not_interested")

@router.post("/conversations/{conversation_id}/mark-close")
async def close_conversation(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    return await _set_status(db, conversation_id, "closed", "closed")

@router.post("/conversations/{conversation_id}/mark-unread")
async def mark_unread(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    conv = await _get_conv(db, conversation_id)
    conv.status = "unread"
    if not conv.unread_count:
        conv.unread_count = 1
    await db.flush()
    return {"success": True, "status": conv.status}

@router.post("/conversations/{conversation_id}/stop-sequence")
async def stop_seq(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    conv = await _get_conv(db, conversation_id)
    conv.sequence_paused = True
    from app.services.sms_service import SMSService
    await SMSService(db)._stop_seq(conv.contact_id)
    await db.flush()
    return {"success": True, "sequence_paused": True}

@router.post("/conversations/{conversation_id}/resume-sequence")
async def resume_seq(conversation_id:int,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    conv = await _get_conv(db, conversation_id)
    conv.sequence_paused = False
    await db.flush()
    return {"success": True, "sequence_paused": False}

# ======================== RECEIVE PATH ========================
# How receiving actually works on SMS-Gate.app (per their documentation):
#
#   Cloud mode has NO endpoint that returns the text of received SMS.
#   GET /messages only reports messages *you* sent. The only ways in are:
#     1. Register a webhook per event: POST /webhooks {"url", "event"}   <- singular!
#     2. Live SMS then arrive as POST {event, payload:{sender, message, ...}}
#     3. History is replayed by POST /messages/inbox/export {deviceId, since, until},
#        which makes the device re-fire sms:received webhooks for that window.
#
# So the webhook endpoint is not an optimisation, it is the entire receive path.
# https://docs.sms-gate.app/features/webhooks/
# https://docs.sms-gate.app/features/reading-messages/

DEVICE_ID_KEY = "gateway.device_id"
LAST_EXPORT_KEY = "gateway.last_inbox_export"


async def _resolve_device_id(db) -> tuple[str, bool]:
    """Return (device_id, is_live). Falls back to the last known id from the DB.

    Previously this was cached in a `.device_id` file next to the source, which
    is wiped on every redeploy and diverges between instances.
    """
    from app.providers.smsgate import get_devices_direct
    from app.services.system_settings import get_setting, set_setting

    devices = await get_devices_direct()
    for d in devices:
        if isinstance(d, dict):
            did = d.get("id") or d.get("deviceId") or ""
            if did:
                await set_setting(db, DEVICE_ID_KEY, did, description="Last seen SMS-Gate device")
                return did, True
    return (await get_setting(db, DEVICE_ID_KEY, "") or ""), False


@router.get("/device-info")
async def get_device_info(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    """Connected devices plus the currently registered webhooks."""
    from app.providers.smsgate import get_devices_direct, list_webhooks_direct
    from app.services.system_settings import get_setting
    from app.utils.urls import webhook_url

    devices = await get_devices_direct()
    wh = await list_webhooks_direct()
    target = webhook_url()
    hooks = wh.get("webhooks", [])
    return {
        "success": True,
        "devices": devices,
        "device_count": len(devices),
        "cached_device_id": await get_setting(db, DEVICE_ID_KEY, "") or "",
        "webhook_url": target,
        "registered_webhooks": hooks,
        "webhook_ok": bool(target) and any(
            isinstance(h, dict) and h.get("url") == target for h in hooks
        ),
    }


@router.post("/register-webhook")
async def register_webhook(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    """(Re)register this deployment's webhook URL for every event we consume."""
    from app.providers.smsgate import register_webhook_direct
    from app.services.system_settings import WEBHOOK_REGISTERED, set_setting
    from app.utils.urls import webhook_url

    url = webhook_url()
    if not url:
        raise HTTPException(400, "PUBLIC_BASE_URL is not set, so the gateway has no address to deliver to. Set it to this deployment's public https URL.")
    result = await register_webhook_direct(url)
    if result.get("success"):
        await set_setting(db, WEBHOOK_REGISTERED, url, description="Webhook URL registered with the SMS gateway")
        await db.flush()
    return result


@router.post("/sync-full")
async def sync_full_inbox(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    """Reconcile with the gateway: ensure webhooks exist, replay recent history,
    and refresh delivery statuses for anything still in flight.

    Inbound text never comes back through this call. It arrives asynchronously
    at /api/v1/webhooks/smsgateway, which is why the response reports what was
    *triggered* rather than what was fetched.
    """
    from datetime import timedelta as _td
    from app.providers.smsgate import (
        export_inbox_direct, poll_status_for_ids, register_webhook_direct,
    )
    from app.services.system_settings import WEBHOOK_REGISTERED, get_setting, set_setting
    from app.utils.urls import webhook_url

    target = webhook_url()
    stats = {
        "success": True,
        "webhook_url": target,
        "device": None,
        "device_online": False,
        "webhook_registered": False,
        "registered_events": [],
        "export_triggered": False,
        "outgoing_updated": 0,
        "new_inbound": 0,
        "total_api_messages": 0,
        "problems": [],
        "note": "",
        "details": [],
    }

    if not target:
        stats["success"] = False
        stats["problems"].append(
            "PUBLIC_BASE_URL is not set. Without a public URL the gateway cannot "
            "deliver inbound SMS, so the inbox will always stay empty."
        )
        stats["note"] = "Set PUBLIC_BASE_URL to this deployment's https URL, then sync again."
        return stats

    if not settings.SMSGATE_WEBHOOK_SECRET and not settings.SMSGATE_WEBHOOK_ALLOW_UNSIGNED:
        stats["problems"].append(
            "SMSGATE_WEBHOOK_SECRET is not set, so every inbound webhook is rejected "
            "with 503 before it reaches the inbox. Copy the signing key from the app: "
            "Settings -> Webhooks -> Signing Key."
        )

    # 1. Device
    device_id, live = await _resolve_device_id(db)
    stats["device_online"] = live
    if device_id:
        stats["device"] = device_id[:16] + "..."
    if not live:
        stats["problems"].append(
            "No device is currently reporting to SMS-Gate. Open the app on the phone "
            "and confirm it shows as online."
        )

    # 2. Webhooks — one registration per event.
    reg = await register_webhook_direct(target)
    stats["webhook_registered"] = reg.get("success", False)
    stats["registered_events"] = reg.get("registered", [])
    if reg.get("errors"):
        stats["problems"].extend(reg["errors"])
    if reg.get("success"):
        await set_setting(db, WEBHOOK_REGISTERED, target,
                          description="Webhook URL registered with the SMS gateway")

    # 3. Replay history so the chat backfills. Only from the last successful
    #    export (minus overlap) to avoid re-pushing the same month every click.
    if device_id:
        last = await get_setting(db, LAST_EXPORT_KEY, "")
        now = dt.now(tz.utc)
        since_dt = None
        if last:
            parsed = _parse_iso(last)
            if parsed:
                since_dt = parsed - _td(minutes=10)
        if since_dt is None:
            since_dt = now - _td(days=7)
        since_dt = max(since_dt, now - _td(days=30))

        export = await export_inbox_direct(
            device_id,
            since=since_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            until=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        stats["export_triggered"] = export.get("success", False)
        stats["export_http"] = export.get("http")
        stats["export_window"] = {"since": since_dt.isoformat(), "until": now.isoformat()}
        if export.get("success"):
            await set_setting(db, LAST_EXPORT_KEY, now.isoformat(),
                              description="Last inbox export watermark")
        else:
            stats["problems"].append(
                f"Inbox export failed (HTTP {export.get('http')}): {export.get('error','')[:200]}"
            )

    # 4. Refresh delivery statuses for outgoing messages still in flight.
    try:
        from app.services.sms_service import SMSService
        svc = SMSService(db)
        pending = await db.execute(
            select(Message).where(
                Message.direction == "outgoing",
                Message.provider_message_id.isnot(None),
                Message.status.in_(("sent", "sending", "queued")),
            ).limit(100)
        )
        ids = [m.provider_message_id for m in pending.scalars().all()]
        stats["total_api_messages"] = len(ids)
        if ids:
            for r in await poll_status_for_ids(ids):
                before = (await db.execute(
                    select(Message).where(Message.provider_message_id == r["provider_message_id"])
                )).scalar_one_or_none()
                prev = before.status if before else None
                m = await svc.process_delivery_status(r["provider_message_id"], r["status"])
                if m is not None and m.status != prev:
                    stats["outgoing_updated"] += 1
                    stats["details"].append({
                        "type": "status_update",
                        "id": r["provider_message_id"][:20],
                        "state": f"{prev} -> {m.status}",
                    })
    except Exception as e:
        logger.warning(f"Status poll error: {e}")
        stats["problems"].append(f"Status poll failed: {str(e)[:200]}")

    await db.flush()

    if stats["problems"]:
        stats["success"] = False
        stats["note"] = "Sync completed with problems — see the list below."
    elif stats["export_triggered"]:
        stats["note"] = (
            "Webhooks are registered and the phone is replaying recent messages. "
            "They land in the chat within a few seconds — no need to keep clicking."
        )
    else:
        stats["note"] = "Webhooks are registered. New incoming SMS will appear automatically."
    return stats


def _parse_iso(raw: str):
    try:
        d = dt.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d.replace(tzinfo=tz.utc) if d.tzinfo is None else d.astimezone(tz.utc)


@router.post("/poll-debug")
async def poll_debug(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    """End-to-end diagnostic of the receive path."""
    from app.providers.smsgate import get_devices_direct, list_webhooks_direct
    from app.models.webhook import WebhookEvent
    from app.utils.urls import webhook_url

    target = webhook_url()
    devices = await get_devices_direct()
    wh = await list_webhooks_direct()
    hooks = wh.get("webhooks", [])

    recent = (await db.execute(
        select(WebhookEvent).order_by(WebhookEvent.created_at.desc()).limit(10)
    )).scalars().all()
    inbound_count = (await db.execute(
        select(func.count()).select_from(Message).where(Message.direction == "incoming")
    )).scalar() or 0

    result = {
        "success": True,
        "webhook_url": target,
        "devices": {"count": len(devices), "list": devices[:5]},
        "registered_webhooks": hooks,
        "matching_events": sorted(
            h.get("event", "") for h in hooks
            if isinstance(h, dict) and h.get("url") == target
        ),
        "stored_inbound_messages": inbound_count,
        "recent_webhook_events": [
            {
                "event_type": e.event_type,
                "status": e.status,
                "error": e.error,
                "at": e.created_at.isoformat(),
            } for e in recent
        ],
        "config": {
            "public_base_url_set": bool(target),
            "signing_secret_set": bool(settings.SMSGATE_WEBHOOK_SECRET),
            "allow_unsigned": settings.SMSGATE_WEBHOOK_ALLOW_UNSIGNED,
            "credentials_set": settings.smsgate_configured,
        },
    }

    issues = []
    if not target:
        issues.append("PUBLIC_BASE_URL is not set — the gateway has nowhere to deliver to.")
    if not settings.smsgate_configured:
        issues.append("SMS-Gate credentials are missing.")
    if not devices:
        issues.append("No device online — open SMS-Gate on the phone.")
    if target and not result["matching_events"]:
        issues.append("No webhook registered for this URL — click Sync to register.")
    if not settings.SMSGATE_WEBHOOK_SECRET and not settings.SMSGATE_WEBHOOK_ALLOW_UNSIGNED:
        issues.append(
            "SMSGATE_WEBHOOK_SECRET is not set — inbound webhooks are rejected with 503."
        )
    if not recent:
        issues.append("No webhook has ever reached this server.")
    result["note"] = " | ".join(issues) if issues else (
        f"Healthy. {inbound_count} inbound messages stored; "
        f"{len(result['matching_events'])} events registered."
    )
    result["issues"] = issues
    return result


@router.post("/poll-now")
async def poll_now(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    """Sync inbox — re-register webhooks, replay history, refresh statuses."""
    return await sync_full_inbox(db, cu)
