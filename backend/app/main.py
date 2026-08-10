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
LAST_POLL = os.path.join(os.path.dirname(__file__), "..", "..", ".last_poll")
WEBHOOK_DONE = os.path.join(os.path.dirname(__file__), "..", "..", ".webhook_done")

async def _startup_webhook():
    try:
        if os.path.exists(WEBHOOK_DONE): return
        from app.providers.smsgate import register_webhook_direct
        r = await register_webhook_direct("https://sendsms-api.onrender.com/api/v1/webhooks/smsgateway")
        if r.get("success"):
            with open(WEBHOOK_DONE, "w") as f: f.write("ok")
            logger.info("Webhook auto-registered")
    except Exception as e: logger.warning(f"Webhook: {e}")

async def _poll():
    """Update delivery statuses for pending messages."""
    try:
        now = time.time()
        try:
            with open(LAST_POLL) as f:
                if now - float(f.read().strip()) < 15: return
        except: pass
        with open(LAST_POLL, "w") as f: f.write(str(now))

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
    except Exception as e:
        logger.warning(f"Poll: {e}")

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    try: await init_db(); logger.info("DB ready")
    except Exception as e: logger.warning("init_db: %s", e)
    try: await _startup_webhook()
    except Exception as e: logger.warning("webhook: %s", e)
    yield

app = FastAPI(title=settings.APP_NAME, version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/api/v1/health")
async def health(bg: BackgroundTasks):
    bg.add_task(_poll)
    return JSONResponse({"status":"ok","app":settings.APP_NAME,"version":"1.0.0"})

from app.api.v1 import auth, contacts, lists, campaigns, sequences, followups, inbox, templates, analytics, settings as settings_api, webhooks, dashboard, send
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
