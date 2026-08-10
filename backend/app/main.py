"""FastAPI — DB init, auto-poll inbox (direct auth), PWA, API, SPA."""
import os, logging, time, base64, json
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from app.config import settings
from app.database import init_db, async_session_factory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LAST_POLL_FILE = os.path.join(os.path.dirname(__file__), "..", "..", ".last_poll")
POLL_INTERVAL = 15

async def _auto_poll_inbox():
    """Background: poll SMS-Gate.app inbox using EXACT send_sms auth pattern."""
    try:
        now = time.time()
        try:
            with open(LAST_POLL_FILE) as f: last = float(f.read().strip())
            if now - last < POLL_INTERVAL: return
        except: pass

        with open(LAST_POLL_FILE, "w") as f: f.write(str(now))

        import httpx
        u = settings.SMSGATE_USERNAME or ""
        p = settings.SMSGATE_PASSWORD or ""
        if not u or not p: return

        auth = base64.b64encode(f"{u}:{p}".encode()).decode()
        headers = {"Content-Type": "application/json", "Authorization": f"Basic {auth}"}
        url = "https://api.sms-gate.app/3rdparty/v1/inbox"

        async with httpx.AsyncClient(timeout=httpx.Timeout(20)) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200: return

            try: data = resp.json()
            except: return

            messages = []
            if isinstance(data, list): messages = data
            elif isinstance(data, dict): messages = data.get("messages") or data.get("data") or data.get("inbox") or []

            if not messages: return

            from app.services.sms_service import SMSService
            from sqlalchemy import select
            from app.models.conversation import Message

            async with async_session_factory() as db:
                svc = SMSService(db)
                count = 0
                for msg in messages[:20]:
                    snd = (msg.get("sender") or msg.get("from") or msg.get("phoneNumber")
                           or msg.get("number") or msg.get("address") or "")
                    txt = (msg.get("text") or msg.get("message") or msg.get("body")
                           or msg.get("content") or "")
                    mid = msg.get("messageId") or msg.get("id") or msg.get("_id") or ""
                    if snd and txt and txt.strip() and mid:
                        ex = await db.execute(
                            select(Message).where(Message.idempotency_key == f"inbound-{mid}"))
                        if not ex.scalar_one_or_none():
                            await svc.process_inbound_message(snd, txt, {"messageId": mid})
                            count += 1
                    elif snd and txt and txt.strip():
                        await svc.process_inbound_message(snd, txt)
                        count += 1
                if count:
                    await db.commit()
                    logger.info(f"AUTO-POLL: {count} new message(s)")

            # Process due scheduled messages
            from app.models.scheduled import ScheduledMessage
            from sqlalchemy import func as sqla_func
            from datetime import datetime as dt, timezone as tz
            async with async_session_factory() as db:
                due = await db.execute(
                    select(ScheduledMessage).where(
                        ScheduledMessage.status == "pending",
                        ScheduledMessage.schedule_at <= dt.now(tz.utc)
                    ).limit(5))
                scheduled = due.scalars().all()
                if scheduled:
                    for sm in scheduled:
                        try:
                            resp2 = await client.post(
                                "https://api.sms-gate.app/3rdparty/v1/messages?skipPhoneValidation=true",
                                headers=headers,
                                json={"textMessage": {"text": sm.body}, "phoneNumbers": [sm.phone_number],
                                      "simNumber": sm.sim_number, "ttl": 3600})
                            if resp2.status_code < 400:
                                sm.status = "sent"
                            else:
                                sm.status = "failed"
                                try: sm.error = resp2.json().get("message", str(resp2.status_code))
                                except: sm.error = f"HTTP {resp2.status_code}"
                            sm.executed_at = dt.now(tz.utc)
                        except Exception as e2:
                            sm.status = "failed"; sm.error = str(e2)[:500]
                    await db.commit()
                    logger.info(f"SCHEDULED: processed {len(scheduled)} messages")

    except Exception as e:
        logger.warning(f"AUTO-POLL: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try: await init_db(); logger.info("DB ready")
    except Exception as e: logger.warning("init_db: %s", e)
    yield

app = FastAPI(title=settings.APP_NAME, version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins_list,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/api/v1/health")
async def health(bg: BackgroundTasks):
    bg.add_task(_auto_poll_inbox)
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
async def manifest():
    return JSONResponse({"name":"SMS SENDER","short_name":"SMS SENDER","start_url":"/","display":"standalone","orientation":"portrait-primary","background_color":"#111827","theme_color":"#2563eb","icons":[{"src":"/icon-192.png","sizes":"192x192","type":"image/png","purpose":"any maskable"},{"src":"/icon-512.png","sizes":"512x512","type":"image/png","purpose":"any maskable"}]})

@app.get("/sw.js")
async def swjs():
    p = os.path.join(PUBLIC_DIR, "sw.js")
    return FileResponse(p, media_type="application/javascript") if os.path.isfile(p) else Response("", 404)

@app.get("/icon-192.png")
async def i192():
    p = os.path.join(PUBLIC_DIR, "icon-192.png")
    return FileResponse(p, media_type="image/png") if os.path.isfile(p) else Response("", 404)

@app.get("/icon-512.png")
async def i512():
    p = os.path.join(PUBLIC_DIR, "icon-512.png")
    return FileResponse(p, media_type="image/png") if os.path.isfile(p) else Response("", 404)

@app.get("/favicon.svg")
async def fav():
    p = os.path.join(PUBLIC_DIR, "favicon.svg")
    return FileResponse(p, media_type="image/svg+xml") if os.path.isfile(p) else Response("", 404)

if os.path.isdir(FRONTEND_DIR):
    ad = os.path.join(FRONTEND_DIR, "assets")
    if os.path.isdir(ad): app.mount("/assets", StaticFiles(directory=ad), name="assets")
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
    @app.get("/{fp:path}", response_class=HTMLResponse)
    async def spa(fp: str = ""):
        if fp.startswith(("api/","assets/","docs","manifest","sw.js","icon-","favicon")): return JSONResponse({"detail":"Not Found"}, 404)
        i = os.path.join(FRONTEND_DIR, "index.html")
        return HTMLResponse(content=open(i).read()) if os.path.isfile(i) else JSONResponse({"detail":"No frontend"}, 404)
else:
    @app.get("/{fp:path}", response_class=HTMLResponse)
    async def spa(fp: str = ""): return HTMLResponse("<h1>SMS SENDER API</h1>")
