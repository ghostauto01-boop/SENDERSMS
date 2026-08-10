"""Send SMS — send now or schedule for later."""
import json, logging, uuid, os
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.user import User
from app.security.auth import get_current_user
from app.models.contact import Contact
from app.models.contact_list import ContactListMember
from app.models.conversation import Conversation, Message
from app.models.scheduled import ScheduledMessage
from app.utils.phone import normalize_nigerian_number, count_sms_segments
from app.providers.smsgate import SMSGateProvider
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

SIM_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "..", ".sim_number")
def _get_sim():
    try: return int(open(SIM_FILE).read().strip())
    except: return 1

def _make_provider():
    return SMSGateProvider(
        base_url=settings.SMSGATE_BASE_URL or "https://api.sms-gate.app/3rdparty/v1",
        username=settings.SMSGATE_USERNAME or "",
        password=settings.SMSGATE_PASSWORD or "",
        sim_number=_get_sim(), timeout=45)

@router.post("/")
async def send_sms_now(
    contact_id: Optional[int] = Query(None),
    phone_number: Optional[str] = Query(None),
    list_id: Optional[int] = Query(None),
    body: str = Query(..., min_length=1, max_length=1600),
    schedule_at: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Send SMS now OR schedule for later.
    schedule_at: ISO datetime string like '2026-08-11T14:00:00+01:00'
    If schedule_at is provided, message is stored and sent at that time.
    """
    # ── SCHEDULED SEND ──
    if schedule_at:
        try:
            sched_dt = datetime.fromisoformat(schedule_at)
        except:
            raise HTTPException(400, "Invalid schedule_at format. Use ISO datetime like 2026-08-11T14:00:00+01:00")

        if sched_dt <= datetime.now(timezone.utc):
            raise HTTPException(400, "schedule_at must be in the future")

        sim = _get_sim()
        scheduled_count = 0
        scheduled_items = []

        if contact_id:
            r = await db.execute(select(Contact).where(Contact.id == contact_id))
            c = r.scalar_one_or_none()
            if not c: raise HTTPException(404, "Contact not found")
            if c.is_opted_out: raise HTTPException(400, "Contact opted out")
            sm = ScheduledMessage(contact_id=c.id, phone_number=c.phone_number, body=body, schedule_at=sched_dt, sim_number=sim)
            db.add(sm); scheduled_count = 1; scheduled_items.append({"phone": c.phone_number})
        elif phone_number:
            norm = normalize_nigerian_number(phone_number)
            if not norm: raise HTTPException(400, f"Invalid number: {phone_number}")
            sm = ScheduledMessage(phone_number=norm, body=body, schedule_at=sched_dt, sim_number=sim)
            db.add(sm); scheduled_count = 1; scheduled_items.append({"phone": norm})
        elif list_id:
            members = await db.execute(select(ContactListMember).where(ContactListMember.list_id == list_id))
            for m in members.scalars().all():
                cr = await db.execute(select(Contact).where(Contact.id == m.contact_id))
                cc = cr.scalar_one_or_none()
                if cc and not cc.is_opted_out:
                    sm = ScheduledMessage(contact_id=cc.id, phone_number=cc.phone_number, body=body, schedule_at=sched_dt, sim_number=sim)
                    db.add(sm); scheduled_count += 1; scheduled_items.append({"phone": cc.phone_number})
        else:
            raise HTTPException(400, "Provide contact_id, phone_number, or list_id")

        await db.flush()
        return {"success": True, "scheduled": True, "count": scheduled_count,
                "schedule_at": schedule_at, "items": scheduled_items[:10],
                "note": f"{scheduled_count} message(s) scheduled for {schedule_at}"}

    # ── SEND NOW ──
    recipients: list[Contact] = []
    if contact_id:
        r = await db.execute(select(Contact).where(Contact.id == contact_id))
        c = r.scalar_one_or_none()
        if not c: raise HTTPException(404, "Contact not found")
        if c.is_opted_out: raise HTTPException(400, "Contact opted out")
        recipients.append(c)
    elif phone_number:
        norm = normalize_nigerian_number(phone_number)
        if not norm: raise HTTPException(400, f"Invalid Nigerian number: {phone_number}")
        r = await db.execute(select(Contact).where(Contact.phone_number == norm))
        c = r.scalar_one_or_none()
        if not c:
            c = Contact(phone_number=norm, country="Nigeria", lead_status="new", source="manual")
            db.add(c); await db.flush()
        if c.is_opted_out: raise HTTPException(400, "Contact opted out")
        recipients.append(c)
    elif list_id:
        members = await db.execute(select(ContactListMember).where(ContactListMember.list_id == list_id))
        for m in members.scalars().all():
            cr = await db.execute(select(Contact).where(Contact.id == m.contact_id))
            cc = cr.scalar_one_or_none()
            if cc and not cc.is_opted_out: recipients.append(cc)
        if not recipients: raise HTTPException(400, "List empty or all opted out")
    else:
        raise HTTPException(400, "Provide contact_id, phone_number, or list_id")

    char_count, segment_count = count_sms_segments(body)
    results = []
    p = _make_provider()

    for contact in recipients:
        msg = body
        for k, v in [("{{first_name}}", contact.first_name or ""),
                      ("{{business_name}}", contact.business_name or ""),
                      ("{{city}}", contact.city or ""),
                      ("{{state}}", contact.state or "")]:
            msg = msg.replace(k, v)
        cr = await db.execute(select(Conversation).where(Conversation.contact_id == contact.id))
        conv = cr.scalar_one_or_none()
        if not conv:
            conv = Conversation(contact_id=contact.id, status="active")
            db.add(conv); await db.flush()
        message = Message(conversation_id=conv.id, contact_id=contact.id, direction="outgoing",
            body=msg, segment_count=segment_count, char_count=char_count,
            status="sending", provider="smsgate",
            idempotency_key=f"direct-{contact.id}-{uuid.uuid4().hex[:8]}")
        db.add(message); await db.flush()
        r = await p.send_sms(to_number=contact.phone_number, message=msg)
        if r.success:
            message.status = "sent"; message.provider_message_id = r.provider_message_id; message.sent_at = datetime.now(timezone.utc)
        else:
            message.status = "failed"; message.last_error = r.error; message.failed_at = datetime.now(timezone.utc)
        message.provider_response = json.dumps(r.raw_response) if r.raw_response else None
        conv.message_count = (conv.message_count or 0) + 1
        conv.last_message_preview = msg[:100]; conv.last_message_at = datetime.now(timezone.utc)
        contact.messages_sent = (contact.messages_sent or 0) + 1
        contact.last_contacted_at = datetime.now(timezone.utc)
        results.append({"contact_id": contact.id, "phone": contact.phone_number, "message_id": message.id,
            "provider_message_id": r.provider_message_id, "status": "sent" if r.success else "failed",
            "error": r.error if not r.success else None, "api_response": r.raw_response})

    await p.close(); await db.flush()
    sent_count = sum(1 for r_ in results if r_["status"] == "sent")
    return {"success": sent_count > 0, "sent": sent_count, "failed": len(results) - sent_count,
        "total": len(results), "char_count": char_count, "segments": segment_count,
        "results": results, "gateway_url": p.base_url, "sim_used": p.sim_number}

@router.get("/scheduled")
async def get_scheduled(page: int=1, per_page: int=50,
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    q = select(ScheduledMessage).order_by(ScheduledMessage.schedule_at.desc()).offset((page-1)*per_page).limit(per_page)
    items = (await db.execute(q)).scalars().all()
    return {"items": [{"id": s.id, "phone": s.phone_number, "body": s.body[:100],
        "schedule_at": s.schedule_at.isoformat() if s.schedule_at else None,
        "status": s.status, "error": s.error, "executed_at": s.executed_at.isoformat() if s.executed_at else None}
        for s in items]}

@router.post("/scheduled/{sid}/cancel")
async def cancel_scheduled(sid: int,
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    r = await db.execute(select(ScheduledMessage).where(ScheduledMessage.id == sid))
    s = r.scalar_one_or_none()
    if not s: raise HTTPException(404, "Not found")
    s.status = "cancelled"
    await db.flush()
    return {"success": True}
