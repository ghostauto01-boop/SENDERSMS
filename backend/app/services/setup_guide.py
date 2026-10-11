"""The in-app setup tutorial: what is configured, what is not, and what to do.

Why this exists
---------------
The app has seven things that have to be right before it works end to end —
secrets, a public URL, the SMS gateway and its webhook, a Brevo sender and its
webhook, a mailbox that reads replies, and the AI connectors — and every one of
them fails *silently* in a way that looks like a bug in the app rather than a
missing setting. "My replies never arrive" is a missing mailbox connection.
"The connector is not working" is a missing ``PUBLIC_BASE_URL``. "Inbound SMS
stopped" is an unregistered webhook.

So instead of a static documentation page, this builds a checklist whose status
is read from the live database and environment on every request: each step says
whether it is done, what the app can currently see, the exact values to paste
somewhere else, and which screen to open to fix it.

Nothing here writes to the database. It only reads.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db_health
from app.config import settings
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.contact_list import ContactList
from app.models.conversation import Conversation
from app.models.email import EmailAccount
from app.models.mcp import McpToken
from app.models.notification import NotificationProvider
from app.utils.urls import public_base_url, webhook_url

logger = logging.getLogger(__name__)

DONE = "done"          # configured and verified as far as the app can tell
TODO = "todo"          # required, and not done
ATTENTION = "attention"  # configured, but something about it will bite you
OPTIONAL = "optional"  # not required; worth doing

GROUPS = (
    ("foundation", "1. Foundation", "Secrets, the public address and the database. Everything else is built on these."),
    ("sms", "2. SMS (Nigeria)", "The handset gateway that sends and receives SMS."),
    ("email", "3. Email sending", "Brevo: the sender, its authentication and its webhook."),
    ("replies", "4. Email replies", "Getting a prospect's reply back into the app — and out of Spam."),
    ("ai", "5. AI connectors", "Letting ChatGPT, Claude or an Arena agent operate the app."),
    ("optional", "6. Optional", "Notifications, calls, sending rules and the first send."),
)


def _step(
    step_id: str,
    group: str,
    title: str,
    *,
    status: str,
    why: str,
    detail: str = "",
    steps: list[str] | None = None,
    route: str | None = None,
    route_label: str = "Open",
    copy: list[dict] | None = None,
    docs: list[dict] | None = None,
    verify: str = "",
) -> dict:
    return {
        "id": step_id,
        "group": group,
        "title": title,
        "status": status,
        "why": why,
        "detail": detail,
        "steps": steps or [],
        "route": route,
        "route_label": route_label if route else None,
        "copy": copy or [],
        "docs": docs or [],
        "verify": verify,
    }


# ---------------------------------------------------------------------------
# 1. Foundation
# ---------------------------------------------------------------------------


def _foundation_steps(db_ok: Optional[bool], db_kind: Optional[str]) -> list[dict]:
    steps: list[dict] = []

    insecure = settings.insecure_defaults()
    weak_password = settings.uses_default_admin_password()

    if weak_password:
        steps.append(_step(
            "admin-password", "foundation", "Change the admin password",
            status=ATTENTION if settings.is_production else TODO,
            why=(
                "Sign-in uses the app password, which is still the one that ships with the "
                "project (12345678). Anyone who knows it can read your contacts and send as you."
            ),
            detail="ADMIN_PASSWORD is still the shipped default.",
            steps=[
                "Create a strong password (12+ characters, not reused anywhere).",
                "Set ADMIN_PASSWORD to it in the environment (Render → Environment, docker "
                "compose, or .env) and restart.",
                "Sign in with the new password — the app refreshes the stored hash on first use.",
            ],
            verify="Sign in with the new password; the old one is refused.",
        ))

    if insecure:
        steps.append(_step(
            "secrets", "foundation", "Replace the default secrets",
            status=TODO if settings.APP_ENV == "production" else ATTENTION,
            why=(
                "SECRET_KEY signs the session cookie and every unsubscribe link, and "
                "CREDENTIAL_ENCRYPTION_KEY encrypts your Brevo keys, gateway passwords and "
                "mailbox app passwords at rest. On the shipped defaults anyone who reads the "
                "repository can forge a session or decrypt your credentials."
            ),
            detail=f"Still on the development default: {', '.join(insecure)}.",
            steps=[
                "Generate both: python -c \"import secrets; print(secrets.token_urlsafe(48))\" for "
                "SECRET_KEY, and python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\" for CREDENTIAL_ENCRYPTION_KEY.",
                "Set ADMIN_PASSWORD to something only you know — it is the password for the "
                "operator login, and the app refreshes the stored hash from it on sign-in.",
                "Put all three in the environment (never in the repository) and restart.",
                "Changing CREDENTIAL_ENCRYPTION_KEY later makes every stored credential "
                "unreadable, so set it once, before you save any API key.",
            ],
            verify="The server log stops printing \"INSECURE DEFAULTS in use for: …\".",
        ))
    else:
        steps.append(_step(
            "secrets", "foundation", "Replace the default secrets",
            status=DONE,
            why="SECRET_KEY, CREDENTIAL_ENCRYPTION_KEY and ADMIN_PASSWORD are all off their defaults.",
            detail="Sessions and stored credentials are signed and encrypted with your own keys.",
        ))

    base = public_base_url()
    if base:
        steps.append(_step(
            "public-base-url", "foundation", "PUBLIC_BASE_URL",
            status=DONE,
            why=(
                "Every absolute link the app hands to an outside service is built from it: the SMS "
                "gateway's inbound webhook, the Brevo webhook, the one-click unsubscribe link in "
                "bulk mail, and every URL in the AI connector's OAuth metadata."
            ),
            detail=f"Set to {base}.",
            copy=[{"label": "Public base URL", "value": base}],
            verify=(
                "Open that address in a browser from a phone on mobile data — not from the server "
                "itself. If it does not load, outside services cannot reach you either."
            ),
        ))
    else:
        steps.append(_step(
            "public-base-url", "foundation", "Set PUBLIC_BASE_URL",
            status=TODO,
            why=(
                "Without it the app cannot register an inbound SMS webhook, bulk mail falls back to "
                "a mailto: unsubscribe, and no AI client can connect — ChatGPT and Claude both "
                "reject OAuth metadata whose `resource` is relative or does not match the URL they "
                "were given."
            ),
            detail="Not set. RENDER_EXTERNAL_URL is used as a fallback on Render, and that is empty too.",
            steps=[
                "Set PUBLIC_BASE_URL to the public HTTPS address of this deployment, with no "
                "trailing slash: https://your-app.onrender.com",
                "Restart the server.",
                "Local development: http://127.0.0.1:8000 works for everything except the AI "
                "connectors and inbound webhooks, which need an address the outside world can "
                "reach (a tunnel such as ngrok or cloudflared).",
            ],
            docs=[{"label": "Deployment guide", "url": "/DEPLOY.md"}],
        ))

    if db_ok is False:
        steps.append(_step(
            "database", "foundation", "Database",
            status=TODO,
            why="Nothing works without it — contacts, campaigns, the inbox and every credential live here.",
            detail=f"The last check failed ({db_kind or 'unknown reason'}).",
            steps=[
                "Check DATABASE_URL. PostgreSQL uses postgresql+asyncpg://user:password@host:5432/db.",
                "On a serverless database (Neon, Supabase) a suspended instance wakes on the first "
                "query — the app reports `quota_exceeded` when the plan is out of compute hours.",
                "GET /api/v1/health/db returns the classified reason without credentials in it.",
            ],
        ))
    else:
        engine = "PostgreSQL" if str(settings.DATABASE_URL).startswith("postgres") else (
            "SQLite" if "sqlite" in str(settings.DATABASE_URL) else "the configured database"
        )
        note = ""
        if engine == "SQLite":
            note = (
                " SQLite is fine for one operator, but it is a single file with no concurrent "
                "writers: a Celery worker and the web process will lock each other under load. "
                "Move to PostgreSQL before sending real volume."
            )
        steps.append(_step(
            "database", "foundation", "Database",
            status=DONE if engine != "SQLite" else ATTENTION,
            why="Contacts, campaigns, conversations and every stored credential live here.",
            detail=f"Connected to {engine}.{note}",
            verify="GET /api/v1/health/db answers 200.",
        ))

    steps.append(_step(
        "worker", "foundation", "Background worker (Celery) or the inline poller",
        status=ATTENTION if not settings.ENABLE_INLINE_POLLER else DONE,
        why=(
            "Campaign batches, scheduled sends, follow-ups, delivery-status polling and mailbox "
            "reply imports all run in the background. Without a worker, or with the inline poller "
            "on, nothing happens on its own."
        ),
        detail=(
            "ENABLE_INLINE_POLLER=true: the web process itself sweeps due work on a timer. This is "
            "the right choice for a single-container deployment, and it is what makes replies "
            "arrive when you have no Celery worker."
            if settings.ENABLE_INLINE_POLLER else
            "ENABLE_INLINE_POLLER=false: the app expects a Celery worker and a Celery beat "
            "scheduler to be running. Start both, or switch the inline poller on — otherwise "
            "campaigns stay `running` with pending contacts and mailboxes are only synced when you "
            "press the button."
        ),
        steps=[
            "Worker:  celery -A app.tasks.celery_app worker --loglevel=info",
            "Beat:    celery -A app.tasks.celery_app beat --loglevel=info",
            "Both need REDIS_URL. Without a broker, set ENABLE_INLINE_POLLER=true instead.",
        ],
        route="/settings",
        route_label="Open Settings",
    ))
    return steps


# ---------------------------------------------------------------------------
# 2. SMS
# ---------------------------------------------------------------------------


async def _sms_steps(db: AsyncSession) -> list[dict]:
    from app.services.system_settings import (
        SIM_NUMBER, WEBHOOK_REGISTERED, get_setting, get_sim_number,
    )

    steps: list[dict] = []
    configured = bool(settings.smsgate_configured)
    url = webhook_url()
    registered = await get_setting(db, WEBHOOK_REGISTERED)
    sim = await get_sim_number(db)

    if configured:
        steps.append(_step(
            "sms-gateway", "sms", "SMS gateway (SMS-Gate)",
            status=DONE,
            why="The gateway is the handset that actually sends and receives your SMS.",
            detail=(
                f"Credentials are set for {settings.SMSGATE_BASE_URL or 'the gateway'}, "
                f"SIM slot {sim}."
            ),
            route="/settings",
            route_label="Open Settings → SMS Gateway",
            verify="Settings → SMS Gateway → Test connection.",
        ))
    else:
        steps.append(_step(
            "sms-gateway", "sms", "Connect the SMS gateway",
            status=TODO,
            why=(
                "SMS is sent through your own handset via SMS-Gate, so nothing leaves the app "
                "until the gateway credentials are in. You can still use the email channel "
                "without it."
            ),
            detail="SMSGATE_BASE_URL / SMSGATE_USERNAME / SMSGATE_PASSWORD are not all set.",
            steps=[
                "Install SMS-Gate on the Android phone that holds the SIM, and sign in to it.",
                "Copy its address, username and password into Settings → SMS Gateway (they are "
                "stored in the database, so no restart is needed), or set SMSGATE_BASE_URL, "
                "SMSGATE_USERNAME and SMSGATE_PASSWORD in the environment.",
                "Set SMSGATE_WEBHOOK_SECRET to the signing key shown on the phone under "
                "Settings → Webhooks → Signing Key. Inbound SMS is rejected with 401 unless the "
                "HMAC signature matches.",
                "Press Test connection.",
            ],
            route="/settings",
            route_label="Open Settings → SMS Gateway",
            docs=[{"label": "SMS-Gate setup notes", "url": "/SMS_GATE.md"}],
        ))

    if not url:
        steps.append(_step(
            "sms-webhook", "sms", "Inbound SMS webhook",
            status=TODO,
            why="Replies to your SMS only arrive if the gateway can call this app back.",
            detail="Blocked by the missing PUBLIC_BASE_URL above — the URL cannot be built.",
        ))
    elif registered and registered == url:
        steps.append(_step(
            "sms-webhook", "sms", "Inbound SMS webhook",
            status=DONE,
            why="The gateway posts every incoming SMS here; that is how replies reach the inbox.",
            detail="Registered with the gateway and matching the current public address.",
            copy=[{"label": "Webhook URL", "value": url}],
            route="/settings",
            route_label="Open Settings → SMS Gateway",
        ))
    else:
        steps.append(_step(
            "sms-webhook", "sms", "Register the inbound SMS webhook",
            status=ATTENTION if registered else TODO,
            why="The gateway posts every incoming SMS here; that is how replies reach the inbox.",
            detail=(
                f"The gateway still has {registered}, which is not this deployment's current "
                "address — inbound SMS is being posted somewhere that no longer exists. This is "
                "what happens after PUBLIC_BASE_URL changes."
                if registered else
                "Not registered yet. The app tries once on boot; if the gateway was unreachable at "
                "that moment, register it from the button below."
            ),
            steps=[
                "Press Register webhook in Settings → SMS Gateway (it registers this URL with the "
                "gateway and remembers it).",
                "Or paste the URL into the gateway app yourself: Settings → Webhooks.",
            ],
            copy=[{"label": "Webhook URL", "value": url}],
            route="/settings",
            route_label="Open Settings → SMS Gateway",
            verify="Text the SIM from another phone; the message appears in Inbox within a minute.",
        ))

    steps.append(_step(
        "sim-slot", "sms", "SIM slot",
        status=DONE,
        why="A dual-SIM handset needs to know which slot to send from.",
        detail=f"Sending from SIM {sim}. Change it in Settings → SMS Gateway.",
        route="/settings",
        route_label="Open Settings → SMS Gateway",
    ))
    return steps


# ---------------------------------------------------------------------------
# 3. Email sending (Brevo)
# ---------------------------------------------------------------------------


async def _email_steps(db: AsyncSession) -> list[dict]:
    from app.services import email_service

    steps: list[dict] = []
    accounts = await email_service.list_accounts(db, include_inactive=True)
    active = [a for a in accounts if a.is_active]
    default = next((a for a in active if a.is_default), active[0] if active else None)

    if not accounts:
        steps.append(_step(
            "brevo-sender", "email", "Add a Brevo sender",
            status=TODO,
            why=(
                "Email leaves through Brevo. Nothing sends — campaigns, one-off mail or replies — "
                "until at least one API key is saved."
            ),
            detail="No sender saved yet.",
            steps=[
                "Create a free Brevo account, then an API key: brevo.com → SMTP & API → API keys "
                "(v3 scope).",
                "Email Manager → Senders → Add sender, and paste the key with the From name and "
                "From address you want recipients to see.",
                "Press Test — it verifies the key against Brevo and tells you whether the From "
                "address is allowed to send.",
                "You can save several keys: if one gets rate-limited or burned, sending falls "
                "through to the next automatically.",
            ],
            route="/email-manager",
            route_label="Open Email Manager → Senders",
            docs=[{"label": "Brevo API keys", "url": "https://app.brevo.com/settings/keys"}],
        ))
    else:
        # `connection_status == "ok"` is itself proof the key was checked against
        # Brevo, so a sender that has never been *re*-tested is not a problem.
        never_tested = [
            a for a in active
            if not a.last_tested_at and (a.connection_status or "") != "ok"
        ]
        failing = [a for a in active if a.connection_status and a.connection_status != "ok"]
        status = DONE
        if failing:
            status = ATTENTION
        elif never_tested:
            status = ATTENTION
        steps.append(_step(
            "brevo-sender", "email", "Brevo sender",
            status=status,
            why="Email leaves through Brevo, and the sender decides what recipients see in the From line.",
            detail=(
                f"{len(active)} active sender(s); default is "
                f"{default.from_email if default else 'none'}."
                + (f" Not tested yet: {', '.join(a.name for a in never_tested)}." if never_tested else "")
                + (f" Failing: {', '.join(f'{a.name} ({a.connection_status})' for a in failing)}."
                   if failing else "")
            ),
            steps=[
                "Press Test on each sender — it checks the key and the From address against Brevo.",
                "Mark the one you want campaigns to use as default; a campaign can override it.",
            ] if status != DONE else [],
            route="/email-manager",
            route_label="Open Email Manager → Senders",
        ))

    if default is not None:
        token = await email_service.ensure_webhook_token(db, default)
        hook = email_service.email_webhook_url(default)
        registered = bool(token)
        steps.append(_step(
            "brevo-webhook", "email", "Paste the Brevo webhook",
            status=ATTENTION if hook else TODO,
            why=(
                "This is how opens, clicks, bounces, complaints and Brevo-parsed inbound mail get "
                "back into the app. Without it a sent email stays `sent` forever and bounces are "
                "never suppressed."
            ),
            detail=(
                "The URL below is unique to this sender and carries a token, so an anonymous caller "
                "cannot inject events. Paste it in Brevo under Transactional → Settings → Webhook "
                "(and the inbound-parsing webhook if you use Brevo inbound)."
                if hook else
                "Blocked by the missing PUBLIC_BASE_URL above."
            ),
            # The token in this URL is a credential and the guide is a read endpoint (an
            # assistant reads it too): show it masked and tell the dashboard where to fetch
            # the real value when the copy button is pressed.
            copy=[{
                "label": "Brevo webhook URL",
                "value": hook or "",
                "reveal": {
                    "method": "POST",
                    "path": f"/api/v1/email/accounts/{default.id}/webhook/reveal",
                    "field": "webhook_url",
                },
            }] if hook else [],
            steps=[
                "Copy the URL below.",
                "In Brevo: Transactional → Settings → Webhook → add it for the events you want "
                "(at minimum delivered, hard_bounce, soft_bounce, spam, opened, click).",
                "Send a test from Email Manager → Quick Send to your own address and watch the "
                "events appear in Email Manager → Activity.",
            ] if hook else [],
            route="/email-manager",
            route_label="Open Email Manager → Senders",
            verify="Email Manager → Activity shows the events for a message you just sent.",
        ))
        _ = registered

    if accounts and not active:
        # Only worth its own step when senders exist but are all switched off —
        # with none saved, the "Add a Brevo sender" step above already says it.
        steps.append(_step(
            "brevo-sender-active", "email", "Enable a sender",
            status=TODO,
            why="Every saved sender is disabled, so nothing can send.",
            detail=f"{len(accounts)} sender(s) saved, none active.",
            route="/email-manager",
            route_label="Open Email Manager → Senders",
        ))
    return steps


# ---------------------------------------------------------------------------
# 4. Email replies
# ---------------------------------------------------------------------------


async def _reply_steps(db: AsyncSession) -> list[dict]:
    from app.services import mailbox_service

    steps: list[dict] = []
    report = await mailbox_service.reply_routing_report(db)
    mailboxes = [m for m in report.get("mailboxes") or [] if m.get("is_active")]
    high = [f for f in report.get("findings") or [] if f.get("severity") == "high"]

    if not mailboxes:
        steps.append(_step(
            "mailbox", "replies", "Connect the mailbox your prospects reply to",
            status=TODO,
            why=(
                "Brevo inbound parsing only sees mail addressed to a domain whose MX points at "
                "Brevo. A reply to a Gmail address never reaches it, so without a connected "
                "mailbox the reply exists only in Gmail and never appears in the app."
            ),
            detail=(
                "No mailbox connected. The app can read INBOX and Spam over IMAP with a Google app "
                "password (no Google Cloud project needed), or over the Gmail API with OAuth."
            ),
            steps=[
                "Email Manager → Replies → App password.",
                "Google blocks IMAP with your normal password: turn on 2-Step Verification, then "
                "create a 16-character app password at myaccount.google.com/apppasswords.",
                "Paste it with the mailbox address. The app verifies the login, imports the last "
                "seven days of replies and starts checking every minute.",
                "Keep \"Move recognised replies out of Spam\" on — that is the fix for a reply that "
                "Gmail filed away.",
                "Optional, for filters and API sending: set GOOGLE_CLIENT_ID and "
                "GOOGLE_CLIENT_SECRET, register "
                f"{(public_base_url() or 'https://your-app')}/api/v1/mailbox/google/callback as an "
                "authorized redirect URI, then use Connect with Google.",
            ],
            route="/email-manager?section=Replies",
            route_label="Open Email Manager → Replies",
            verify="Reply to one of your own campaigns from another address; it appears in Email Inbox within a minute.",
        ))
    else:
        first = mailboxes[0]
        failing = [m for m in mailboxes if m.get("last_sync_status") == "error"]
        steps.append(_step(
            "mailbox", "replies", "Connected mailbox",
            status=ATTENTION if failing else DONE,
            why="This is what reads your prospect's replies into the app, and moves them out of Spam.",
            detail=(
                f"{len(mailboxes)} connected. {report.get('replies_imported', 0)} replies imported, "
                f"{report.get('rescued_from_spam', 0)} rescued from Spam"
                + (f". Last check failed: {failing[0].get('last_error')}" if failing else ".")
            ),
            steps=[
                "Press Check on the mailbox — an app password is revoked whenever the Google "
                "account password changes, and an OAuth refresh token expires on a re-consent.",
            ] if failing else [],
            copy=[{"label": "Mailbox", "value": first.get("email_address", "")}],
            route="/email-manager?section=Replies",
            route_label="Open Email Manager → Replies",
        ))

    freemail = [f for f in high if "webmail" in (f.get("problem") or "")]
    if freemail:
        steps.append(_step(
            "reply-routing", "replies", "Why replies land in Spam",
            status=ATTENTION,
            why=(
                "This is the actual cause, and it is not a Gmail bug: the mail is sent by Brevo but "
                "the From/Reply-To is a free webmail address, so it cannot pass DMARC for that "
                "domain and Gmail treats the whole thread as spoofed."
            ),
            detail="; ".join(f"{f.get('where')}: {f.get('value')}" for f in freemail),
            steps=[
                "Connect that mailbox (above) so replies are read from it, and so one-to-one "
                "replies leave from it — an authenticated thread stops tainting the next reply.",
                "With the Gmail API connection, switch on \"Never send it to Spam\": it installs "
                "Gmail's own filter for the people who have replied.",
                "The permanent fix is a domain you control, authenticated in Brevo with SPF, DKIM "
                "and DMARC, used for both From and Reply-To.",
                "Keep bulk mail on Brevo and personal replies on the mailbox — that split is what "
                "the app does by default.",
            ],
            route="/email-manager?section=Replies",
            route_label="Open Email Manager → Replies",
        ))

    other = [
        f for f in report.get("findings") or []
        # "Reply inbox: no mailbox connected" IS the step above; showing it twice
        # makes the guide look like it is nagging about one problem as two.
        if (f not in high or "webmail" not in (f.get("problem") or ""))
        and not (not mailboxes and f.get("where") == "Reply inbox")
    ]
    for index, finding in enumerate(other[:3]):
        steps.append(_step(
            # A stable id: the frontend uses it as a React key and for the
            # "which step is open" state, so it must not depend on hash().
            f"reply-finding-{index}", "replies",
            f"Reply routing: {finding.get('where')}",
            status=ATTENTION if finding.get("severity") == "high" else OPTIONAL,
            why=finding.get("problem") or "",
            detail=str(finding.get("value") or ""),
            steps=[finding.get("fix")] if finding.get("fix") else [],
            route="/email-manager?section=Replies",
            route_label="Open Email Manager → Replies",
        ))
    return steps


# ---------------------------------------------------------------------------
# 5. AI connectors
# ---------------------------------------------------------------------------


async def _ai_steps(db: AsyncSession) -> list[dict]:
    from app.mcp import connectors as C
    from app.mcp import oauth as oauth_flow

    steps: list[dict] = []
    base = public_base_url()

    if not base:
        steps.append(_step(
            "mcp", "ai", "AI connectors are blocked by PUBLIC_BASE_URL",
            status=TODO,
            why=(
                "ChatGPT and Claude read the OAuth metadata at the URL you give them and refuse it "
                "unless `resource` is the exact absolute HTTPS address of the endpoint. With no "
                "public base URL every one of those values is relative."
            ),
            detail="Fix the PUBLIC_BASE_URL step above and this section fills in.",
        ))
        return steps

    connections = {}
    for profile in C.CONNECTORS:
        connections[profile.key] = await oauth_flow.get_connection(db, profile.key)

    tested = {k: c for k, c in connections.items() if getattr(c, "last_test_at", None)}
    failing = {k: c for k, c in tested.items() if getattr(c, "status", None) == "error"}
    passed = {k: c for k, c in tested.items() if getattr(c, "status", None) == "ok"}

    status = DONE if passed and not failing else (ATTENTION if tested else TODO)
    detail = "No connector has been tested yet." if not tested else (
        f"{len(passed)} connector(s) pass their handshake test"
        + (f"; {len(failing)} failed: {', '.join(failing)}." if failing else ".")
    )

    steps.append(_step(
        "mcp", "ai", "Connect an AI assistant",
        status=status,
        why=(
            "The app is an MCP server, so ChatGPT, Claude or an Arena agent can import contacts, "
            "build and send campaigns, answer the inbox and read analytics. Each client gets its "
            "own endpoint because each one's requirements are different."
        ),
        detail=detail,
        steps=[
            "Settings → AI (MCP) shows one card per client with its URL and the steps written for it.",
            "ChatGPT: OAuth only — there is no field for a token, and a server offering one is "
            "rejected before it is shown.",
            "Claude: paste the URL as a custom connector; in Claude Code use the `claude mcp add` "
            "command on the card.",
            "Arena Agent Mode has no connector screen — point the agent at "
            "tools/arena-connector/AGENTS.md in this repository, or use curl with a bearer token.",
            "Press Run connection test on each card: it replays the exact handshake that client "
            "performs and reports every step, with the fix for the first one that fails.",
        ],
        copy=[{"label": f"{p.label} MCP URL", "value": p.resource} for p in C.CONNECTORS],
        route="/settings",
        route_label="Open Settings → AI (MCP)",
        verify="A green \"handshake passed\" pill on the card, and calls appearing in \"What the AI did\".",
    ))

    tokens = (await db.execute(
        select(func.count()).select_from(McpToken).where(McpToken.is_active.is_(True))
    )).scalar() or 0
    steps.append(_step(
        "mcp-token", "ai", "Bearer token for header-based clients",
        status=DONE if tokens else OPTIONAL,
        why=(
            "Claude Code, Cursor, Cline, Windsurf and the Arena bridge authenticate with an "
            "Authorization header rather than OAuth. ChatGPT's hosted connectors ignore tokens "
            "entirely — they only do OAuth."
        ),
        detail=(
            f"{tokens} active token(s)." if tokens else
            "None yet. Create one in Settings → AI (MCP); it is shown once and stored as a hash."
        ),
        route="/settings",
        route_label="Open Settings → AI (MCP)",
    ))
    return steps


# ---------------------------------------------------------------------------
# 6. Optional
# ---------------------------------------------------------------------------


async def _optional_steps(db: AsyncSession) -> list[dict]:
    steps: list[dict] = []

    providers = (await db.execute(
        select(NotificationProvider).where(NotificationProvider.is_enabled.is_(True))
    )).scalars().all()
    steps.append(_step(
        "notifications", "optional", "Push / email notifications",
        status=DONE if providers else OPTIONAL,
        why=(
            "So a reply, a finished campaign, a gateway going offline or a new email reply reaches "
            "you without the app being open."
        ),
        detail=(
            f"Enabled: {', '.join(p.provider for p in providers)}." if providers else
            "None enabled. Pushover is a five-minute setup (a user key and an app token); OneSignal "
            "needs an app id and a REST API key."
        ),
        steps=[] if providers else [
            "Settings → Notifications → choose Pushover or OneSignal and paste the credentials.",
            "Pick which events notify you: new reply, campaign completed or failed, gateway "
            "offline, follow-up due, system error.",
            "Press Test — it sends a real notification through that provider.",
        ],
        route="/settings",
        route_label="Open Settings → Notifications",
    ))

    steps.append(_step(
        "calls", "optional", "Phone calls (CallGate)",
        status=DONE if settings.callgate_configured else OPTIONAL,
        why="Dial a contact from the app through the same handset that sends SMS.",
        detail=(
            "CallGate credentials are set." if settings.callgate_configured else
            "Not configured. Optional — only needed if you want click-to-call from a contact."
        ),
        route="/settings",
        route_label="Open Settings → Calls",
    ))

    steps.append(_step(
        "sending-rules", "optional", "Sending rules and compliance",
        status=OPTIONAL,
        why=(
            "Quiet hours, daily caps and the consent rules that keep you out of a carrier's spam "
            "filter and a regulator's inbox. They apply to every send, including an assistant's."
        ),
        detail="Set quiet hours, per-channel daily limits and the compliance toggles.",
        route="/settings",
        route_label="Open Settings → Sending Rules",
    ))

    counts = {
        "contacts": (await db.execute(select(func.count()).select_from(Contact))).scalar() or 0,
        "lists": (await db.execute(select(func.count()).select_from(ContactList))).scalar() or 0,
        "campaigns": (await db.execute(select(func.count()).select_from(Campaign))).scalar() or 0,
        "conversations": (await db.execute(
            select(func.count()).select_from(Conversation)
        )).scalar() or 0,
    }
    ready = counts["contacts"] > 0
    steps.append(_step(
        "first-send", "optional", "Send something real",
        status=DONE if ready else OPTIONAL,
        why="The order that works: contacts → list → template → campaign → validate → test → send.",
        detail=(
            f"{counts['contacts']} contacts, {counts['lists']} lists, {counts['campaigns']} "
            f"campaigns, {counts['conversations']} conversations."
        ),
        steps=[
            "Contacts → Import (CSV). A phone number in +234… form, or an email address.",
            "Lists → create one and add the contacts, or Audiences for a saved filter.",
            "Templates → save the message; {{first_name}} and your own CSV columns are substituted "
            "per contact, and anything with no value is removed rather than sent verbatim.",
            "Send → pick the channel, the sender and the audience, then Send a test to yourself.",
            "Campaigns → create, Validate, and start. Email Manager does the same for email with "
            "A/B tests, follow-ups and analytics.",
        ] if not ready else [],
        route="/send",
        route_label="Open Send",
    ))

    accounts = (await db.execute(
        select(func.count()).select_from(EmailAccount).where(EmailAccount.is_active.is_(True))
    )).scalar() or 0
    steps.append(_step(
        "unsubscribe", "optional", "Unsubscribe handling",
        status=DONE if (accounts or public_base_url()) else ATTENTION,
        why=(
            "Gmail and Yahoo require one-click unsubscribe on bulk mail, and a complaint rate above "
            "0.3% gets you filtered. Bulk mail carries a small Unsubscribe button plus the "
            "List-Unsubscribe headers; one-to-one mail carries neither, so a personal reply still "
            "reads as personal."
        ),
        detail=(
            "Bulk mail gets the button and the headers. A reply of STOP / unsubscribe / remove is "
            "also honoured and suppresses that address."
            if public_base_url() else
            "The button needs PUBLIC_BASE_URL; until then bulk mail falls back to a mailto: "
            "List-Unsubscribe header only."
        ),
        route="/email-manager",
        route_label="Open Email Manager → Suppression",
    ))
    return steps


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


async def build_guide(db: AsyncSession) -> dict:
    """The whole tutorial, with live status for every step."""
    status = db_health.status
    steps: list[dict] = []
    steps += _foundation_steps(getattr(status, "ok", None), getattr(status, "kind", None))

    for builder in (_sms_steps, _email_steps, _reply_steps, _ai_steps, _optional_steps):
        try:
            steps += await builder(db)
        except Exception as exc:  # noqa: BLE001 — one broken section must not blank the page
            logger.warning("SETUP GUIDE: %s failed: %s", builder.__name__, exc)

    await db.commit()

    groups: list[dict] = []
    for key, label, hint in GROUPS:
        members = [s for s in steps if s["group"] == key]
        required = [s for s in members if s["status"] in (TODO, ATTENTION)]
        groups.append({
            "id": key,
            "label": label,
            "hint": hint,
            "steps": members,
            "done": sum(1 for s in members if s["status"] == DONE),
            "total": len(members),
            "needs_attention": len(required),
        })

    # Progress counts a step as finished when it is DONE, or when it is OPTIONAL
    # and therefore never required. The blocking ones are TODO/ATTENTION.
    blocking = [s for s in steps if s["status"] in (TODO, ATTENTION)]
    finished = [s for s in steps if s["status"] == DONE]
    optional = [s for s in steps if s["status"] == OPTIONAL]
    total = len(steps)
    return {
        "groups": groups,
        "steps": steps,
        "progress": {
            "done": len(finished),
            "blocking": len(blocking),
            "optional": len(optional),
            "total": total,
            "percent": round(100 * (len(finished) + len(optional)) / total) if total else 100,
            "ready_to_send": not any(
                s["id"] in {"secrets", "public-base-url", "database", "sms-gateway", "brevo-sender"}
                and s["status"] == TODO
                for s in steps
            ),
        },
        "next": [s["id"] for s in blocking][:5],
        "environment": {
            "app_env": settings.APP_ENV,
            "public_base_url": public_base_url(),
            "inline_poller": bool(settings.ENABLE_INLINE_POLLER),
            "google_oauth_ready": bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET),
            "gmail_send_replies": bool(settings.GMAIL_SEND_REPLIES),
            "gmail_rescue_from_spam": bool(settings.GMAIL_RESCUE_FROM_SPAM),
            "mcp_oauth_enabled": bool(settings.MCP_OAUTH_ENABLED),
        },
    }


async def summary(db: AsyncSession) -> dict:
    """The cheap version, for the sidebar badge."""
    guide = await build_guide(db)
    progress = guide["progress"]
    first = next(
        (s for s in guide["steps"] if s["status"] in (TODO, ATTENTION)), None
    )
    return {
        **progress,
        "next_step": first["title"] if first else None,
        "next_route": first["route"] if first else None,
    }
