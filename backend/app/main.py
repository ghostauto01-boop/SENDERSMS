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
    """Update delivery statuses for pending messages and process scheduled sends."""
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
        from app.services.gateway_dispatch import poll_status_dispatch

        async with async_session_factory() as db:
            msgs = await db.execute(
                select(Message).where(
                    Message.provider_message_id.isnot(None),
                    Message.status.in_(("sent","sending","queued"))
                ).limit(100))
            # Poll each gateway for the messages IT sent, so switching the
            # active gateway never abandons in-flight delivery receipts.
            ids_by_provider: dict = {}
            for m in msgs.scalars().all():
                ids_by_provider.setdefault(m.provider or "smsgate", []).append(m.provider_message_id)
            ids = [mid for group in ids_by_provider.values() for mid in group]
            if ids:
                results = await poll_status_dispatch(db, ids_by_provider)
                count = 0
                for r in results:
                    mr = await db.execute(select(Message).where(Message.provider_message_id == r["provider_message_id"]))
                    m = mr.scalar_one_or_none()
                    if m and m.status != r["status"]:
                        m.status = r["status"]
                        if r["status"] == "delivered":
                            from datetime import datetime as dt, timezone as tz
                            m.delivered_at = dt.now(tz.utc)
                        elif r["status"] in ("failed", "cancelled") and not m.failed_at:
                            from datetime import datetime as dt, timezone as tz
                            m.failed_at = dt.now(tz.utc)
                        count += 1
                if count:
                    await db.commit()
                    logger.info(f"STATUS: updated {count} messages")

        # Always process schedules even when no delivery statuses to poll
        await _process_scheduled()
        await _launch_scheduled_campaigns()
        await _process_due_followups()
        # Fallback inline sender for running campaigns when Celery worker is
        # asleep (free tier) or Redis unreachable — otherwise campaigns stay
        # “running” with pending contacts forever.
        await _process_running_campaigns_inline()
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

async def _process_due_followups():
    """Send due follow-ups when this deployment has no awake Celery worker."""
    try:
        from app.tasks.campaign_tasks import process_due_followups_async

        processed = await process_due_followups_async(send_inline=True)
        if processed:
            logger.info("FOLLOW-UPS: processed %s", processed)
    except Exception as exc:
        logger.warning("Due follow-ups: %s", exc)


async def _process_running_campaigns_inline():
    """Process running campaigns when a Celery worker is unavailable/asleep.

    This delegates to the same sequence-aware engine as Celery instead of
    maintaining a second direct-send implementation that ignored sequences.
    """
    try:
        from sqlalchemy import select
        from app.models.campaign import Campaign
        from app.tasks.campaign_tasks import process_campaign_batch_async

        async with async_session_factory() as db:
            campaign_ids = list(
                (
                    await db.execute(
                        select(Campaign.id)
                        .where(Campaign.status == "running")
                        .order_by(Campaign.id)
                        .limit(3)
                    )
                ).scalars().all()
            )

        for campaign_id in campaign_ids:
            processed = await process_campaign_batch_async(
                campaign_id, send_inline=True, batch_size=10
            )
            if processed:
                logger.info(
                    "CAMPAIGN inline: campaign %s processed %s contacts",
                    campaign_id,
                    processed,
                )
    except Exception as exc:
        logger.warning("Inline campaign process: %s", exc)

async def _process_scheduled():
    """Send due scheduled messages and mirror them into the normal Message/Inbox tables.

    Each ScheduledMessage becomes one Message row (outgoing). That way:
    - Sent scheduled messages appear in the inbox chat with a double-check.
    - Failed scheduled messages appear as failed bubbles AND in the Scheduled->Failed tab.
    - Delivery receipts via poll/webhook can update the Message row as usual.
    """
    try:
        from app.services.gateway_dispatch import get_active_gateway, send_sms_dispatch
        from app.models.scheduled import ScheduledMessage
        from app.models.contact import Contact
        from app.models.conversation import Conversation, Message
        from app.models.suppression import SuppressionEntry
        from sqlalchemy import select
        from datetime import datetime as dt, timezone as tz
        import json, uuid
        from app.utils.templating import render_template
        from app.utils.phone import count_sms_segments
        async with async_session_factory() as db:
            due = await db.execute(select(ScheduledMessage).where(
                ScheduledMessage.status == "pending",
                ScheduledMessage.schedule_at <= dt.now(tz.utc)).order_by(ScheduledMessage.schedule_at.asc()).limit(10))
            scheduled = due.scalars().all()
            if not scheduled:
                return
            active_provider = await get_active_gateway(db)
            for sm in scheduled:
                try:
                    # Resolve contact (create if phone-only)
                    contact = None
                    if sm.contact_id:
                        cr = await db.execute(select(Contact).where(Contact.id == sm.contact_id))
                        contact = cr.scalar_one_or_none()
                    if not contact and sm.phone_number:
                        cr = await db.execute(select(Contact).where(Contact.phone_number == sm.phone_number))
                        contact = cr.scalar_one_or_none()
                        if not contact and sm.phone_number:
                            contact = Contact(phone_number=sm.phone_number, country="Nigeria", lead_status="new", source="scheduled")
                            db.add(contact)
                            await db.flush()

                    # Check opt-out / suppression before touching gateway
                    if contact and contact.is_opted_out:
                        sm.status = "failed"
                        sm.error = "Contact has opted out (STOP)"
                        sm.executed_at = dt.now(tz.utc)
                        # also create a failed Message so inbox shows why it didn't go
                        if contact:
                            await _create_scheduled_message_row(db, sm, contact, "failed", sm.error, provider=active_provider)
                        continue
                    if sm.phone_number:
                        sup = await db.execute(select(SuppressionEntry).where(SuppressionEntry.phone_number == sm.phone_number))
                        if sup.scalar_one_or_none():
                            sm.status = "failed"
                            sm.error = "Number is on suppression list"
                            sm.executed_at = dt.now(tz.utc)
                            if contact:
                                await _create_scheduled_message_row(db, sm, contact, "failed", sm.error, provider=active_provider)
                            continue

                    # Personalize body if we have a contact
                    body = sm.body
                    if contact:
                        try:
                            body = render_template(body, contact)
                        except Exception:
                            pass

                    # Ensure conversation exists so inbox thread is visible
                    conv = None
                    if contact:
                        cr = await db.execute(select(Conversation).where(Conversation.contact_id == contact.id).order_by(Conversation.id).limit(1))
                        conv = cr.scalars().first()
                        if not conv:
                            conv = Conversation(contact_id=contact.id, status="active")
                            db.add(conv)
                            await db.flush()

                    # Create the Message row first (queued), then call gateway
                    segment_count = 1
                    char_count = len(body)
                    try:
                        char_count, segment_count = count_sms_segments(body)
                    except Exception:
                        pass

                    msg = None
                    if contact and conv:
                        msg = Message(
                            conversation_id=conv.id,
                            contact_id=contact.id,
                            direction="outgoing",
                            body=body,
                            segment_count=segment_count,
                            char_count=char_count,
                            status="sending",
                            provider=active_provider,
                            idempotency_key=f"scheduled-{sm.id}-{uuid.uuid4().hex[:8]}",
                        )
                        db.add(msg)
                        await db.flush()

                    # Call gateway
                    target_phone = sm.phone_number or (contact.phone_number if contact else "")
                    provider_name, r = await send_sms_dispatch(db, target_phone, body, sm.sim_number or 1)
                    if msg:
                        msg.provider = provider_name

                    if r.get("success"):
                        sm.status = "sent"
                        sm.error = None
                        if msg:
                            msg.status = "sent"
                            msg.provider_message_id = r.get("provider_message_id", "")
                            msg.sent_at = dt.now(tz.utc)
                            msg.provider_response = json.dumps(r.get("raw")) if r.get("raw") else None
                            sm.message_id = msg.id
                            # update conversation preview
                            conv.message_count = (conv.message_count or 0) + 1
                            conv.last_message_preview = body[:100]
                            conv.last_message_at = dt.now(tz.utc)
                            contact.messages_sent = (contact.messages_sent or 0) + 1
                            contact.last_contacted_at = dt.now(tz.utc)
                        else:
                            sm.message_id = None
                    else:
                        err = (r.get("error") or "Gateway rejected message")[:500]
                        sm.status = "failed"
                        sm.error = err
                        if msg:
                            msg.status = "failed"
                            msg.last_error = err
                            msg.failed_at = dt.now(tz.utc)
                            msg.provider_response = json.dumps(r.get("raw")) if r.get("raw") else None
                            sm.message_id = msg.id
                            if conv:
                                conv.message_count = (conv.message_count or 0) + 1
                                conv.last_message_preview = body[:100]
                                conv.last_message_at = dt.now(tz.utc)

                    sm.executed_at = dt.now(tz.utc)
                except Exception as ie:
                    logger.warning(f"Scheduled {sm.id} handling error: {ie}")
                    sm.status = "failed"
                    sm.error = str(ie)[:500]
                    sm.executed_at = dt.now(tz.utc)

            await db.commit()
            sent = sum(1 for s in scheduled if s.status == "sent")
            failed = sum(1 for s in scheduled if s.status == "failed")
            logger.info(f"SCHEDULED: {len(scheduled)} messages ({sent} sent, {failed} failed)")
    except Exception as e:
        logger.warning(f"Scheduled: {e}")

async def _create_scheduled_message_row(db, sm, contact, status, error, provider="smsgate"):
    """Helper: create a failed Message bubble for a scheduled that never reached gateway."""
    try:
        from app.models.conversation import Conversation, Message
        from sqlalchemy import select
        import uuid, json
        from app.utils.phone import count_sms_segments
        from datetime import datetime as dt, timezone as tz
        cr = await db.execute(select(Conversation).where(Conversation.contact_id == contact.id).order_by(Conversation.id).limit(1))
        conv = cr.scalars().first()
        if not conv:
            conv = Conversation(contact_id=contact.id, status="active")
            db.add(conv)
            await db.flush()
        try:
            cc, sc = count_sms_segments(sm.body)
        except Exception:
            cc, sc = len(sm.body), 1
        msg = Message(
            conversation_id=conv.id,
            contact_id=contact.id,
            direction="outgoing",
            body=sm.body,
            segment_count=sc,
            char_count=cc,
            status=status,
            provider=provider,
            last_error=error,
            failed_at=dt.now(tz.utc) if status == "failed" else None,
            idempotency_key=f"scheduled-{sm.id}-{uuid.uuid4().hex[:8]}",
        )
        db.add(msg)
        await db.flush()
        sm.message_id = msg.id
        conv.message_count = (conv.message_count or 0) + 1
        conv.last_message_preview = sm.body[:100]
        conv.last_message_at = dt.now(tz.utc)
    except Exception as e:
        logger.warning(f"_create_scheduled_message_row: {e}")

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

    # Uvicorn binds the listen socket only AFTER this startup section
    # returns (i.e. we reach `yield`). Blocking here on Postgres / SMS-Gate
    # is what made Render report "no open ports" and time the deploy out.
    async def _boot():
        try:
            await asyncio.wait_for(init_db(), timeout=45)
            logger.info("DB ready")
        except Exception as e:
            logger.warning("init_db: %s", e)
        try:
            await asyncio.wait_for(_startup_webhook(), timeout=20)
        except Exception as e:
            logger.warning("webhook: %s", e)

    boot = asyncio.create_task(_boot())

    poller = None
    if settings.ENABLE_INLINE_POLLER:
        poller = asyncio.create_task(_poll_loop())
        logger.info("Inline poller started (every %ss)", settings.INLINE_POLL_INTERVAL)

    yield

    boot.cancel()
    if poller:
        poller.cancel()
        try:
            await poller
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await boot
        except (asyncio.CancelledError, Exception):
            pass

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
