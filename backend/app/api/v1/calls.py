"""Phone calls via CallGate — start/end calls, history, status.

The handset runs the CallGate Android app (same phone as SMS-Gate); this API
proxies call control through the backend because browsers cannot talk to the
phone's local server directly (CORS is disallowed by design).
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.call import CallLog
from app.models.contact import Contact
from app.models.user import User
from app.security.auth import get_current_user
from app.services.system_settings import (
    get_callgate_settings,
    set_callgate_settings,
    CALLGATE_WEBHOOK_REGISTERED,
    get_setting,
    set_setting,
)
from app.utils.phone import normalize_nigerian_number, phone_search_variants

logger = logging.getLogger(__name__)
router = APIRouter()


def _display(c: Contact | None, phone: str) -> str:
    if c is None:
        return phone
    name = " ".join(p for p in [c.first_name, c.last_name] if p).strip()
    return name or c.business_name or phone


async def _cfg(db: AsyncSession) -> dict:
    return await get_callgate_settings(db)


# --------------------------------------------------------------------------
# Config (also mirrored under /settings/callgate for the Settings page)
# --------------------------------------------------------------------------

class CallGateConfigIn(BaseModel):
    base_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    webhook_secret: Optional[str] = None
    dial_mode: Optional[str] = None  # callgate | direct


@router.get("/config")
async def get_config(db: AsyncSession = Depends(get_db),
                     cu: User = Depends(get_current_user)):
    from app.security.encryption import mask_value

    cfg = await _cfg(db)
    registered = await get_setting(db, CALLGATE_WEBHOOK_REGISTERED)
    return {
        "base_url": cfg["base_url"],
        "username": cfg["username"],
        "password_set": bool(cfg["password"]),
        "password_hint": mask_value(cfg["password"]) if cfg["password"] else "",
        "webhook_secret_set": bool(cfg["webhook_secret"]),
        "dial_mode": cfg["dial_mode"],
        "configured": cfg["configured"],
        "webhook_registered": registered,
    }


@router.put("/config")
async def save_config(payload: CallGateConfigIn,
                      db: AsyncSession = Depends(get_db),
                      cu: User = Depends(get_current_user)):
    data = payload.model_dump(exclude_unset=True)
    if "base_url" in data and data["base_url"]:
        b = data["base_url"].strip().rstrip("/")
        if b and not b.startswith(("http://", "https://")) and "." not in b and ":" not in b:
            raise HTTPException(400, "Base URL must be a phone address like 192.168.1.5:8084 or http://…")
    if "dial_mode" in data and data["dial_mode"] not in (None, "callgate", "direct"):
        raise HTTPException(400, "dial_mode must be 'callgate' or 'direct'")
    cfg = await set_callgate_settings(
        db,
        base_url=data.get("base_url"),
        username=data.get("username"),
        password=data.get("password"),
        webhook_secret=data.get("webhook_secret"),
        dial_mode=data.get("dial_mode"),
    )
    await db.commit()
    # A changed target invalidates the old registration marker.
    return {"success": True, "configured": cfg["configured"], "dial_mode": cfg["dial_mode"]}


@router.post("/test")
async def test_connection(db: AsyncSession = Depends(get_db),
                          cu: User = Depends(get_current_user)):
    from app.providers import callgate as provider

    cfg = await _cfg(db)
    r = await provider.test_connection_direct(cfg["base_url"], cfg["username"], cfg["password"])
    return {"success": r["success"], "online": r.get("online", False),
            "message": r.get("message", ""), "raw": {k: v for k, v in r.items() if k != "message"}}


@router.get("/webhooks")
async def list_webhooks(db: AsyncSession = Depends(get_db),
                        cu: User = Depends(get_current_user)):
    from app.providers import callgate as provider

    cfg = await _cfg(db)
    r = await provider.list_webhooks_direct(cfg["base_url"], cfg["username"], cfg["password"])
    if not r.get("success"):
        raise HTTPException(400, r.get("error") or "Could not read CallGate webhooks")
    return {"webhooks": r["webhooks"], "events": list(provider.CALL_EVENTS)}


@router.post("/register-webhook")
async def register_webhook(url: Optional[str] = Query(None),
                           db: AsyncSession = Depends(get_db),
                           cu: User = Depends(get_current_user)):
    """Point the phone's call events at this deployment (or an explicit URL)."""
    from app.providers import callgate as provider
    from app.utils.urls import WEBHOOK_PATH

    cfg = await _cfg(db)
    if not cfg["configured"]:
        raise HTTPException(400, "CallGate is not configured — set the phone address, username and password first.")
    if url:
        url = url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "The webhook URL must start with http:// or https://")
        suffix = "/api/v1/webhooks/callgate"
        if not url.endswith(suffix):
            url = f"{url}{suffix}"
    else:
        from app.utils.urls import public_base_url

        base = (public_base_url() or "").rstrip("/")
        if not base:
            raise HTTPException(400, "PUBLIC_BASE_URL is not set — pass an explicit HTTPS URL instead.")
        url = f"{base}/api/v1/webhooks/callgate"
    r = await provider.register_webhook_direct(url, None, cfg["base_url"], cfg["username"], cfg["password"])
    if r.get("success"):
        await set_setting(db, CALLGATE_WEBHOOK_REGISTERED, url,
                           category="callgate", description="Call webhook URL registered on the phone")
        await db.commit()
    else:
        raise HTTPException(400, "; ".join(r.get("errors") or [r.get("error") or "Registration failed"]))
    return r


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: str,
                         db: AsyncSession = Depends(get_db),
                         cu: User = Depends(get_current_user)):
    from app.providers import callgate as provider

    cfg = await _cfg(db)
    r = await provider.delete_webhook_direct(webhook_id, cfg["base_url"], cfg["username"], cfg["password"])
    if not r.get("success"):
        raise HTTPException(400, r.get("error") or "Delete failed")
    return {"success": True}


# --------------------------------------------------------------------------
# Calling
# --------------------------------------------------------------------------

class StartCallIn(BaseModel):
    contact_id: Optional[int] = None
    phone_number: Optional[str] = None
    # When true and CallGate is unreachable, still return a tel: fallback URL.
    allow_direct_fallback: bool = True


@router.post("/start")
async def start_call(payload: StartCallIn,
                     db: AsyncSession = Depends(get_db),
                     cu: User = Depends(get_current_user)):
    from app.providers import callgate as provider

    contact = None
    phone = (payload.phone_number or "").strip()
    if payload.contact_id:
        contact = (await db.execute(
            select(Contact).where(Contact.id == payload.contact_id))).scalar_one_or_none()
        if contact is None:
            raise HTTPException(404, "Contact not found")
        phone = contact.phone_number
    if not phone:
        raise HTTPException(400, "Provide contact_id or phone_number")

    cfg = await _cfg(db)
    dial_number = normalize_nigerian_number(phone) or phone

    log = CallLog(contact_id=contact.id if contact else None,
                  phone_number=dial_number, direction="outgoing", status="initiated")
    db.add(log)
    await db.flush()

    # Direct-dial mode (or unconfigured CallGate): hand a tel: URL to the app.
    if cfg["dial_mode"] == "direct" or not cfg["configured"]:
        log.status = "direct_dial"
        await db.flush()
        return {"success": True, "mode": "direct",
                "tel_url": f"tel:{dial_number}",
                "call_id": log.id, "phone": dial_number,
                "contact": _display(contact, dial_number),
                "note": "CallGate not configured — dialling directly from this device." if not cfg["configured"]
                        else "Direct-dial mode — calling from this device."}

    r = await provider.start_call_direct(dial_number, cfg["base_url"], cfg["username"], cfg["password"])
    if r.get("success"):
        log.status = "ringing"
        await db.flush()
        return {"success": True, "mode": "callgate", "call_id": log.id,
                "phone": dial_number, "contact": _display(contact, dial_number),
                "tel_url": f"tel:{dial_number}",
                "note": "Calling via your phone (CallGate)."}
    log.status = "failed"
    log.last_error = (r.get("error") or "")[:500]
    await db.flush()
    out = {"success": False, "mode": "callgate", "call_id": log.id,
           "phone": dial_number, "contact": _display(contact, dial_number),
           "error": r.get("error") or "Call failed"}
    if payload.allow_direct_fallback:
        out["tel_url"] = f"tel:{dial_number}"
        out["note"] = "CallGate is unreachable — tap the number to dial directly instead."
    return out


@router.post("/end")
async def end_call(db: AsyncSession = Depends(get_db),
                   cu: User = Depends(get_current_user)):
    from app.providers import callgate as provider

    cfg = await _cfg(db)
    if not cfg["configured"]:
        raise HTTPException(400, "CallGate is not configured")
    r = await provider.end_call_direct(cfg["base_url"], cfg["username"], cfg["password"])
    if r.get("success"):
        # Close the most recent open outgoing call.
        row = (await db.execute(
            select(CallLog).where(CallLog.direction == "outgoing",
                                  CallLog.status.in_(("initiated", "ringing", "started")))
            .order_by(desc(CallLog.id)).limit(1))).scalar_one_or_none()
        if row is not None:
            row.status = "ended"
            row.ended_at = datetime.now(timezone.utc)
            await db.flush()
        return {"success": True}
    raise HTTPException(400, r.get("error") or "Could not end the call")


@router.post("/log-direct")
async def log_direct(contact_id: Optional[int] = None,
                     phone_number: Optional[str] = None,
                     db: AsyncSession = Depends(get_db),
                     cu: User = Depends(get_current_user)):
    """Record a tel:-link call in history (the app can't observe it)."""
    phone = (phone_number or "").strip()
    contact = None
    if contact_id:
        contact = (await db.execute(
            select(Contact).where(Contact.id == contact_id))).scalar_one_or_none()
        if contact is None:
            raise HTTPException(404, "Contact not found")
        phone = contact.phone_number
    if not phone:
        raise HTTPException(400, "Provide contact_id or phone_number")
    log = CallLog(contact_id=contact.id if contact else None,
                  phone_number=phone, direction="outgoing", status="direct_dial",
                  started_at=datetime.now(timezone.utc))
    db.add(log)
    await db.flush()
    return {"success": True, "call_id": log.id}


@router.get("/logs")
async def call_logs(page: int = Query(1, ge=1),
                    per_page: int = Query(25, ge=1, le=100),
                    search: Optional[str] = None,
                    db: AsyncSession = Depends(get_db),
                    cu: User = Depends(get_current_user)):
    q = select(CallLog)
    if search and search.strip():
        term = search.strip()
        variants = phone_search_variants(term)
        conds = [CallLog.phone_number.ilike(f"%{term}%")]
        for v in variants[:6]:
            conds.append(CallLog.phone_number.ilike(f"%{v}%"))
        # Also match by contact name.
        sub = select(Contact.id).where(
            or_(Contact.first_name.ilike(f"%{term}%"),
                Contact.last_name.ilike(f"%{term}%"),
                Contact.business_name.ilike(f"%{term}%")))
        ids = list((await db.execute(sub)).scalars().all())
        if ids:
            conds.append(CallLog.contact_id.in_(ids))
        q = q.where(or_(*conds))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (await db.execute(
        q.order_by(desc(CallLog.id)).offset((page - 1) * per_page).limit(per_page))).scalars().all()
    items = []
    for r in rows:
        c = r.contact
        items.append({
            "id": r.id, "contact_id": r.contact_id,
            "contact_name": _display(c, r.phone_number),
            "phone_number": r.phone_number, "direction": r.direction,
            "status": r.status, "started_at": r.started_at.isoformat() if r.started_at else None,
            "ended_at": r.ended_at.isoformat() if r.ended_at else None,
            "duration_seconds": r.duration_seconds,
            "last_error": r.last_error,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return {"total": total, "page": page, "per_page": per_page, "items": items}


@router.get("/active")
async def active_call(db: AsyncSession = Depends(get_db),
                      cu: User = Depends(get_current_user)):
    row = (await db.execute(
        select(CallLog).where(CallLog.status.in_(("initiated", "ringing", "started")))
        .order_by(desc(CallLog.id)).limit(1))).scalar_one_or_none()
    if row is None:
        return {"active": False}
    return {"active": True, "id": row.id, "phone_number": row.phone_number,
            "contact_id": row.contact_id, "status": row.status,
            "contact_name": _display(row.contact, row.phone_number)}
