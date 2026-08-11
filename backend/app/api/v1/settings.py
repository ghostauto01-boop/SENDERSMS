"""Settings — Gateway, Pushover, SIM, Compliance, Sending Rules."""
import json,logging,os
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
from app.security.encryption import encrypt_value,decrypt_value,mask_value
from app.config import settings

logger=logging.getLogger(__name__)
router=APIRouter()

@router.get("/gateway")
async def get_gw(db:AsyncSession=Depends(get_db)):
    from app.services.system_settings import get_sim_number
    return{"configured":settings.smsgate_configured,"is_enabled":True,"username":settings.SMSGATE_USERNAME or"","password":mask_value(settings.SMSGATE_PASSWORD or""),"base_url":settings.SMSGATE_BASE_URL or"","sim_number":await get_sim_number(db),"connection_status":"unknown","last_error":None}

@router.put("/gateway/sim")
async def set_sim(sim:int=1,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    from app.services.system_settings import set_sim_number
    return{"success":True,"sim_number":await set_sim_number(db,sim)}

@router.post("/gateway/test")
async def test_gw():
    from app.providers.smsgate import test_connection_direct
    r=await test_connection_direct()
    return{"success":r["success"]and r.get("online",False),"online":r.get("online",False),"name":r.get("name",""),"sims":r.get("sims",1),"message":"Connected"if r["success"]and r.get("online")else"Failed — phone offline or bad credentials","raw":r}

@router.post("/gateway/register-webhook")
async def reg_wh(url:Optional[str]=Query(None),cu:User=Depends(get_current_user)):
    """Register this deployment's webhook endpoint for every event we consume.

    `url` may be supplied to point the gateway somewhere else (a tunnel while
    developing, for example); otherwise PUBLIC_BASE_URL is used.
    """
    from app.providers.smsgate import register_webhook_direct
    from app.utils.urls import webhook_url,WEBHOOK_PATH
    if url:
        url=url.strip().rstrip("/")
        if not url.startswith(("http://","https://")):
            raise HTTPException(400,"The webhook URL must start with http:// or https://")
        if not url.endswith(WEBHOOK_PATH):
            url=f"{url}{WEBHOOK_PATH}"
        if url.startswith("http://") and not url.startswith("http://127.0.0.1"):
            raise HTTPException(400,"SMS-Gate only delivers to HTTPS endpoints (plain HTTP is allowed for 127.0.0.1 only). Use an HTTPS URL or a tunnel such as ngrok or Cloudflare Tunnel.")
    else:
        url=webhook_url()
    if not url:
        raise HTTPException(400,"PUBLIC_BASE_URL is not set. Set it to this deployment's public URL (e.g. https://your-app.onrender.com), or pass a URL explicitly, so the gateway knows where to deliver events.")
    r=await register_webhook_direct(url)
    if not r.get("success"):
        raise HTTPException(502,r.get("error") or "The gateway rejected the webhook registration.")
    return r

@router.get("/gateway/webhooks")
async def list_wh():
    """List webhooks registered on the gateway, annotated for the settings UI.

    Reports which of the events we consume are already delivered to this
    deployment and which are missing, so the UI can offer a one-click repair
    instead of forcing the user to call the API by hand.
    """
    from app.providers.smsgate import list_webhooks_direct,DEFAULT_EVENTS
    from app.utils.urls import webhook_url
    r=await list_webhooks_direct()
    hooks=[w for w in r.get("webhooks",[]) if isinstance(w,dict)]
    ours=webhook_url()
    mine=[w for w in hooks if w.get("url")==ours] if ours else []
    registered=sorted({w.get("event","") for w in mine if w.get("event")})
    missing=[e for e in DEFAULT_EVENTS if e not in registered]
    return{
        "configured":settings.smsgate_configured,
        "webhook_url":ours,
        "public_base_url_set":bool(ours),
        "signing_secret_set":bool(settings.SMSGATE_WEBHOOK_SECRET),
        "required_events":list(DEFAULT_EVENTS),
        "registered_events":registered,
        "missing_events":missing,
        "healthy":bool(ours) and not missing,
        "webhooks":[{"id":w.get("id"),"url":w.get("url",""),"event":w.get("event",""),
                     "is_ours":w.get("url")==ours}for w in hooks],
        "error":r.get("error"),
        "http":r.get("http"),
    }

@router.delete("/gateway/webhooks/{webhook_id}")
async def del_wh(webhook_id:str,cu:User=Depends(get_current_user)):
    """Remove a single webhook registration from the gateway."""
    from app.providers.smsgate import delete_webhook_direct
    r=await delete_webhook_direct(webhook_id)
    if not r.get("success"):
        raise HTTPException(502,r.get("error") or f"The gateway refused to delete this webhook (HTTP {r.get('http')}).")
    return{"success":True,"deleted":webhook_id}

@router.get("/notifications")
async def get_notifs(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    ps=await db.execute(select(NotificationProvider))
    return{"providers":[{"id":p.id,"provider":p.provider,"is_enabled":p.is_enabled,"notify_new_reply":p.notify_new_reply,"notify_campaign_completed":p.notify_campaign_completed,"notify_campaign_failed":p.notify_campaign_failed,"notify_gateway_offline":p.notify_gateway_offline,"notify_followup_due":p.notify_followup_due,"notify_system_error":p.notify_system_error}for p in ps.scalars().all()]}

@router.put("/notifications/{provider}")
async def put_notif(provider:str,is_enabled:Optional[bool]=Query(None),user_key:Optional[str]=Query(None),app_token:Optional[str]=Query(None),db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    if provider!="pushover":raise HTTPException(400,"Invalid")
    r=await db.execute(select(NotificationProvider).where(NotificationProvider.provider==provider).limit(1));p=r.scalar_one_or_none()
    if not p:p=NotificationProvider(provider=provider);db.add(p)
    cfg=json.loads(p.config_json or"{}")
    if user_key is not None:cfg["user_key_encrypted"]=encrypt_value(user_key)if user_key else""
    if app_token is not None:cfg["app_token_encrypted"]=encrypt_value(app_token)if app_token else""
    p.config_json=json.dumps(cfg)
    if is_enabled is not None:p.is_enabled=is_enabled
    p.updated_at=datetime.now(timezone.utc);await db.flush();return{"success":True}

@router.post("/notifications/{provider}/test")
async def test_notif(provider:str,db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    if provider!="pushover":raise HTTPException(400)
    r=await db.execute(select(NotificationProvider).where(NotificationProvider.provider==provider,NotificationProvider.is_enabled==True).limit(1));p=r.scalar_one_or_none()
    if not p:return{"success":False,"error":"Not configured"}
    cfg=json.loads(p.config_json or"{}");uk=decrypt_value(cfg.get("user_key_encrypted",""));at=decrypt_value(cfg.get("app_token_encrypted",""))
    from app.providers.pushover import PushoverProvider
    ok=await PushoverProvider(app_token=at,user_key=uk).test_notification()
    if ok:p.last_test_at=datetime.now(timezone.utc);p.last_error=None
    else:p.last_error="Test failed"
    await db.flush();return{"success":ok,"error":p.last_error if not ok else None}

@router.get("/compliance")
async def get_comp(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    return{s.key:s.value for s in(await db.execute(select(SystemSetting).where(SystemSetting.category=="compliance"))).scalars().all()}

@router.put("/compliance")
async def put_comp(ec:Optional[bool]=Query(None),ea:Optional[bool]=Query(None),es:Optional[bool]=Query(None),ek:Optional[bool]=Query(None),db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    for k,v in{"enable_consent_requirement":ec,"enable_auto_opt_out":ea,"enable_suppression_list":es,"enable_campaign_suppression":ek}.items():
        if v is not None:
            s=(await db.execute(select(SystemSetting).where(SystemSetting.key==k))).scalar_one_or_none()
            if s:s.value=str(v).lower()
            else:db.add(SystemSetting(key=k,value=str(v).lower(),category="compliance"))
    await db.flush();return{"success":True}

@router.get("/sending-rules")
async def get_sr(db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    return{s.key:s.value for s in(await db.execute(select(SystemSetting).where(SystemSetting.category=="sending_rules"))).scalars().all()}

@router.put("/sending-rules")
async def put_sr(dl:Optional[bool]=Query(None),dm:Optional[int]=Query(None),hl:Optional[bool]=Query(None),hm:Optional[int]=Query(None),ml:Optional[bool]=Query(None),mm:Optional[int]=Query(None),ss:Optional[str]=Query(None),se:Optional[str]=Query(None),aw:Optional[bool]=Query(None),ah:Optional[bool]=Query(None),db:AsyncSession=Depends(get_db),cu:User=Depends(get_current_user)):
    for k,v in{"enable_daily_limit":dl,"daily_maximum":dm,"enable_hourly_limit":hl,"hourly_maximum":hm,"enable_per_minute_limit":ml,"messages_per_minute":mm,"sending_start_time":ss,"sending_end_time":se,"allow_weekends":aw,"allow_holidays":ah}.items():
        if v is not None:
            s=(await db.execute(select(SystemSetting).where(SystemSetting.key==k))).scalar_one_or_none()
            val=str(v).lower()if isinstance(v,bool)else str(v)
            if s:s.value=val
            else:db.add(SystemSetting(key=k,value=val,category="sending_rules"))
    await db.flush();return{"success":True}
