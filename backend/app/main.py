"""FastAPI — webhook auto-register, status poll, scheduled, PWA, SPA."""
import os, logging, time, uuid, traceback
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers, MutableHeaders
from app.config import settings
from app.database import init_db, async_session_factory
from app import db_health
from app.poll_scheduler import PollActivity, next_poll_delay
from app.utils.urls import bind_request_base, clear_request_base

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Signals for the idle-aware inline poller (see app.poll_scheduler).
poll_activity = PollActivity()


class MCPConnectorCorsMiddleware:
    """Allow hosted MCP clients to preflight only the public MCP surface.

    The app-wide CORS policy is intentionally limited to the operator's
    configured frontend origins. ChatGPT and Claude run their connectors from
    hosted browser contexts, so their Origin values need a separate, explicit
    allowlist rather than widening CORS for contacts, campaigns, and the rest
    of the CRM API.
    """

    _METHODS = "GET, POST, DELETE, OPTIONS"
    _HEADERS = (
        "Accept, Authorization, Content-Type, Last-Event-ID, Mcp-Protocol-Version, "
        "Mcp-Session-Id, X-Mcp-Selftest"
    )
    _EXPOSE = (
        "Content-Type, Content-Length, Location, Mcp-Session-Id, "
        "Mcp-Protocol-Version, WWW-Authenticate"
    )

    def __init__(self, app):
        self.app = app
        self.origins = {origin.rstrip("/") for origin in settings.mcp_cors_origins_list}

    @staticmethod
    def _is_mcp_surface(path: str) -> bool:
        # ChatGPT and Claude discover the shared authorization server at the
        # deployment root, so their registration/token requests land at
        # /oauth/* (not /connectors/<client>/oauth/*). Include both forms and
        # the slash variants used by Streamable HTTP clients.
        normalized = path.rstrip("/") or "/"
        return (
            normalized in {"/mcp", "/oauth"}
            or normalized.startswith("/oauth/")
            or path.startswith("/connectors/")
            or path.startswith("/.well-known/oauth-")
            or normalized == "/.well-known/openid-configuration"
            or normalized.startswith("/.well-known/openid-configuration/")
        )

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_headers = Headers(scope=scope)
        origin = (request_headers.get("origin") or "").rstrip("/")
        path = str(scope.get("path") or "")
        if not origin or origin not in self.origins or not self._is_mcp_surface(path):
            await self.app(scope, receive, send)
            return

        cors_headers = {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": self._METHODS,
            "Access-Control-Allow-Headers": self._HEADERS,
            "Access-Control-Expose-Headers": self._EXPOSE,
        }

        if (
            scope.get("method", "").upper() == "OPTIONS"
            and request_headers.get("access-control-request-method")
        ):
            cors_headers["Access-Control-Max-Age"] = "600"
            cors_headers["Vary"] = (
                "Origin, Access-Control-Request-Method, Access-Control-Request-Headers"
            )
            response = Response(status_code=204, headers=cors_headers)
            await response(scope, receive, send)
            return

        async def send_with_cors(message):
            if message.get("type") == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in cors_headers.items():
                    headers[key] = value
                vary = [part.strip() for part in (headers.get("Vary") or "").split(",") if part.strip()]
                if not any(part.lower() == "origin" for part in vary):
                    vary.append("Origin")
                headers["Vary"] = ", ".join(vary)
            await send(message)

        await self.app(scope, receive, send_with_cors)


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
    # Same for CallGate (phone calls) — but only when it is configured, since
    # the handset's local server is often unreachable from here at boot time.
    try:
        from app.services.system_settings import (
            CALLGATE_WEBHOOK_REGISTERED, get_callgate_settings,
        )
        from app.utils.urls import public_base_url

        async with async_session_factory() as db:
            cfg = await get_callgate_settings(db)
            base = (public_base_url() or "").rstrip("/")
            if cfg["configured"] and base:
                url = f"{base}/api/v1/webhooks/callgate"
                if await get_setting(db, CALLGATE_WEBHOOK_REGISTERED) != url:
                    from app.providers.callgate import register_webhook_direct as _reg_call
                    r = await _reg_call(url, None, cfg["base_url"],
                                        cfg["username"], cfg["password"])
                    if r.get("success"):
                        await set_setting(db, CALLGATE_WEBHOOK_REGISTERED, url,
                                          description="Call webhook URL registered on the phone")
                        await db.commit()
                        logger.info("CallGate webhook auto-registered at %s", url)
                    else:
                        logger.warning("CallGate webhook registration failed: %s", r)
    except Exception as e:
        logger.warning(f"CallGate webhook: {e}")

async def _poll() -> int:
    """Update delivery statuses for pending messages and process scheduled sends.

    Returns a rough count of things touched this cycle (statuses updated,
    messages sent, campaigns launched ...). ``0`` means nothing was due. The
    scheduler uses that to decide whether the next cycle can wait.

    A database outage (Neon suspended, host unreachable ...) is recorded in
    ``db_health.status`` and re-raised as ``_DatabaseDown`` so the loop backs
    off instead of retrying every 30 seconds.
    """
    work = 0
    try:
        from app.services.system_settings import LAST_POLL, get_float, set_setting

        now = time.time()
        try:
            async with async_session_factory() as db:
                if now - await get_float(db, LAST_POLL, 0.0) < 15:
                    return 0
                await set_setting(db, LAST_POLL, str(now),
                                  description="Unix time of the last delivery-status poll")
                await db.commit()
        except Exception as exc:
            if db_health.is_db_error(exc):
                db_health.status.note_error(exc)
                raise _DatabaseDown() from exc
            raise
        db_health.status.note_ok()

        from sqlalchemy import select
        from app.models.conversation import Message
        from app.providers.smsgate import poll_status_for_ids

        async with async_session_factory() as db:
            msgs = await db.execute(
                select(Message).where(
                    Message.provider_message_id.isnot(None),
                    Message.status.in_(("sent","sending","queued")),
                    # Provider ids come from two different systems now: SMS-Gate
                    # ids are pollable, Brevo ids are settled by its webhook.
                    # Polling an email id against SMS-Gate would 404 forever.
                    Message.channel != "email",
                ).limit(100))
            ids = [m.provider_message_id for m in msgs.scalars().all()]
            if ids:
                # Statuses still settling — keep polling at full speed.
                work += 1
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
                        elif r["status"] in ("failed", "cancelled") and not m.failed_at:
                            from datetime import datetime as dt, timezone as tz
                            m.failed_at = dt.now(tz.utc)
                        count += 1
                if count:
                    await db.commit()
                    logger.info(f"STATUS: updated {count} messages")
                    work += count

        # Always process schedules even when no delivery statuses to poll
        work += await _process_scheduled()
        work += await _launch_scheduled_campaigns()
        work += await _process_due_followups()
        work += await _process_campaign_followups()
        work += await _process_meeting_reminders()
        # Fallback inline sender for running campaigns when Celery worker is
        # asleep (free tier) or Redis unreachable — otherwise campaigns stay
        # “running” with pending contacts forever.
        work += await _process_running_campaigns_inline()
        # Bounce circuit breaker: pause any email campaign over the bounce limit.
        # The webhook checks as events arrive; this is the backstop (and the
        # no-worker fallback for the Celery beat task of the same name).
        work += await _check_circuit_breakers_inline()
        # Sweep any queued messages that were rate-limited / deferred (or that
        # a dead broker left behind). Uses the same atomic claim as Celery so
        # the two can never double-send the same message.
        work += await _process_queued_messages_inline()
        # SMS Ads Manager: dispatch, follow-up automation and always-on
        # audience refresh. Uses the same no-worker fallback pattern as the
        # legacy campaign sweep above.
        work += await _process_ads_manager()
        # Connected mailboxes: pull replies (and the ones Gmail filed in Spam)
        # into the email inbox. No-worker fallback for the Celery beat task.
        work += await _process_mailbox_sync_inline()
    except _DatabaseDown:
        raise
    except Exception as e:
        logger.warning(f"Poll: {e}")
    return work


class _DatabaseDown(Exception):
    """Raised by _poll when the database itself is unavailable."""


async def _pending_work_snapshot() -> tuple[bool, "float | None"]:
    """Cheap look at what is queued so the poller can sleep when idle.

    Returns ``(has_pending_work, next_due_at)``:

    * ``has_pending_work`` — something needs attention *now* (a running
      campaign, queued outgoing messages, delivery statuses still settling).
    * ``next_due_at`` — unix time of the earliest future job among scheduled
      messages, scheduled campaigns, follow-ups and upcoming meetings, or
      ``None``.

    Only the simple time-indexed tables are consulted; the ads manager and
    campaign follow-up chains are swept on every cycle anyway and tolerate
    the idle interval.
    """
    from datetime import datetime as dt, timezone as tz
    from sqlalchemy import select, func
    from app.models.campaign import Campaign
    from app.models.conversation import Message
    from app.models.scheduled import ScheduledMessage
    from app.models.followup import FollowUp
    from app.models.meeting import Meeting

    now = dt.now(tz.utc)
    async with async_session_factory() as db:
        running = (await db.execute(
            select(func.count()).select_from(Campaign).where(Campaign.status == "running")
        )).scalar() or 0
        queued = (await db.execute(
            select(func.count()).select_from(Message).where(
                Message.direction == "outgoing", Message.status == "queued",
                _not_held_by_paused_campaign(Message))
        )).scalar() or 0
        settling = (await db.execute(
            select(func.count()).select_from(Message).where(
                Message.provider_message_id.isnot(None),
                Message.status.in_(("sent", "sending", "queued")),
                Message.channel != "email")
        )).scalar() or 0

        candidates = []
        for stmt in (
            select(func.min(ScheduledMessage.schedule_at)).where(ScheduledMessage.status == "pending"),
            select(func.min(Campaign.scheduled_at)).where(Campaign.status == "scheduled"),
            select(func.min(FollowUp.scheduled_at)).where(FollowUp.status == "pending"),
        ):
            value = (await db.execute(stmt)).scalar()
            if value is not None:
                if value.tzinfo is None:
                    value = value.replace(tzinfo=tz.utc)
                candidates.append(value.timestamp())

        # Meeting reminders fire N minutes *before* the meeting, so the due
        # time is starts_at - N for every reminder not sent yet.
        from app.services.meeting_service import reminder_minutes, reminders_sent
        meetings = (await db.execute(
            select(Meeting).where(
                Meeting.status.in_(("scheduled", "confirmed")),
                Meeting.send_sms_reminder == 1,
                Meeting.starts_at > now,
            ).order_by(Meeting.starts_at.asc()).limit(100)
        )).scalars().all()
        for meeting in meetings:
            sent = reminders_sent(meeting)
            starts = meeting.starts_at
            if starts.tzinfo is None:
                starts = starts.replace(tzinfo=tz.utc)
            for minutes in reminder_minutes(meeting):
                if str(minutes) in sent:
                    continue
                candidates.append(starts.timestamp() - minutes * 60)

    has_pending = bool(running or queued or settling)
    next_due = min(candidates) if candidates else None
    return has_pending, next_due


async def _process_queued_messages_inline() -> int:
    """Send queued outgoing messages when no Celery worker picks them up.

    This is the safety net behind sending limits: a rate-limited message is
    left ``queued`` and sent here once its window/pacing slot opens. It also
    fixes the older dead-end where an auto-reply "left queued" because Redis
    was unreachable was never actually delivered.
    """
    try:
        from sqlalchemy import select as _select
        from app.models.conversation import Message as _Message
        from app.tasks.sms_tasks import _send_one

        async with async_session_factory() as db:
            rows = await db.execute(
                _select(_Message.id)
                .where(
                    _Message.direction == "outgoing",
                    _Message.status == "queued",
                    _not_held_by_paused_campaign(_Message),
                )
                .order_by(_Message.created_at.asc())
                .limit(20)
            )
            ids = list(rows.scalars().all())

        sent = 0
        for mid in ids:
            try:
                result = await _send_one(mid, final_on_failure=True)
                if result is False:
                    sent += 1
                elif isinstance(result, int):
                    # Still rate limited; leave for the next sweep.
                    break
            except Exception as exc:
                logger.warning("Queued sweep: message %s error: %s", mid, exc)
        if sent:
            logger.info("QUEUED SWEEP: sent %s deferred message(s)", sent)
        return sent
    except Exception as exc:
        logger.warning("Queued sweep: %s", exc)
        return 0


async def _process_ads_manager() -> int:
    """Drive SMS Ads Manager campaigns without a Celery worker.

    Mirrors the legacy inline campaign sweep: claim work atomically, dispatch a
    small batch, and let the shared sending limits do the pacing. Safe to run
    alongside the Celery beat task -- every row is claimed with a conditional
    UPDATE, so the two can never double-send.
    """
    try:
        from app.tasks.ads_tasks import run_ads_cycle

        result = await run_ads_cycle(send_inline=True)
        if result.get("sent") or result.get("followups"):
            logger.info("ADS: %s", result)
        return int(result.get("sent") or 0) + int(result.get("followups") or 0)
    except Exception as exc:
        logger.warning("Ads manager sweep: %s", exc)
        return 0


async def _process_mailbox_sync_inline() -> int:
    """Import email replies without a Celery worker.

    The same job runs on beat (``app.tasks.mailbox_tasks.sync_mailboxes``). It is
    repeated here for the deployments that have no worker at all — the Render free
    tier, or a Docker compose without the Celery service — because a reply that
    only arrives when a worker happens to be awake is the exact complaint this
    feature exists to fix.

    Safe to run alongside beat: each mailbox carries its own ``last_sync_at`` and
    poll interval, and message storage is idempotent on the provider message id,
    so two overlapping passes store one copy.
    """
    try:
        from app.services import mailbox_service

        async with async_session_factory() as db:
            results = await mailbox_service.sync_due_mailboxes(db)
            await db.commit()
        imported = sum(int(r.get("stored") or 0) for r in results if isinstance(r, dict))
        rescued = sum(int(r.get("rescued") or 0) for r in results if isinstance(r, dict))
        if imported or rescued:
            logger.info("MAILBOX: imported %s reply(ies), rescued %s from Spam", imported, rescued)
        return imported + rescued
    except Exception as exc:  # noqa: BLE001 — one unreachable mailbox must not stop the poll
        logger.warning("Mailbox sweep: %s", exc)
        return 0


async def _launch_scheduled_campaigns() -> int:
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
        return int(launched or 0)
    except Exception as e:
        logger.warning(f"Scheduled campaigns: {e}")
        return 0

async def _process_due_followups() -> int:
    """Send due follow-ups when this deployment has no awake Celery worker."""
    try:
        from app.tasks.campaign_tasks import process_due_followups_async

        processed = await process_due_followups_async(send_inline=True)
        if processed:
            logger.info("FOLLOW-UPS: processed %s", processed)
        return int(processed or 0)
    except Exception as exc:
        logger.warning("Due follow-ups: %s", exc)
        return 0


async def _process_campaign_followups() -> int:
    """Send campaign follow-ups whose wait time has elapsed.

    Runs in the same inline poller as the other sweeps so follow-up chains keep
    moving on deployments with no awake Celery worker.
    """
    try:
        from app.services.campaign_followup_service import process_due_campaign_followups

        totals = await process_due_campaign_followups()
        if totals.get("sent") or totals.get("stopped"):
            logger.info(
                "CAMPAIGN FOLLOW-UPS: sent %s, stopped %s across %s rule(s)",
                totals["sent"], totals["stopped"], totals["rules"],
            )
        return int(totals.get("sent") or 0) + int(totals.get("stopped") or 0)
    except Exception as exc:
        logger.warning("Campaign follow-ups: %s", exc)
        return 0


async def _process_meeting_reminders() -> int:
    """Fire calendar reminders whose window has opened.

    Runs in the same inline poller as the other sweeps so meeting reminders go
    out on deployments with no awake Celery worker.
    """
    try:
        from app.services.meeting_service import process_due_meeting_reminders

        totals = await process_due_meeting_reminders()
        if totals.get("sent") or totals.get("skipped"):
            logger.info(
                "MEETING REMINDERS: sent %s, skipped %s across %s meeting(s)",
                totals["sent"], totals["skipped"], totals["checked"],
            )
        return int(totals.get("sent") or 0)
    except Exception as exc:
        logger.warning("Meeting reminders: %s", exc)
        return 0


async def _check_circuit_breakers_inline() -> int:
    """Run the circuit-breaker sweep when no Celery worker is awake to do it."""
    try:
        from app.tasks.campaign_tasks import check_circuit_breakers_async

        tripped = await check_circuit_breakers_async()
        for t in tripped:
            logger.warning("CIRCUIT BREAKER (inline): paused %s %s: %s", t["kind"], t["id"], t.get("reason"))
        return len(tripped)
    except Exception as exc:
        logger.warning("Circuit breaker sweep: %s", exc)
        return 0


def _not_held_by_paused_campaign(message_model):
    """SQL condition: the message's campaign is not paused.

    Mail created before a pause is *held* (left queued) until the campaign
    resumes. The sweeps must not keep picking it up -- it would starve everything
    behind it and keep the poller awake forever.
    """
    from sqlalchemy import or_ as _or, select as _select
    from app.models.ads import AdsCampaign
    from app.models.campaign import Campaign

    paused_classic = _select(Campaign.id).where(Campaign.status == "paused")
    paused_ads = _select(AdsCampaign.id).where(AdsCampaign.status == "paused")
    from sqlalchemy import and_ as _and

    return _and(
        _or(message_model.campaign_id.is_(None), message_model.campaign_id.not_in(paused_classic)),
        _or(message_model.ads_campaign_id.is_(None), message_model.ads_campaign_id.not_in(paused_ads)),
    )


async def _process_running_campaigns_inline() -> int:
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

        total = 0
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
                total += int(processed or 0)
        # A running campaign is pending work even when this batch sent nothing
        # (e.g. paused by the sending window) — keep the poller awake for it.
        return total or (1 if campaign_ids else 0)
    except Exception as exc:
        logger.warning("Inline campaign process: %s", exc)
        return 0

async def _process_scheduled() -> int:
    """Send due scheduled messages and mirror them into the normal Message/Inbox tables.

    Each ScheduledMessage becomes one Message row (outgoing). That way:
    - Sent scheduled messages appear in the inbox chat with a double-check.
    - Failed scheduled messages appear as failed bubbles AND in the Scheduled->Failed tab.
    - Delivery receipts via poll/webhook can update the Message row as usual.
    """
    try:
        from app.providers.smsgate import send_sms_direct
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
                return 0

            # Respect sending limits / pacing. When the next slot is not open,
            # leave the messages pending and try again on the next poll cycle —
            # this is what spreads a big scheduled blast evenly through the day
            # instead of firing it all at once.
            from app.services.sending_limits import SendingGate
            slot = await SendingGate(db).check()
            if not slot["allowed"]:
                logger.info(
                    "SCHEDULED: rate limited (%s); deferring %s message(s)",
                    slot["reason"], len(scheduled),
                )
                return len(scheduled)

            for sm in scheduled:
                try:
                    # Email schedules take their own path: they deliver through
                    # Brevo and never touch the SIM/gateway logic below.
                    if (sm.channel or "sms") == "email":
                        await _process_scheduled_email(db, sm)
                        continue
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
                            await _create_scheduled_message_row(db, sm, contact, "failed", sm.error)
                        continue
                    if sm.phone_number:
                        sup = await db.execute(select(SuppressionEntry).where(SuppressionEntry.phone_number == sm.phone_number))
                        if sup.scalar_one_or_none():
                            sm.status = "failed"
                            sm.error = "Number is on suppression list"
                            sm.executed_at = dt.now(tz.utc)
                            if contact:
                                await _create_scheduled_message_row(db, sm, contact, "failed", sm.error)
                            continue

                    # Personalize body if we have a contact
                    body = sm.body
                    if contact:
                        try:
                            from app.services.variable_service import render_for_contact
                            body = await render_for_contact(db, body, contact)
                        except Exception:
                            # Never let a registry lookup stop a scheduled send;
                            # fall back to the plain renderer.
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
                            provider="smsgate",
                            idempotency_key=f"scheduled-{sm.id}-{uuid.uuid4().hex[:8]}",
                        )
                        db.add(msg)
                        await db.flush()

                    # Call gateway
                    target_phone = sm.phone_number or (contact.phone_number if contact else "")
                    r = await send_sms_direct(target_phone, body, sm.sim_number or 1)

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
            return len(scheduled)
    except Exception as e:
        logger.warning(f"Scheduled: {e}")
    return 0

async def _process_scheduled_email(db, sm) -> None:
    """Deliver one due scheduled EMAIL through the email pipeline.

    Mirrors the SMS branch's bookkeeping: the ``ScheduledMessage`` row is the
    record of intent, a ``Message(channel="email")`` row is what the inbox and
    analytics read, and the two are linked with ``sm.message_id``.
    """
    from datetime import datetime as dt, timezone as tz
    from sqlalchemy import select
    from app.models.contact import Contact
    from app.services import email_service

    if await email_service.get_suppression(db, getattr(sm, "to_address", None)):
        sm.status = "failed"
        sm.error = "Address is on the email suppression list"
        sm.executed_at = dt.now(tz.utc)
        await db.flush()
        return

    contact = None
    if sm.contact_id:
        contact = (
            await db.execute(select(Contact).where(Contact.id == sm.contact_id))
        ).scalar_one_or_none()
    if contact is None and sm.to_address:
        target = email_service.normalize_email(sm.to_address)
        if target:
            from sqlalchemy import func

            contact = (
                await db.execute(select(Contact).where(func.lower(Contact.email) == target))
            ).scalars().first()
            if contact is None:
                contact = Contact(
                    email=target,
                    phone_number=f"email:{target}"[:20],
                    country="Nigeria",
                    lead_status="new",
                    source="scheduled_email",
                )
                db.add(contact)
                await db.flush()

    if contact is None:
        sm.status = "failed"
        sm.error = "No contact or recipient address for this scheduled email"
        sm.executed_at = dt.now(tz.utc)
        await db.flush()
        return

    problem = await email_service.contact_email_problem(db, contact)
    if problem:
        sm.status = "failed"
        sm.error = f"Skipped: {problem}"
        sm.executed_at = dt.now(tz.utc)
        await db.flush()
        return

    account = await email_service.get_account(db, sm.email_account_id)
    message = await email_service.queue_email(
        db, contact,
        subject=sm.subject or sm.body[:80],
        text_body=sm.body,
        html_body=sm.html_body,
        account=account,
        status="sending",
        idempotency_key=f"scheduled-{sm.id}",
        attachments=email_service.load_attachments(getattr(sm, "attachments", None)),
        bulk=bool(sm.list_id),
        outreach=bool(sm.list_id),
        cc=email_service.clean_addresses(getattr(sm, "cc_addresses", None)),
        bcc=email_service.clean_addresses(getattr(sm, "bcc_addresses", None)),
    )
    if message is None:
        sm.status = "failed"
        sm.error = "Could not queue the email (no usable sender account?)"
        sm.executed_at = dt.now(tz.utc)
        await db.flush()
        return

    result = await email_service.deliver(db, message)
    sm.message_id = message.id
    sm.executed_at = dt.now(tz.utc)
    if result.get("success"):
        sm.status = "sent"
        sm.error = None
        contact.emails_sent = (contact.emails_sent or 0) + 1
        contact.last_emailed_at = dt.now(tz.utc)
    else:
        sm.status = "failed"
        sm.error = str(result.get("error"))[:500]
    await db.flush()


async def _create_scheduled_message_row(db, sm, contact, status, error):
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
            provider="smsgate",
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
    """Run _poll() on an idle-aware timer (see app.poll_scheduler).

    Full speed (INLINE_POLL_INTERVAL) while someone is using the app or there
    is work in flight; otherwise sleep until the next known due time, capped
    at INLINE_IDLE_POLL_INTERVAL, so an idle deployment lets the database
    scale to zero instead of burning its monthly quota.
    """
    import asyncio

    delay = float(settings.INLINE_POLL_INTERVAL)
    idle = False
    while True:
        try:
            poll_activity.wake.clear()
            try:
                # Any API request / webhook wakes us early via poll_activity.touch().
                await asyncio.wait_for(poll_activity.wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

            did_work = False
            db_ok = True
            has_pending = False
            next_due = None
            try:
                did_work = (await _poll()) > 0
                try:
                    has_pending, next_due = await _pending_work_snapshot()
                except Exception as exc:
                    if db_health.is_db_error(exc):
                        db_health.status.note_error(exc)
                        db_ok = False
                    else:
                        logger.warning("Poll snapshot: %s", exc)
            except _DatabaseDown:
                db_ok = False

            delay = next_poll_delay(
                interval=settings.INLINE_POLL_INTERVAL,
                idle_interval=settings.INLINE_IDLE_POLL_INTERVAL,
                active_window=settings.INLINE_POLL_ACTIVE_WINDOW,
                now=time.time(),
                last_request_at=poll_activity.last_request_at,
                did_work=did_work,
                has_pending_work=has_pending,
                next_due_at=next_due,
                db_ok=db_ok,
            )
            now_idle = delay > settings.INLINE_POLL_INTERVAL
            if now_idle != idle:
                idle = now_idle
                if idle:
                    logger.info(
                        "Poller idle (%s): next pass in %.0fs so the database can sleep",
                        "database unavailable" if not db_ok else "nothing due, nobody active",
                        delay,
                    )
                else:
                    logger.info("Poller active: every %ss", settings.INLINE_POLL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Poll loop: %s", e)
            delay = float(settings.INLINE_POLL_INTERVAL)

async def _apply_safe_sending_defaults() -> None:
    """One-time: switch the protective sending rules on for a never-configured database."""
    from app.services.sending_limits import apply_safe_defaults

    async with async_session_factory() as db:
        result = await apply_safe_defaults(db)
        await db.commit()
    logger.info("Sending rules safe-defaults step: %s", result)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    # Uvicorn binds the listen socket only AFTER this startup section
    # returns (i.e. we reach `yield`). Blocking here on Postgres / SMS-Gate
    # is what made Render report "no open ports" and time the deploy out.
    async def _boot():
        try:
            await asyncio.wait_for(init_db(), timeout=45)
            db_health.status.note_ok()
            logger.info("DB ready")
        except Exception as e:
            if db_health.is_db_error(e) or isinstance(e, asyncio.TimeoutError):
                kind, msg = db_health.status.note_error(e)
                logger.error("init_db failed (%s): %s", kind, msg)
            else:
                logger.warning("init_db: %s", e)
        try:
            await asyncio.wait_for(_apply_safe_sending_defaults(), timeout=20)
        except Exception as e:
            logger.warning("safe sending defaults: %s", e)
        try:
            await asyncio.wait_for(_startup_webhook(), timeout=20)
        except Exception as e:
            logger.warning("webhook: %s", e)
        try:
            from app.services import verification_jobs

            resumed = await asyncio.wait_for(verification_jobs.resume_pending(), timeout=20)
            if resumed:
                logger.info("Resumed %s unfinished email verification job(s)", resumed)
        except Exception as e:
            logger.warning("verification jobs: %s", e)

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
# GZip large JSON (inbox threads, analytics, contact pages) — cuts payloads ~70%.
app.add_middleware(GZipMiddleware, minimum_size=1000)
# The MCP headers are listed explicitly rather than relying on "*": a browser
# client (claude.ai, or the connector self-test page) sends a credentialed
# request, and the Fetch spec says a wildcard Access-Control-Allow-Headers is
# then treated as the literal header name "*" — the preflight fails and the
# connector shows "could not connect" with no server-side error at all.
# expose_headers matters for the same clients: without it, JavaScript cannot
# read Mcp-Session-Id off the response, so every request after the first looks
# unauthenticated.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=[
        "Authorization", "Content-Type", "Accept", "Accept-Encoding", "Origin",
        "X-Requested-With", "X-CSRF-Token", "Last-Event-ID",
        "Mcp-Session-Id", "Mcp-Protocol-Version", "X-Mcp-Selftest",
    ],
    expose_headers=[
        "Mcp-Session-Id", "Mcp-Protocol-Version", "WWW-Authenticate",
        "Content-Type", "Content-Length", "Location",
    ],
)
# Must wrap the global CORS layer so provider-origin OPTIONS requests are
# handled before the app-wide policy rejects a non-local origin.
app.add_middleware(MCPConnectorCorsMiddleware)


@app.middleware("http")
async def _bind_public_base_url(request, call_next):
    """Learn this deployment's public address from the request itself.

    The MCP connector flow needs absolute URLs (ChatGPT and Claude both reject
    a relative ``resource``), and a deployment that never set PUBLIC_BASE_URL
    used to answer every connector attempt with "PUBLIC_BASE_URL is not set" —
    which is why the AI connector looked broken while the endpoint was fine.

    Binding the request's own scheme + host here means the OAuth metadata, the
    ``WWW-Authenticate`` challenge and the connector self-test all produce
    working absolute URLs with nothing configured. ``PUBLIC_BASE_URL`` still
    wins when it is set (see app.utils.urls), so a custom domain or a tunnel
    keeps working exactly as before.
    """
    bind_request_base(request)
    # Own the identifier here instead of trusting a caller-provided value; it
    # is also the unique key for the private error record.
    request_id = uuid.uuid4().hex
    request.state.request_id = request_id
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        clear_request_base()


@app.middleware("http")
async def _cache_static_assets(request, call_next):
    """Vite emits content-hashed filenames, so /assets/* never changes.

    Marking them immutable lets returning browsers (and the service worker)
    reuse every JS/CSS chunk without a revalidation round-trip, which is most
    of the difference between a 1s and a 5s reload on mobile data.

    The same pass records real API traffic for the idle-aware poller. Health
    probes and static files do not count — an uptime pinger must not keep the
    database awake.
    """
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/v1/health"):
        poll_activity.touch()
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001 — every crash must become JSON
        return await _error_response(request, exc)
    if path.startswith("/assets/"):
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    return response


async def _store_error_record(request: Request, exc: Exception, request_id: str, code: str) -> None:
    """Best-effort persistence of the server-side cause, keyed by request id."""
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-50000:]
    try:
        from app.models.system_error import SystemErrorRecord
        async with async_session_factory() as db:
            db.add(SystemErrorRecord(
                request_id=request_id,
                code=code,
                exception_type=type(exc).__name__[:255],
                message=(str(exc) or type(exc).__name__)[:10000],
                traceback=trace,
                method=request.method[:12],
                path=str(request.url.path)[:1000],
            ))
            await db.commit()
    except Exception as store_exc:  # noqa: BLE001 — error reporting must not mask the original
        logger.error("Could not persist error request_id=%s: %s", request_id, store_exc)


async def _error_response(request: Request, exc: Exception) -> JSONResponse:
    """Return a safe structured error and persist the detailed cause privately."""
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    request.state.request_id = request_id
    headers = {"Cache-Control": "no-store", "X-Request-ID": request_id}
    if db_health.is_db_error(exc):
        payload = db_health.error_payload(exc)
        code = "DATABASE_UNAVAILABLE"
        message = payload["db"]["message"]
        hint = payload["db"]["hint"]
        logger.error(
            "Database error request_id=%s %s %s: %s",
            request_id, request.method, request.url.path, message,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        await _store_error_record(request, exc, request_id, code)
        return JSONResponse(
            status_code=503,
            content={
                "code": code,
                "message": f"Database unavailable: {message}",
                "detail": f"Database unavailable: {message}",
                "field": None,
                "hint": hint,
                "request_id": request_id,
                "error_kind": "database",
                "db": payload.get("db"),
            },
            headers={**headers, "Retry-After": "30"},
        )

    code = "INTERNAL_ERROR"
    logger.error(
        "Unhandled error request_id=%s %s %s",
        request_id, request.method, request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    await _store_error_record(request, exc, request_id, code)
    return JSONResponse(
        status_code=500,
        content={
            "code": code,
            "message": "An unexpected server error occurred.",
            "detail": "Internal server error. Please try again; if it persists, share the request ID with the operator.",
            "field": None,
            "hint": f"Share request_id {request_id} with the operator; see GET /api/v1/system/errors?request_id={request_id}.",
            "request_id": request_id,
        },
        headers=headers,
    )


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    """Standardize expected HTTP errors without dropping the legacy detail key."""
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    detail = exc.detail
    message = str(detail) if not isinstance(detail, dict) else str(detail.get("message", detail))
    codes = {
        400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN",
        404: "NOT_FOUND", 409: "CONFLICT", 413: "PAYLOAD_TOO_LARGE",
        422: "VALIDATION_ERROR", 429: "RATE_LIMITED", 503: "SERVICE_UNAVAILABLE",
    }
    content = {
        "code": detail.get("code", codes.get(exc.status_code, "HTTP_ERROR")) if isinstance(detail, dict) else codes.get(exc.status_code, "HTTP_ERROR"),
        "message": message,
        "detail": message,
        "field": detail.get("field") if isinstance(detail, dict) else None,
        "hint": detail.get("hint") if isinstance(detail, dict) else None,
        "request_id": request_id,
    }
    if isinstance(detail, dict):
        # Structured errors may carry machine-readable extras (the candidates of
        # an ambiguous id, the list of validation problems, ...). They ride along
        # as top-level keys; the envelope above always wins on a name clash.
        for key, value in detail.items():
            content.setdefault(key, value)
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
        headers={**(exc.headers or {}), "Cache-Control": "no-store", "X-Request-ID": request_id},
    )


@app.exception_handler(RequestValidationError)
async def _request_validation_error(request: Request, exc: RequestValidationError):
    """Return field-level validation errors without echoing submitted values."""
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    errors = exc.errors()
    first = errors[0] if errors else {}
    location = first.get("loc") or ()
    field = ".".join(str(part) for part in location if part not in {"body", "query", "path", "header"}) or None
    message = str(first.get("msg") or "Request validation failed")
    detail = f"{field}: {message}" if field else message
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "message": message,
            "detail": detail,
            "field": field,
            "hint": "Check the request fields and try again.",
            "request_id": request_id,
        },
        headers={"Cache-Control": "no-store", "X-Request-ID": request_id},
    )


@app.exception_handler(Exception)
async def _unhandled_error(request: Request, exc: Exception):
    """Fallback for anything that escapes the middleware above."""
    return await _error_response(request, exc)


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


@app.get("/api/v1/health/db")
async def health_db():
    """Database probe. Public, safe, and the first thing to check when every
    page shows an error.

    Runs ``SELECT 1`` and reports a classified, credential-free summary:
    ``{"ok": false, "kind": "quota_exceeded", "message": ..., "hint": ...}``.
    Answers 200 when healthy and 503 when not, so uptime tools can alert.
    """
    result = await db_health.check_db(async_session_factory)
    code = 200 if result.get("ok") else 503
    return JSONResponse(result, status_code=code, headers={"Cache-Control": "no-store"})

from app.api.v1 import validator, ads, auth, calendar, calls, contacts, lists, campaigns, sequences, followups, inbox, overview, templates, analytics, settings as settings_api, webhooks, dashboard, send, autoreply, automations, ai, variables, campaign_followups, notifications, email as email_api, mailbox as mailbox_api, guide as guide_api, mcp as mcp_api, system as system_api
app.include_router(auth.router, prefix="/api/v1/auth")
app.include_router(dashboard.router, prefix="/api/v1/dashboard")
app.include_router(contacts.router, prefix="/api/v1/contacts")
app.include_router(validator.router, prefix="/api/v1/validator")
app.include_router(lists.router, prefix="/api/v1/lists")
app.include_router(campaigns.router, prefix="/api/v1/campaigns")
app.include_router(sequences.router, prefix="/api/v1/sequences")
app.include_router(followups.router, prefix="/api/v1/followups")
app.include_router(inbox.router, prefix="/api/v1/inbox")
# Unified campaign + inbox overview: joins the classic campaigns table and the
# SMS Ads Manager into one shape so a single screen can show everything.
app.include_router(overview.router, prefix="/api/v1/overview")
app.include_router(templates.router, prefix="/api/v1/templates")
app.include_router(analytics.router, prefix="/api/v1/analytics")
app.include_router(settings_api.router, prefix="/api/v1/settings")
app.include_router(system_api.router, prefix="/api/v1/system")
app.include_router(webhooks.router, prefix="/api/v1/webhooks")
app.include_router(send.router, prefix="/api/v1/send")
app.include_router(autoreply.router, prefix="/api/v1/autoreply")
app.include_router(automations.router, prefix="/api/v1/automations")
app.include_router(ai.router, prefix="/api/v1/ai")
app.include_router(variables.router, prefix="/api/v1/variables")
app.include_router(campaign_followups.router, prefix="/api/v1/campaign-followups")
app.include_router(calendar.router, prefix="/api/v1/calendar")
# SMS Ads Manager (additive; the legacy campaign routes above are untouched).
app.include_router(ads.router, prefix="/api/v1/ads")
# Phone calls via CallGate (same handset as SMS-Gate).
app.include_router(calls.router, prefix="/api/v1/calls")
app.include_router(notifications.router, prefix="/api/v1/notifications")
# Email channel (Brevo): senders, one-off sends, the email inbox and its stats.
# The SMS routes above are untouched -- this is a parallel, additive surface.
app.include_router(email_api.router, prefix="/api/v1/email")
# Connected mailboxes (Gmail / IMAP): reads the operator's own inbox so prospect
# replies land in the app, and sends one-to-one replies back out through that same
# mailbox so they stay DMARC-aligned instead of tainting the thread.
app.include_router(mailbox_api.router, prefix="/api/v1/mailbox")
# The in-app setup tutorial: a checklist whose status is read live from the
# database and environment, so a missing PUBLIC_BASE_URL or an unregistered
# webhook is something the operator is told about instead of something they have
# to diagnose from a symptom.
app.include_router(guide_api.router, prefix="/api/v1/guide")
app.include_router(mcp_api.router, prefix="/api/v1/mcp")
# The canonical address an AI assistant is pointed at: https://your-app/mcp
app.include_router(mcp_api.protocol_router, prefix="/mcp")
# Per-client connector endpoints (/connectors/chatgpt/mcp, ...), the OAuth 2.1
# authorization server they need (/connectors/<key>/oauth/*) and the discovery
# documents both ChatGPT and Claude read before they will show a sign-in window
# (/.well-known/oauth-protected-resource, /.well-known/oauth-authorization-server).
# Mounted at the root because those paths are fixed by the specs, and mounted
# before the SPA catch-all so the catch-all cannot swallow them.
app.include_router(mcp_api.public_router)

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
