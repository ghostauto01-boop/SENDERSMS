"""Execution Guard, Remote State Reconciliation & Idempotency Ledger.

Protects SENDERSMS campaign and follow-up mutations when external AI Agent
runners (such as Arena Agent Mode sandboxes, stdio MCP bridges, or webhook
orchestrators) experience mid-turn workspace interruptions — e.g., S3/R2
``SignatureDoesNotMatch`` errors in the agent host's snapshot layer.

Core guarantees:
  1. **Zero Secret Leakage**: Structured diagnostic logs redact every bearer
     token, API key, cookie, signing secret, and password.
  2. **Strict Credential Separation**: Workspace/object-storage SigV4 headers
     (``Authorization: AWS4-HMAC-SHA256``, ``X-Amz-*``) are detected and
     rejected on the MCP Bearer channel with an actionable diagnostic instead of
     a mysterious 401.
  3. **Clock-Skew Telemetry**: Compares client ``X-Client-Timestamp`` / ``Date``
     headers against server UTC and warns when drift exceeds 60 seconds.
  4. **Reconcile-Before-Mutate**: ``reconcile_campaigns_and_followups`` inspects
     live ``Campaign`` rows (e.g. #25, #26), ``CampaignFollowUp`` rules/logs,
     and ``ScheduledMessage`` queues (e.g. 54 scheduled opener follow-ups) so an
     agent resuming after a dropped connection never replays or duplicates
     already-queued sends.
  5. **Idempotency + Expected-State Guards**: Mutations carrying an
     ``Idempotency-Key`` / ``X-Idempotency-Key`` return the cached outcome on
     replay instead of double-creating or double-starting campaigns.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import logging
import re
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign
from app.models.campaign_followup import CampaignFollowUp, CampaignFollowUpLog
from app.models.scheduled import ScheduledMessage
from app.models.system import SystemSetting

logger = logging.getLogger("app.execution_guard")

SENSITIVE_HEADER_SUBSTRINGS = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "api-key",
    "apikey",
    "signature",
    "x-amz-security-token",
)

SENSITIVE_FIELD_RE = re.compile(
    r"(password|secret|token|api_key|apikey|authorization|cookie|private_key|signing_key)",
    re.IGNORECASE,
)

BEARER_INLINE_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-+/=]{6,}", re.IGNORECASE)
SK_INLINE_RE = re.compile(r"\b(sk-[A-Za-z0-9\-_]{6,})\b")

IDEMPOTENCY_PREFIX = "idempotency.ledger."
IDEMPOTENCY_TTL_SECONDS = 86_400
CLOCK_SKEW_WARN_SECONDS = 60.0


def redact_text(value: str) -> str:
    if not value:
        return value
    masked = BEARER_INLINE_RE.sub(r"\1[REDACTED]", value)
    masked = SK_INLINE_RE.sub("[REDACTED_KEY]", masked)
    return masked


def redact_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for k, v in headers.items():
        key_str = str(k)
        val_str = str(v or "")
        low = key_str.lower()
        if any(s in low for s in SENSITIVE_HEADER_SUBSTRINGS):
            if low == "authorization" and val_str.lower().startswith("bearer "):
                safe[key_str] = "Bearer [REDACTED]"
            elif low == "authorization" and "aws4-hmac-sha256" in val_str.lower():
                safe[key_str] = "AWS4-HMAC-SHA256 [REDACTED_SIGV4]"
            else:
                safe[key_str] = "[REDACTED]"
        else:
            safe[key_str] = redact_text(val_str)[:256]
    return safe


def redact_payload(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if SENSITIVE_FIELD_RE.search(str(k)):
                out[str(k)] = "[REDACTED]"
            else:
                out[str(k)] = redact_payload(v)
        return out
    if isinstance(obj, list):
        return [redact_payload(x) for x in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def detect_sigv4_contamination(headers: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Detect if AWS SigV4 workspace/storage credentials leaked onto a SENDERSMS request."""
    low_map = {str(k).lower(): str(v or "") for k, v in headers.items()}
    auth = low_map.get("authorization", "")
    amz_headers = sorted(k for k in low_map if k.startswith("x-amz-"))
    if "aws4-hmac-sha256" in auth.lower() or amz_headers:
        return {
            "contaminated": True,
            "reason": "sigv4_credentials_on_bearer_endpoint",
            "amz_headers_present": amz_headers,
            "remediation": (
                "SENDERSMS MCP uses 'Authorization: Bearer <SENDERSMS_MCP_TOKEN>' exclusively. "
                "Keep AWS/S3/R2 workspace credentials isolated from SENDERSMS_MCP_TOKEN."
            ),
        }
    return None


def check_clock_skew(
    headers: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Compare client timestamp header (Unix seconds, ISO-8601, or HTTP Date) with server UTC."""
    server_now = now or datetime.now(timezone.utc)
    low_map = {str(k).lower(): str(v or "").strip() for k, v in headers.items()}
    raw_ts = low_map.get("x-client-timestamp") or low_map.get("date") or ""
    if not raw_ts:
        return {
            "server_utc": server_now.isoformat(),
            "client_timestamp_present": False,
            "skew_seconds": None,
            "healthy": True,
        }

    client_dt: Optional[datetime] = None
    try:
        if raw_ts.isdigit() or (raw_ts.replace(".", "", 1).isdigit()):
            client_dt = datetime.fromtimestamp(float(raw_ts), tz=timezone.utc)
        else:
            try:
                parsed = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                client_dt = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                parsed_http = parsedate_to_datetime(raw_ts)
                client_dt = (
                    parsed_http
                    if parsed_http.tzinfo
                    else parsed_http.replace(tzinfo=timezone.utc)
                )
    except Exception:
        return {
            "server_utc": server_now.isoformat(),
            "client_timestamp_present": True,
            "parse_error": True,
            "skew_seconds": None,
            "healthy": False,
        }

    skew = round((server_now - client_dt.astimezone(timezone.utc)).total_seconds(), 3)
    healthy = abs(skew) <= CLOCK_SKEW_WARN_SECONDS
    if not healthy:
        logger.warning(
            "Clock skew warning: %.1fs drift detected between client and server UTC",
            skew,
        )
    return {
        "server_utc": server_now.isoformat(),
        "client_utc": client_dt.astimezone(timezone.utc).isoformat(),
        "client_timestamp_present": True,
        "skew_seconds": skew,
        "healthy": healthy,
    }


def _ledger_setting_key(scope: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{scope}:{idempotency_key.strip()}".encode("utf-8")).hexdigest()[:40]
    return f"{IDEMPOTENCY_PREFIX}{digest}"


async def get_idempotent_replay(
    db: AsyncSession,
    scope: str,
    idempotency_key: Optional[str],
) -> Optional[dict[str, Any]]:
    """Return a previously recorded mutation response if ``idempotency_key`` was already executed."""
    if not idempotency_key or not idempotency_key.strip():
        return None
    skey = _ledger_setting_key(scope, idempotency_key)
    row = (
        await db.execute(select(SystemSetting).where(SystemSetting.key == skey))
    ).scalar_one_or_none()
    if row is None or not row.value:
        return None
    try:
        stored = json.loads(row.value)
        recorded_at = float(stored.get("ts") or 0)
        if datetime.now(timezone.utc).timestamp() - recorded_at > IDEMPOTENCY_TTL_SECONDS:
            return None
        logger.info(
            "Idempotent replay intercepted scope=%s key_hash=%s",
            scope,
            skey[-12:],
        )
        return stored.get("response")
    except Exception:
        return None


async def record_idempotent_result(
    db: AsyncSession,
    scope: str,
    idempotency_key: Optional[str],
    response_payload: dict[str, Any],
) -> None:
    if not idempotency_key or not idempotency_key.strip():
        return
    skey = _ledger_setting_key(scope, idempotency_key)
    envelope = json.dumps(
        {
            "ts": datetime.now(timezone.utc).timestamp(),
            "scope": scope,
            "response": redact_payload(response_payload),
        },
        default=str,
    )
    row = (
        await db.execute(select(SystemSetting).where(SystemSetting.key == skey))
    ).scalar_one_or_none()
    if row is None:
        db.add(
            SystemSetting(
                key=skey,
                value=envelope,
                category="idempotency",
                description=f"Idempotency guard ({scope})",
            )
        )
    else:
        row.value = envelope
    await db.flush()


async def reconcile_campaigns_and_followups(
    db: AsyncSession,
    *,
    campaign_ids: Optional[Iterable[int]] = None,
    include_scheduled_messages: bool = True,
) -> dict[str, Any]:
    """Inspect remote campaign, follow-up, and scheduled-message state BEFORE retrying mutations.

    Specifically designed so an agent recovering from a dropped tool/snapshot
    connection can verify Campaigns (e.g. #25, #26) and pending opener follow-ups
    (e.g. 54 scheduled messages/follow-ups) without risking duplicate sends.
    """
    wanted_ids = [int(x) for x in campaign_ids] if campaign_ids else []

    if wanted_ids:
        q = select(Campaign).where(Campaign.id.in_(wanted_ids)).order_by(Campaign.id.asc())
    else:
        q = (
            select(Campaign)
            .where(Campaign.status.in_(("running", "scheduled", "paused", "sending", "draft")))
            .order_by(Campaign.updated_at.desc(), Campaign.id.desc())
            .limit(50)
        )
    campaigns = (await db.execute(q)).scalars().all()
    found_ids = {c.id for c in campaigns}
    missing_ids = [cid for cid in wanted_ids if cid not in found_ids]

    campaign_snapshots: list[dict[str, Any]] = []
    active_or_pending_campaigns = 0

    for c in campaigns:
        rules = (
            await db.execute(
                select(CampaignFollowUp)
                .where(CampaignFollowUp.campaign_id == c.id)
                .order_by(CampaignFollowUp.step_order.asc())
            )
        ).scalars().all()

        log_counts_rows = (
            await db.execute(
                select(CampaignFollowUpLog.status, func.count())
                .where(CampaignFollowUpLog.campaign_id == c.id)
                .group_by(CampaignFollowUpLog.status)
            )
        ).all()
        followup_log_counts = {str(st): int(cnt) for st, cnt in log_counts_rows}

        already_dispatched = (
            c.status in ("running", "sending", "completed")
            or int(c.messages_sent or 0) > 0
            or (c.status == "scheduled" and c.scheduled_start_at is not None)
        )
        if c.status in ("running", "sending", "scheduled", "paused"):
            active_or_pending_campaigns += 1

        campaign_snapshots.append(
            {
                "id": c.id,
                "name": c.name,
                "channel": c.channel or "sms",
                "status": c.status,
                "total_contacts": int(c.total_contacts or 0),
                "messages_sent": int(c.messages_sent or 0),
                "messages_delivered": int(c.messages_delivered or 0),
                "messages_failed": int(c.messages_failed or 0),
                "replies": int(c.replies or 0),
                "scheduled_start_at": (
                    c.scheduled_start_at.isoformat() if c.scheduled_start_at else None
                ),
                "started_at": c.started_at.isoformat() if c.started_at else None,
                "completed_at": c.completed_at.isoformat() if c.completed_at else None,
                "followup_rules_total": len(rules),
                "followup_rules_active": sum(1 for r in rules if bool(r.is_active)),
                "followup_logs": followup_log_counts,
                "already_active_or_sent": already_dispatched,
                "safe_action": (
                    "DO_NOT_REPLAY_START — campaign already active/scheduled/sent; inspect status only"
                    if already_dispatched
                    else "SAFE_TO_VALIDATE_OR_SCHEDULE_WITH_IDEMPOTENCY_KEY"
                ),
            }
        )

    scheduled_summary: dict[str, Any] = {"pending_count": 0, "by_status": {}, "next_due_at": None}
    if include_scheduled_messages:
        status_rows = (
            await db.execute(
                select(ScheduledMessage.status, func.count()).group_by(ScheduledMessage.status)
            )
        ).all()
        by_status = {str(st): int(cnt) for st, cnt in status_rows}
        next_due = (
            await db.execute(
                select(func.min(ScheduledMessage.schedule_at)).where(
                    ScheduledMessage.status == "pending"
                )
            )
        ).scalar()
        scheduled_summary = {
            "pending_count": by_status.get("pending", 0),
            "by_status": by_status,
            "next_due_at": next_due.isoformat() if next_due else None,
        }

    return {
        "reconciled_at": datetime.now(timezone.utc).isoformat(),
        "requested_campaign_ids": wanted_ids,
        "missing_campaign_ids": missing_ids,
        "active_or_pending_campaigns": active_or_pending_campaigns,
        "campaigns": campaign_snapshots,
        "scheduled_messages": scheduled_summary,
        "replay_protection_advice": (
            "Always verify 'already_active_or_sent' and 'scheduled_messages.pending_count' before "
            "re-issuing create/start/schedule/follow-up mutations, and pass an X-Idempotency-Key."
        ),
    }


async def execution_health_report(
    db: AsyncSession,
    *,
    headers: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Comprehensive, secret-redacted health & alerting report for Agent/MCP execution."""
    from app.models.mcp import McpToken
    from app.services import ai_providers, gateway_service

    hdrs = headers or {}
    sigv4_check = detect_sigv4_contamination(hdrs)
    clock = check_clock_skew(hdrs)

    active_tokens = (
        await db.execute(
            select(func.count()).select_from(McpToken).where(McpToken.is_active == True)  # noqa: E712
        )
    ).scalar() or 0

    or_summary = await ai_providers.keys_summary(db)
    openrouter_configured = bool(
        (or_summary.get("providers") or {}).get("openrouter", {}).get("configured")
    )
    sms_ready = await gateway_service.has_any_usable_sms_sender(db)

    alerts: list[str] = []
    if sigv4_check:
        alerts.append(sigv4_check["remediation"])
    if not clock["healthy"]:
        alerts.append(
            f"Clock skew of {clock.get('skew_seconds')}s exceeds {CLOCK_SKEW_WARN_SECONDS}s tolerance."
        )

    status = "healthy" if not alerts else "degraded"
    return {
        "status": status,
        "service": "sendersms-mcp-execution",
        "auth_scheme": "Bearer (OAuth 2.1 / MCP Access Token — never AWS SigV4)",
        "credential_isolation_ok": sigv4_check is None,
        "clock": clock,
        "mcp_active_tokens": int(active_tokens),
        "openrouter_configured": openrouter_configured,
        "sms_sender_ready": sms_ready,
        "alerts": alerts,
        "redacted_request_headers": redact_headers(hdrs) if hdrs else {},
    }
