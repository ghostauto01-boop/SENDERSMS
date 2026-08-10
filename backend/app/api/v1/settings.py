"""Settings API — Gateway (direct from config), Pushover, Compliance, Sending Rules."""
import json, logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.notification import NotificationProvider
from app.models.system import SystemSetting
from app.models.user import User
from app.security.auth import get_current_user
from app.security.encryption import encrypt_value, decrypt_value, mask_value
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# ── SMS Gateway (reads directly from config — no DB table) ──

import os
SIM_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "..", ".sim_number")

def _get_sim() -> int:
    try:
        with open(SIM_FILE) as f: return int(f.read().strip())
    except: return 1

def _set_sim(n: int):
    with open(SIM_FILE, "w") as f: f.write(str(n))

@router.get("/gateway")
async def get_gw():
    """Gateway status from hardcoded config. No database involved."""
    has_creds = bool(settings.SMSGATE_USERNAME and settings.SMSGATE_PASSWORD)
    return {
        "configured": has_creds,
        "is_enabled": True,
        "username": settings.SMSGATE_USERNAME or "",
        "password": mask_value(settings.SMSGATE_PASSWORD or ""),
        "base_url": settings.SMSGATE_BASE_URL or "https://api.sms-gate.app/3rdparty/v1",
        "sim_number": _get_sim(),
        "connection_status": "unknown",
        "last_error": None,
    }

@router.put("/gateway/sim")
async def set_sim(sim: int = 1):
    """Set the SIM slot for sending SMS."""
    _set_sim(max(1, min(2, sim)))
    return {"success": True, "sim_number": _get_sim()}

@router.post("/gateway/test")
async def test_gw(sim: int = None):
    """Test SMS-Gate.app connection directly from config."""
    from app.providers.smsgate import SMSGateProvider
    s = sim or _get_sim()
    p = SMSGateProvider(sim_number=s)
    ok = await p.test_connection()
    await p.close()
    return {
        "success": ok,
        "connection_status": "healthy" if ok else "error",
        "url_used": p.base_url,
        "username_used": p.username,
        "has_password": bool(p.password),
        "message": "Connected — phone is online" if ok else "Failed — phone offline or bad credentials",
    }

# ── Notifications ──────────────────────────

@router.get("/notifications")
async def get_notifs(db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    ps = await db.execute(select(NotificationProvider))
    return {"providers": [{"id": p.id, "provider": p.provider, "is_enabled": p.is_enabled,
            "notify_new_reply": p.notify_new_reply, "notify_campaign_completed": p.notify_campaign_completed,
            "notify_campaign_failed": p.notify_campaign_failed, "notify_gateway_offline": p.notify_gateway_offline,
            "notify_followup_due": p.notify_followup_due, "notify_system_error": p.notify_system_error}
            for p in ps.scalars().all()]}

@router.put("/notifications/{provider}")
async def put_notif(provider: str, is_enabled: Optional[bool] = Query(None),
    user_key: Optional[str] = Query(None), app_token: Optional[str] = Query(None),
    notify_new_reply: Optional[bool] = Query(None), notify_campaign_completed: Optional[bool] = Query(None),
    notify_campaign_failed: Optional[bool] = Query(None), notify_gateway_offline: Optional[bool] = Query(None),
    notify_followup_due: Optional[bool] = Query(None), notify_system_error: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    if provider not in ("pushover", "onesignal"): raise HTTPException(400, "Invalid")
    r = await db.execute(select(NotificationProvider).where(NotificationProvider.provider == provider).limit(1))
    p = r.scalar_one_or_none()
    if not p: p = NotificationProvider(provider=provider); db.add(p)
    cfg = json.loads(p.config_json or "{}")
    if user_key is not None: cfg["user_key_encrypted"] = encrypt_value(user_key) if user_key else ""
    if app_token is not None: cfg["app_token_encrypted"] = encrypt_value(app_token) if app_token else ""
    p.config_json = json.dumps(cfg)
    if is_enabled is not None: p.is_enabled = is_enabled
    for attr in ["notify_new_reply","notify_campaign_completed","notify_campaign_failed","notify_gateway_offline","notify_followup_due","notify_system_error"]:
        v = locals().get(attr); v is not None and setattr(p, attr, v)
    p.updated_at = datetime.now(timezone.utc); await db.flush()
    return {"success": True}

@router.post("/notifications/{provider}/test")
async def test_notif(provider: str, db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    if provider not in ("pushover","onesignal"): raise HTTPException(400, "Invalid")
    r = await db.execute(select(NotificationProvider).where(NotificationProvider.provider == provider, NotificationProvider.is_enabled == True).limit(1))
    prov = r.scalar_one_or_none()
    if not prov: return {"success": False, "error": "Not configured"}
    cfg = json.loads(prov.config_json or "{}")
    if provider == "pushover":
        from app.providers.pushover import PushoverProvider
        ok = await PushoverProvider(app_token=decrypt_value(cfg.get("app_token_encrypted","")), user_key=decrypt_value(cfg.get("user_key_encrypted",""))).test_notification()
    else:
        from app.providers.onesignal import OneSignalProvider
        ok = await OneSignalProvider(app_id=cfg.get("app_id"), rest_api_key=decrypt_value(cfg.get("rest_api_key_encrypted",""))).test_notification()
    if ok: prov.last_test_at = datetime.now(timezone.utc); prov.last_error = None
    else: prov.last_error = "Test failed"
    await db.flush(); return {"success": ok, "error": prov.last_error if not ok else None}

@router.get("/compliance")
async def get_comp(db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    return {s.key: s.value for s in (await db.execute(select(SystemSetting).where(SystemSetting.category == "compliance"))).scalars().all()}

@router.put("/compliance")
async def put_comp(ec: Optional[bool]=Query(None), ea: Optional[bool]=Query(None), es: Optional[bool]=Query(None), ek: Optional[bool]=Query(None),
    db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    for k, v in {"enable_consent_requirement": ec, "enable_auto_opt_out": ea, "enable_suppression_list": es, "enable_campaign_suppression": ek}.items():
        if v is not None:
            s = (await db.execute(select(SystemSetting).where(SystemSetting.key == k))).scalar_one_or_none()
            if s: s.value = str(v).lower()
            else: db.add(SystemSetting(key=k, value=str(v).lower(), category="compliance"))
    await db.flush(); return {"success": True}

@router.get("/sending-rules")
async def get_sr(db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    return {s.key: s.value for s in (await db.execute(select(SystemSetting).where(SystemSetting.category == "sending_rules"))).scalars().all()}

@router.put("/sending-rules")
async def put_sr(dl: Optional[bool]=Query(None), dm: Optional[int]=Query(None), hl: Optional[bool]=Query(None), hm: Optional[int]=Query(None),
    ml: Optional[bool]=Query(None), mm: Optional[int]=Query(None), md: Optional[int]=Query(None), xd: Optional[int]=Query(None),
    ss: Optional[str]=Query(None), se: Optional[str]=Query(None), aw: Optional[bool]=Query(None), ah: Optional[bool]=Query(None),
    db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    for k, v in {"enable_daily_limit": dl, "daily_maximum": dm, "enable_hourly_limit": hl, "hourly_maximum": hm,
        "enable_per_minute_limit": ml, "messages_per_minute": mm, "min_delay": md, "max_delay": xd,
        "sending_start_time": ss, "sending_end_time": se, "allow_weekends": aw, "allow_holidays": ah}.items():
        if v is not None:
            s = (await db.execute(select(SystemSetting).where(SystemSetting.key == k))).scalar_one_or_none()
            val = str(v).lower() if isinstance(v, bool) else str(v)
            if s: s.value = val
            else: db.add(SystemSetting(key=k, value=val, category="sending_rules"))
    await db.flush(); return {"success": True}
