"""FastAPI — webhook auto-register, status poll, scheduled, PWA, SPA."""
import os, logging, time
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from app.config import settings
from app.database import init_db, async_session_factory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def _startup_webhook():
    """Register our webhook URL with the gateway once per deployment target."""
    try:
        from app.services.system_settings import (
            WEBHOOK_REGISTERED, get_setting, set_setting,
        )
        from app.utils.urls import webhook_url

        url = webhook_url()
        if not url:
            logger.warning(
                "Skipping webhook registration: PUBLIC_BASE_URL is not set. "
                "Inbound SMS will not be delivered until it is configured."
            )
            return
        if not settings.smsgate_configured:
            logger.warning("Skipping webhook registration: gateway credentials not configured.")
            return

        async with async_session_factory() as db:
            # Re-register whenever the target URL changes (new deployment,
            # new domain), not just the first time ever.
            if await get_setting(db, WEBHOOK_REGISTERED) == url:
                return

            from app.providers.smsgate import register_webhook_direct
            r = await register_webhook_direct(url)
            if r.get("success"):
                await set_setting(db, WEBHOOK_REGISTERED, url,
                                  description="Webhook URL registered with the SMS gateway")
                await db.commit()
                logger.info("Webhook auto-registered at %s", url)
            else:
                logger.warning("Webhook registration failed: %s", r)
    except Exception as e:
        logger.warning(f"Webhook: {e}")

async def _poll():
    """Update delivery statuses for pending messages."""
    try:
        from app.services.system_settings import LAST_POLL, get_float, set_setting

        now = time.time()
        async with async_session_factory() as db:
            if now - await get_float(db, LAST_POLL, 0.0) < 15:
                return
            await set_setting(db, LAST_POLL, str(now),
                              description="Unix time of the last delivery-status poll")
            await db.commit()

        from sqlalchemy import select
        from app.models.conversation import Message
        from app.providers.smsgate import poll_status_for_ids

        async with async_session_factory() as db:
            msgs = await db.execute(
                select(Message).where(
                    Message.provider_message_id.isnot(None),
                    Message.status.in_(("sent","sending","queued"))
                ).limit(100))
            ids = [m.provider_message_id for m in msgs.scalars().all()]
            if not ids: return

            results = await poll_status_for_ids(ids)
            count = 0
            for r in results:
                mr = await db.execute(select(Message).where(Message.provider_message_id == r["provider_message_id"]))
                m = mr.scalar_one_or_none()
                if m and m.status != r["status"]:
                    m.status = r["status"]
                    if r["status"] == "delivered":
                        from datetime import datetime as dt, timezone as tz
                        m.delivered_at = dt.now(tz.utc)
                    count += 1
            if count:
                await db.commit()
                logger.info(f"STATUS: updated {count} messages")

        await _process_scheduled()
        await _launch_scheduled_campaigns()
    except Exception as e:
        logger.warning(f"Poll: {e}")


async def _launch_scheduled_campaigns():
    """Start campaigns whose scheduled time has arrived.

    Celery beat does this too. It is repeated here because on the Render free
    tier the worker sleeps after inactivity, and a campaign scheduled for
    tomorrow morning must still go out if nothing has woken the worker. The
    launcher claims each campaign with an atomic UPDATE, so both running at
    once cannot double-send.
    """
    try:
        from app.tasks.campaign_tasks import launch_due_campaigns_async
        launched = await launch_due_campaigns_async()
        if launched:
            logger.info("SCHEDULED CAMPAIGNS: launched %s", launched)
    except Exception as e:
        logger.warning(f"Scheduled campaigns: {e}")

async def _process_scheduled():
    try:
        from app.providers.smsgate import send_sms_direct
        from app.models.scheduled import ScheduledMessage
        from sqlalchemy import select
        from datetime import datetime as dt, timezone as tz
        async with async_session_factory() as db:
            due = await db.execute(select(ScheduledMessage).where(
                ScheduledMessage.status == "pending",
                ScheduledMessage.schedule_at <= dt.now(tz.utc)).limit(5))
            scheduled = due.scalars().all()
            if not scheduled: return
            for sm in scheduled:
                r = await send_sms_direct(sm.phone_number, sm.body, sm.sim_number)
                sm.status = "sent" if r["success"] else "failed"
                sm.error = r.get("error","")[:500] if not r["success"] else None
                sm.executed_at = dt.now(tz.utc)
            await db.commit()
            logger.info(f"SCHEDULED: {len(scheduled)} messages")
    except Exception as e: logger.warning(f"Scheduled: {e}")

async def _poll_loop():
    """Run _poll() on a timer instead of piggybacking on health checks."""
    import asyncio
    while True:
        try:
            await asyncio.sleep(settings.INLINE_POLL_INTERVAL)
            await _poll()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Poll loop: %s", e)

@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    try: await init_db(); logger.info("DB ready")
    except Exception as e: logger.warning("init_db: %s", e)
    try: await _startup_webhook()
    except Exception as e: logger.warning("webhook: %s", e)

    poller = None
    if settings.ENABLE_INLINE_POLLER:
        poller = asyncio.create_task(_poll_loop())
        logger.info("Inline poller started (every %ss)", settings.INLINE_POLL_INTERVAL)

    yield

    if poller:
        poller.cancel()
        try: await poller
        except (asyncio.CancelledError, Exception): pass

app = FastAPI(title=settings.APP_NAME, version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

from app.security.rate_limit import install_rate_limiting
install_rate_limiting(app)

@app.get("/api/v1/health")
async def health():
    """Liveness probe. Must stay cheap and side-effect free.

    This used to kick off _poll() (delivery-status sync AND sending due
    scheduled messages) on every call, so any uptime monitor or load
    balancer probe drove real SMS traffic.
    """
    return JSONResponse({"status":"ok","app":settings.APP_NAME,"version":"1.0.0"})

from app.api.v1 import auth, contacts, lists, campaigns, sequences, followups, inbox, templates, analytics, settings as settings_api, webhooks, dashboard, send, autoreply
app.include_router(auth.router, prefix="/api/v1/auth")
app.include_router(dashboard.router, prefix="/api/v1/dashboard")
app.include_router(contacts.router, prefix="/api/v1/contacts")
app.include_router(lists.router, prefix="/api/v1/lists")
app.include_router(campaigns.router, prefix="/api/v1/campaigns")
app.include_router(sequences.router, prefix="/api/v1/sequences")
app.include_router(followups.router, prefix="/api/v1/followups")
app.include_router(inbox.router, prefix="/api/v1/inbox")
app.include_router(templates.router, prefix="/api/v1/templates")
app.include_router(analytics.router, prefix="/api/v1/analytics")
app.include_router(settings_api.router, prefix="/api/v1/settings")
app.include_router(webhooks.router, prefix="/api/v1/webhooks")
app.include_router(send.router, prefix="/api/v1/send")
app.include_router(autoreply.router, prefix="/api/v1/autoreply")

PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "public")
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")

@app.get("/manifest.json")
async def m(): return JSONResponse({"name":"SMS SENDER","short_name":"SMS SENDER","start_url":"/","display":"standalone","orientation":"portrait-primary","background_color":"#111827","theme_color":"#2563eb","icons":[{"src":"/icon-192.png","sizes":"192x192","type":"image/png","purpose":"any maskable"},{"src":"/icon-512.png","sizes":"512x512","type":"image/png","purpose":"any maskable"}]})
@app.get("/sw.js")
async def sw(): p=os.path.join(PUBLIC_DIR,"sw.js"); return FileResponse(p,media_type="application/javascript") if os.path.isfile(p) else Response("",404)
@app.get("/icon-192.png")
async def i1(): p=os.path.join(PUBLIC_DIR,"icon-192.png"); return FileResponse(p,media_type="image/png") if os.path.isfile(p) else Response("",404)
@app.get("/icon-512.png")
async def i2(): p=os.path.join(PUBLIC_DIR,"icon-512.png"); return FileResponse(p,media_type="image/png") if os.path.isfile(p) else Response("",404)
@app.get("/favicon.svg")
async def fv(): p=os.path.join(PUBLIC_DIR,"favicon.svg"); return FileResponse(p,media_type="image/svg+xml") if os.path.isfile(p) else Response("",404)

if os.path.isdir(FRONTEND_DIR):
    ad=os.path.join(FRONTEND_DIR,"assets")
    if os.path.isdir(ad): app.mount("/assets", StaticFiles(directory=ad), name="assets")
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
    @app.get("/{fp:path}", response_class=HTMLResponse)
    async def spa(fp:str=""):
        if fp.startswith(("api/","assets/","docs","manifest","sw.js","icon-","favicon")): return JSONResponse({"detail":"Not Found"},404)
        i=os.path.join(FRONTEND_DIR,"index.html")
        return HTMLResponse(content=open(i).read()) if os.path.isfile(i) else JSONResponse({"detail":"No frontend"},404)
else:
    @app.get("/{fp:path}", response_class=HTMLResponse)
    async def spa(fp:str=""): return HTMLResponse("<h1>SMS SENDER API</h1>")
