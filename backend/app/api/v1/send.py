"""Send SMS — uses send_sms_direct + scheduling with proper history/failed tracking."""
import json, logging, uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.user import User
from app.security.auth import get_current_user
from app.models.contact import Contact
from app.models.contact_list import ContactListMember
from app.models.conversation import Conversation, Message
from app.models.scheduled import ScheduledMessage
from app.utils.phone import normalize_nigerian_number, count_sms_segments
from app.utils.templating import render_template

logger = logging.getLogger(__name__)
router = APIRouter()

def _parse_schedule_at(raw: str) -> datetime:
    """Parse schedule_at string into aware UTC datetime, accepting naive or offset forms."""
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(400, "Invalid schedule_at format. Use ISO8601 e.g. 2026-08-12T09:00:00+01:00 or UTC")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    if dt <= datetime.now(timezone.utc):
        raise HTTPException(400, "schedule_at must be in the future")
    return dt

@router.post("/")
async def send_sms_now(
    contact_id: Optional[int] = Query(None),
    phone_number: Optional[str] = Query(None),
    list_id: Optional[int] = Query(None),
    body: str = Query(..., min_length=1, max_length=1600),
    schedule_at: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    from app.services.system_settings import get_sim_number
    sim = await get_sim_number(db)

    # SCHEDULED: store for later delivery, do not call gateway now
    if schedule_at:
        sched_dt = _parse_schedule_at(schedule_at)
        scheduled = []
        if contact_id:
            r = await db.execute(select(Contact).where(Contact.id == contact_id))
            c = r.scalar_one_or_none()
            if not c:
                raise HTTPException(404, "Contact not found")
            if c.is_opted_out:
                raise HTTPException(400, "Contact opted out")
            sm = ScheduledMessage(
                contact_id=c.id,
                phone_number=c.phone_number,
                list_id=None,
                body=body,
                schedule_at=sched_dt,
                sim_number=sim,
            )
            db.add(sm)
            scheduled.append(c.phone_number)
        elif phone_number:
            norm = normalize_nigerian_number(phone_number)
            if not norm:
                raise HTTPException(400, f"Invalid number:{phone_number}")
            # check if contact exists and opted out
            r = await db.execute(select(Contact).where(Contact.phone_number == norm))
            existing = r.scalar_one_or_none()
            if existing and existing.is_opted_out:
                raise HTTPException(400, "Contact opted out")
            sm = ScheduledMessage(phone_number=norm, body=body, schedule_at=sched_dt, sim_number=sim)
            db.add(sm)
            scheduled.append(norm)
        elif list_id:
            members = await db.execute(select(ContactListMember).where(ContactListMember.list_id == list_id))
            count = 0
            for m in members.scalars().all():
                cr = await db.execute(select(Contact).where(Contact.id == m.contact_id))
                cc = cr.scalar_one_or_none()
                if cc and not cc.is_opted_out:
                    sm = ScheduledMessage(
                        contact_id=cc.id,
                        phone_number=cc.phone_number,
                        list_id=list_id,
                        body=body,
                        schedule_at=sched_dt,
                        sim_number=sim,
                    )
                    db.add(sm)
                    scheduled.append(cc.phone_number)
                    count += 1
            if count == 0:
                raise HTTPException(400, "List empty or all contacts opted out")
        else:
            raise HTTPException(400, "Provide contact_id, phone_number, or list_id")
        await db.flush()
        # Ensure UTC iso for client
        iso = sched_dt.isoformat()
        return {
            "success": True,
            "scheduled": True,
            "count": len(scheduled),
            "schedule_at": iso,
            "note": f"{len(scheduled)} message(s) scheduled for {iso}",
        }

    # SEND NOW
    recipients = []
    if contact_id:
        r = await db.execute(select(Contact).where(Contact.id == contact_id))
        c = r.scalar_one_or_none()
        if not c:
            raise HTTPException(404, "Contact not found")
        if c.is_opted_out:
            raise HTTPException(400, "Contact opted out")
        recipients.append(c)
    elif phone_number:
        norm = normalize_nigerian_number(phone_number)
        if not norm:
            raise HTTPException(400, f"Invalid:{phone_number}")
        r = await db.execute(select(Contact).where(Contact.phone_number == norm))
        c = r.scalar_one_or_none()
        if not c:
            c = Contact(phone_number=norm, country="Nigeria", lead_status="new", source="manual")
            db.add(c)
            await db.flush()
        if c.is_opted_out:
            raise HTTPException(400, "Contact opted out")
        recipients.append(c)
    elif list_id:
        members = await db.execute(select(ContactListMember).where(ContactListMember.list_id == list_id))
        for m in members.scalars().all():
            cr = await db.execute(select(Contact).where(Contact.id == m.contact_id))
            cc = cr.scalar_one_or_none()
            if cc and not cc.is_opted_out:
                recipients.append(cc)
        if not recipients:
            raise HTTPException(400, "List empty")
    else:
        raise HTTPException(400, "Provide contact_id, phone_number, or list_id")

    char_count, segment_count = count_sms_segments(body)
    from app.services.gateway_dispatch import get_active_gateway, send_sms_dispatch
    active_provider = await get_active_gateway(db)
    results = []

    for contact in recipients:
        msg = render_template(body, contact)
        char_count, segment_count = count_sms_segments(msg)
        cr = await db.execute(
            select(Conversation).where(Conversation.contact_id == contact.id).order_by(Conversation.id).limit(1)
        )
        conv = cr.scalars().first()
        if not conv:
            conv = Conversation(contact_id=contact.id, status="active")
            db.add(conv)
            await db.flush()
        message = Message(
            conversation_id=conv.id,
            contact_id=contact.id,
            direction="outgoing",
            body=msg,
            segment_count=segment_count,
            char_count=char_count,
            status="sending",
            provider=active_provider,
            idempotency_key=f"direct-{contact.id}-{uuid.uuid4().hex[:8]}",
        )
        db.add(message)
        await db.flush()
        provider_name, r = await send_sms_dispatch(db, contact.phone_number, msg, sim)
        message.provider = provider_name
        if r["success"]:
            message.status = "sent"
            message.provider_message_id = r.get("provider_message_id", "")
            message.sent_at = datetime.now(timezone.utc)
        else:
            message.status = "failed"
            message.last_error = r.get("error", "")
            message.failed_at = datetime.now(timezone.utc)
        message.provider_response = json.dumps(r.get("raw")) if r.get("raw") else None
        conv.message_count = (conv.message_count or 0) + 1
        conv.last_message_preview = msg[:100]
        conv.last_message_at = datetime.now(timezone.utc)
        contact.messages_sent = (contact.messages_sent or 0) + 1
        contact.last_contacted_at = datetime.now(timezone.utc)
        results.append(
            {
                "contact_id": contact.id,
                "phone": contact.phone_number,
                "message_id": message.id,
                "provider_message_id": r.get("provider_message_id", ""),
                "status": "sent" if r["success"] else "failed",
                "error": r.get("error") if not r["success"] else None,
                "api_response": r.get("raw"),
            }
        )

    await db.flush()
    sc = sum(1 for x in results if x["status"] == "sent")
    return {
        "success": sc > 0,
        "sent": sc,
        "failed": len(results) - sc,
        "total": len(results),
        "char_count": char_count,
        "segments": segment_count,
        "results": results,
        "sim_used": sim,
    }

@router.get("/scheduled")
async def get_scheduled(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """List scheduled messages with status filtering. Supports pending / sent / failed / cancelled / all."""
    base = select(ScheduledMessage)
    if status and status != "all":
        base = base.where(ScheduledMessage.status == status)
    if search:
        term = f"%{search}%"
        base = base.where(or_(ScheduledMessage.phone_number.ilike(term), ScheduledMessage.body.ilike(term)))
    # count
    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0
    # paginate - order by schedule_at desc so nearest future first? For scheduled we want pending soon first, but desc is recent first; keep pending asc then fallback
    # Use created order desc for history view; for pending we want earliest first. Simplify: if status==pending order asc else desc
    if status == "pending":
        q = base.order_by(ScheduledMessage.schedule_at.asc()).offset((page - 1) * per_page).limit(per_page)
    else:
        q = base.order_by(ScheduledMessage.schedule_at.desc()).offset((page - 1) * per_page).limit(per_page)
    items = (await db.execute(q)).scalars().all()

    # Enrich with contact names if available
    out = []
    for s in items:
        contact_name = None
        if s.contact_id:
            r = await db.execute(select(Contact).where(Contact.id == s.contact_id))
            c = r.scalar_one_or_none()
            if c:
                contact_name = f"{c.first_name or ''} {c.last_name or ''}".strip() or c.business_name or None
        out.append(
            {
                "id": s.id,
                "contact_id": s.contact_id,
                "contact_name": contact_name,
                "phone": s.phone_number,
                "body": s.body,
                "body_preview": s.body[:100] if s.body else "",
                "schedule_at": s.schedule_at.isoformat() if s.schedule_at else None,
                "status": s.status,
                "error": s.error,
                "executed_at": s.executed_at.isoformat() if s.executed_at else None,
                "message_id": s.message_id,
                "list_id": s.list_id,
                "sim_number": s.sim_number,
            }
        )
    return {"total": total, "page": page, "per_page": per_page, "items": out}

@router.get("/history")
async def get_history(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """List actual sent messages (from messages table) filtered by status. Powers Sent / Failed tabs."""
    base = select(Message).where(Message.direction == "outgoing")
    if status and status != "all":
        # map 'failed' includes both failed and cancelled for visibility
        if status == "failed":
            base = base.where(Message.status.in_(["failed", "cancelled"]))
        else:
            base = base.where(Message.status == status)
    if search:
        term = f"%{search}%"
        base = base.where(or_(Message.body.ilike(term), Message.last_error.ilike(term)))
    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0
    q = base.order_by(Message.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    msgs = (await db.execute(q)).scalars().all()
    # batch load contacts for names/phones if contact_id missing? Use message.contact_id
    out = []
    for m in msgs:
        # try to get phone from contact
        phone = None
        contact_name = None
        if m.contact_id:
            r = await db.execute(select(Contact).where(Contact.id == m.contact_id))
            c = r.scalar_one_or_none()
            if c:
                phone = c.phone_number
                contact_name = f"{c.first_name or ''} {c.last_name or ''}".strip() or c.business_name or c.phone_number
        out.append(
            {
                "id": m.id,
                "contact_id": m.contact_id,
                "contact_name": contact_name,
                "phone": phone,
                "body": m.body,
                "body_preview": m.body[:100] if m.body else "",
                "status": m.status,
                "error": m.last_error,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "sent_at": m.sent_at.isoformat() if m.sent_at else None,
                "failed_at": m.failed_at.isoformat() if m.failed_at else None,
                "delivered_at": m.delivered_at.isoformat() if m.delivered_at else None,
                "provider_message_id": m.provider_message_id,
                "campaign_id": m.campaign_id,
                "conversation_id": m.conversation_id,
            }
        )
    return {"total": total, "page": page, "per_page": per_page, "items": out}

@router.post("/scheduled/{sid}/cancel")
async def cancel_scheduled(sid: int, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    r = await db.execute(select(ScheduledMessage).where(ScheduledMessage.id == sid))
    s = r.scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Not found")
    if s.status != "pending":
        raise HTTPException(400, f"Cannot cancel a message that is {s.status}")
    s.status = "cancelled"
    await db.flush()
    return {"success": True, "status": s.status}

@router.post("/scheduled/{sid}/retry")
async def retry_scheduled(sid: int, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    """Retry a failed scheduled message by resetting it to pending with same schedule (now +1min)."""
    r = await db.execute(select(ScheduledMessage).where(ScheduledMessage.id == sid))
    s = r.scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Not found")
    if s.status not in ("failed", "cancelled"):
        raise HTTPException(400, f"Can only retry failed/cancelled messages (now {s.status})")
    s.status = "pending"
    s.schedule_at = datetime.now(timezone.utc)  # send ASAP on next poll
    s.error = None
    s.executed_at = None
    await db.flush()
    return {"success": True, "status": s.status, "schedule_at": s.schedule_at.isoformat()}

@router.post("/messages/{mid}/retry")
async def retry_message(mid: int, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    """Re-send a failed outgoing message immediately via gateway, updating its status."""
    r = await db.execute(select(Message).where(Message.id == mid))
    m = r.scalar_one_or_none()
    if not m:
        raise HTTPException(404, "Message not found")
    if m.direction != "outgoing":
        raise HTTPException(400, "Only outgoing messages can be retried")
    if m.status not in ("failed", "cancelled"):
        raise HTTPException(400, f"Message is {m.status}, not failed")

    contact = (await db.execute(select(Contact).where(Contact.id == m.contact_id))).scalar_one_or_none()
    if not contact:
        raise HTTPException(404, "Contact not found")
    if contact.is_opted_out:
        raise HTTPException(400, "Contact opted out")

    from app.services.gateway_dispatch import send_sms_dispatch
    from app.services.system_settings import get_sim_number
    sim = await get_sim_number(db)
    provider_name, result = await send_sms_dispatch(db, contact.phone_number, m.body, sim)
    m.provider = provider_name
    if result["success"]:
        m.status = "sent"
        m.provider_message_id = result.get("provider_message_id", "")
        m.sent_at = datetime.now(timezone.utc)
        m.last_error = None
        m.failed_at = None
        m.provider_response = json.dumps(result.get("raw")) if result.get("raw") else None
    else:
        m.status = "failed"
        m.last_error = result.get("error", "")[:500]
        m.failed_at = datetime.now(timezone.utc)
        m.provider_response = json.dumps(result.get("raw")) if result.get("raw") else None
    await db.flush()
    return {"success": result["success"], "status": m.status, "error": m.last_error, "message_id": m.id}
