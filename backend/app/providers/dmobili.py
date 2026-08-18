"""Dmobili.com — second SMS gateway (Pace Bulk SMS platform).

Dmobili advertises an HTTP API that can send SMS, receive SMS into your
application and return delivery reports, but the specification is issued
privately by their support team (there is no public documentation). Their
platform is the "PACE Bulk SMS" product (REST, GET/POST, JSON/XML, Basic
login with username/password plus optional API token, 234-prefixed numbers,
DLR states PENDING / DELIVERED / EXPIRED / REJECTED / UNDELIVERABLE).

Because the exact endpoint shapes are not public, everything about the
request is configurable via settings (DMOBILI_*):

* ``DMOBILI_BASE_URL``  — gateway host (default https://dmobili.com)
* ``DMOBILI_USERNAME`` / ``DMOBILI_PASSWORD`` — Basic login credentials
* ``DMOBILI_API_TOKEN`` — standalone API token (sent as ``apikey``) when the
  account uses token auth instead of username/password
* ``DMOBILI_SENDER_ID`` — the registered Sender Name (their routes require it)
* ``DMOBILI_SEND_PATH`` — send endpoint path (default ``api/sms/index.php``)
* ``DMOBILI_BALANCE_PATH`` / ``DMOBILI_REPORT_PATH`` — optional endpoints
* ``DMOBILI_ROUTE`` — optional route name (Alpha, Premium, OTP, ...)

The send endpoint defaults to the classic layout used by this platform
family::

    GET {base}/{send_path}?user=..&password=..&sender=..&to=..&message=..

When Dmobili support hands over the real spec, point the paths at it —
no code change should be needed. Responses are parsed defensively: JSON
(``{"status": ..., "message_id": ...}``), ``code:payload`` text and plain
text bodies are all understood, so most variants work out of the box.
"""

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import settings
from app.providers.base import (
    DeliveryStatus,
    GatewayHealth,
    InboundMessage,
    SMSProvider,
    SMSResult,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Configuration accessors (read per-call so tests/env can change them)
# --------------------------------------------------------------------------

def _base() -> str:
    return (settings.DMOBILI_BASE_URL or "https://dmobili.com").rstrip("/")


def _send_url() -> str:
    return f"{_base()}/{(settings.DMOBILI_SEND_PATH or 'api/sms/index.php').strip('/')}"


def _balance_url() -> Optional[str]:
    p = (settings.DMOBILI_BALANCE_PATH or "").strip()
    return f"{_base()}/{p.strip('/')}" if p else None


def _report_url() -> Optional[str]:
    p = (settings.DMOBILI_REPORT_PATH or "").strip()
    return f"{_base()}/{p.strip('/')}" if p else None


def _auth_params() -> dict:
    """Authentication query parameters, honouring token-based accounts."""
    token = (settings.DMOBILI_API_TOKEN or "").strip()
    if token:
        return {"apikey": token}
    return {
        "user": (settings.DMOBILI_USERNAME or "").strip(),
        "password": (settings.DMOBILI_PASSWORD or "").strip(),
    }


def _creds_present() -> bool:
    a = _auth_params()
    return all(v for v in a.values())


def format_number(phone: str) -> str:
    """Format a destination number the way the platform expects it.

    The Pace platform requires the ``234`` national prefix and rejects the
    ``+`` of E.164. The app stores outbound numbers as ``+234...``, so strip
    the plus; also fold local ``080...`` / ``80...`` forms into ``234...``.
    """
    p = (phone or "").strip()
    if p.startswith("+"):
        p = p[1:]
    if p.startswith("00"):
        p = p[2:]
    if re.fullmatch(r"0\d{10}", p):
        p = "234" + p[1:]
    return p


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------

# DLR vocabulary published on the Pace platform's API page.
_DLR_MAP = {
    "delivered": "delivered",
    "deliverd": "delivered",
    "failed": "failed",
    "rejected": "failed",
    "expired": "failed",
    "undeliverable": "failed",
    "undelivered": "failed",
    "handset_errors": "failed",
    "user_errors": "failed",
    "operator_errors": "failed",
    "pending": "sent",
    "enroute": "sent",
    "buffered": "sent",
    "sent": "sent",
    "queued": "queued",
}

# Text fragments that mark a plain-text response as an ERROR even when the
# HTTP status is 200 (these platforms report failures in the body).
_ERROR_HINTS = (
    "error", "invalid", "insufficient", "wrong password", "wrong username",
    "authentication fail", "auth fail", "not registered", "denied",
    "missing parameter", "no credit", "expired", "suspended", "blocked",
    "sender id not", "sender not", "dnd", "rejected",
)

# Numeric response codes common to this platform family. Unknown codes are
# treated by context (presence of error hints) rather than rejected.
_CODE_MAP = {
    "1701": "sent",       # message queued/sent
    "1702": "sent",
    "1703": "sent",
    "1704": "sent",
    "1705": "sent",
    "1706": "sent",
    "1707": "sent",
    "1801": "failed",     # missing/invalid parameter family
    "1802": "failed",
    "1803": "failed",
    "1804": "failed",     # insufficient credit
    "1805": "failed",
    "1806": "failed",
    "1807": "failed",
    "1808": "failed",
    "1809": "failed",
    "1810": "failed",
}


def _looks_like_id(s: str) -> bool:
    s = (s or "").strip()
    return bool(s) and len(s) <= 100 and not any(c in s for c in "<>\n")


def parse_send_response(status_code: int, text: str) -> dict:
    """Turn a raw gateway response into the standard send-result dict.

    Returns ``{"success", "provider_message_id", "status", "error", "raw"}``
    shaped exactly like ``app.providers.smsgate.send_sms_direct`` so callers
    can treat both gateways identically.
    """
    text = (text or "").strip()
    raw: dict = {"http_status": status_code, "body": text[:2000]}

    # HTTP-level failures are unambiguous.
    if status_code >= 500:
        return {"success": False, "error": f"Gateway error (HTTP {status_code})", "raw": raw}
    if status_code in (401, 403):
        return {"success": False, "error": "Authentication rejected — check Dmobili credentials", "raw": raw}

    # Try JSON first.
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            raw["json"] = d
            err = d.get("error") or d.get("Error")
            status = str(d.get("status") or d.get("Status") or d.get("code") or d.get("Code") or "").strip()
            mid = str(
                d.get("message_id") or d.get("messageId") or d.get("msgid")
                or d.get("id") or d.get("batch_id") or d.get("data") or ""
            ).strip()
            sl = status.lower()
            if err:
                return {"success": False, "error": str(err)[:500], "raw": raw}
            if sl in ("fail", "failed", "error", "0", "false", "rejected"):
                detail = d.get("message") or d.get("description") or status
                return {"success": False, "error": str(detail)[:500] or "Gateway rejected message", "raw": raw}
            if sl in ("ok", "success", "sent", "1", "true", "accepted", "queued", "1701") or _looks_like_id(mid):
                return {
                    "success": True,
                    "provider_message_id": mid or str(uuid.uuid4()),
                    "status": "sent",
                    "raw": raw,
                }
            # Unknown JSON shape: fall through to text heuristics on the body.
    except (ValueError, TypeError):
        pass

    low = text.lower()

    # "code:detail" or "code,detail" text responses.
    m = re.match(r"^\s*(\d{3,5})\s*[:,]\s*(.*)$", text, re.S)
    if m:
        code, detail = m.group(1), m.group(2).strip()
        st = _CODE_MAP.get(code)
        if st == "failed" or (st is None and any(h in detail.lower() for h in _ERROR_HINTS)):
            return {"success": False, "error": f"Code {code}: {detail[:400]}", "raw": raw}
        if st == "sent" or _looks_like_id(detail):
            return {
                "success": True,
                "provider_message_id": detail or str(uuid.uuid4()),
                "status": "sent",
                "raw": raw,
            }

    # Plain text with explicit error vocabulary.
    if any(h in low for h in _ERROR_HINTS) and not re.fullmatch(r"[\w-]+", text):
        return {"success": False, "error": text[:500], "raw": raw}

    # Short plain-text responses are treated as the message reference.
    if status_code < 400 and text and _looks_like_id(text):
        return {"success": True, "provider_message_id": text, "status": "sent", "raw": raw}

    if status_code < 400 and text:
        # Non-empty 2xx/3xx with no error hint: assume accepted.
        return {"success": True, "provider_message_id": str(uuid.uuid4()), "status": "sent", "raw": raw}

    return {"success": False, "error": f"Empty or unusable gateway response (HTTP {status_code})", "raw": raw}


def map_dlr_status(raw_status: str) -> str:
    """Map a provider DLR string onto the app's sent/delivered/failed set."""
    s = (raw_status or "").strip().lower()
    if not s:
        return "unknown"
    if s in _DLR_MAP:
        return _DLR_MAP[s]
    if "deliver" in s:
        return "delivered"
    if any(x in s for x in ("fail", "reject", "expire", "undeliv", "error")):
        return "failed"
    if any(x in s for x in ("pending", "enroute", "buffer", "process", "sent")):
        return "sent"
    return "unknown"


# --------------------------------------------------------------------------
# Direct (module-level) API — mirrors the smsgate module interface so the
# dispatcher can swap the two gateways without changing call sites.
# --------------------------------------------------------------------------

async def send_sms_direct(phone: str, body: str, sender_id: Optional[str] = None) -> dict:
    """Send one SMS through Dmobili. Returns the standard result dict."""
    if not _creds_present():
        return {"success": False, "error": "No Dmobili credentials configured"}

    sender = (sender_id or settings.DMOBILI_SENDER_ID or "").strip()
    params = dict(_auth_params())
    params.update({
        "sender": sender,
        "to": format_number(phone),
        "message": body,
    })
    if (settings.DMOBILI_ROUTE or "").strip():
        params["route"] = settings.DMOBILI_ROUTE.strip()
    # Drop empty optionals rather than sending sender=&route=.
    params = {k: v for k, v in params.items() if v not in (None, "")}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.DMOBILI_TIMEOUT)) as c:
            r = await c.get(_send_url(), params=params)
            logger.info("DMOBILI SMS: HTTP %s %s", r.status_code, (r.text or "")[:200])
            result = parse_send_response(r.status_code, r.text)
            return result
    except httpx.TimeoutException:
        return {"success": False, "error": "Dmobili gateway timed out"}
    except Exception as e:
        logger.warning("dmobili send error: %s", e)
        return {"success": False, "error": str(e)[:500]}


async def get_balance() -> dict:
    """Query the credit balance (only works when DMOBILI_BALANCE_PATH is set)."""
    url = _balance_url()
    if not url:
        return {"success": False, "error": "DMOBILI_BALANCE_PATH not configured"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as c:
            r = await c.get(url, params=_auth_params())
            text = (r.text or "").strip()
            ok = r.status_code < 400 and not any(h in text.lower() for h in ("wrong", "invalid", "authentication", "denied"))
            return {"success": ok, "balance": text[:200] if ok else None,
                    "error": None if ok else (text[:300] or f"HTTP {r.status_code}"),
                    "http": r.status_code}
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}


async def test_connection_direct() -> dict:
    """Connectivity check used by Settings -> Test connection.

    With a balance endpoint configured this verifies credentials end to end.
    Without one it probes the send endpoint with credentials only (no
    message), which costs nothing and proves the server + endpoint exist;
    credential problems surface in the response body when the platform
    answers, but the result is flagged ``verified=False`` in that mode.
    """
    if not _creds_present():
        return {"success": False, "online": False, "message": "No Dmobili credentials configured"}

    bal = await get_balance()
    if _balance_url():
        return {
            "success": bal.get("success", False),
            "online": bal.get("success", False) or bal.get("http") is not None,
            "verified": bal.get("success", False),
            "balance": bal.get("balance"),
            "message": "Credentials OK" if bal.get("success") else (bal.get("error") or "Balance check failed"),
        }

    # Reachability probe: credentials but no message. Nothing is sent.
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as c:
            r = await c.get(_send_url(), params=_auth_params())
            reachable = r.status_code < 500
            return {
                "success": reachable,
                "online": reachable,
                "verified": False,
                "http": r.status_code,
                "message": (
                    "Gateway reachable (credentials not verifiable without a "
                    "balance/report endpoint — see docs/DMOBILI-GATEWAY.md)"
                    if reachable else f"Gateway returned HTTP {r.status_code}"
                ),
            }
    except Exception as e:
        return {"success": False, "online": False, "verified": False, "message": f"Cannot reach gateway: {str(e)[:200]}"}


async def poll_status_for_ids(ids: list) -> list:
    """Best-effort DLR polling (only works when DMOBILI_REPORT_PATH is set).

    Returns ``[{"provider_message_id": ..., "status": ...}]`` like the
    smsgate module does. Providers that push DLRs by callback leave this
    unconfigured and get an empty list (statuses arrive via the webhook).
    """
    url = _report_url()
    if not url or not ids:
        return []
    results = []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20)) as c:
            for mid in ids:
                if not mid:
                    continue
                try:
                    params = dict(_auth_params())
                    params["message_id"] = mid
                    r = await c.get(url, params=params)
                    if r.status_code != 200:
                        continue
                    text = (r.text or "").strip()
                    # Try JSON, then bare status text.
                    status = ""
                    try:
                        d = json.loads(text)
                        if isinstance(d, dict):
                            status = str(d.get("status") or d.get("state") or d.get("dlr") or "")
                        elif isinstance(d, str):
                            status = d
                    except ValueError:
                        status = text
                    st = map_dlr_status(status)
                    if st != "unknown":
                        results.append({"provider_message_id": mid, "status": st})
                except Exception:
                    continue
        return results
    except Exception:
        return []


# --------------------------------------------------------------------------
# Provider class (same interface as SMSGateProvider)
# --------------------------------------------------------------------------

class DmobiliProvider(SMSProvider):
    """Two-way SMS gateway adapter for Dmobili.com."""

    def __init__(self, sender_id: Optional[str] = None):
        self.sender_id = sender_id or settings.DMOBILI_SENDER_ID

    async def close(self):
        """No persistent client is kept; exists for interface parity with SMSGateProvider."""
        return None

    async def test_connection(self) -> bool:
        r = await test_connection_direct()
        return bool(r.get("success"))

    async def send_sms(self, to_number: str, message: str, sender_id: Optional[str] = None,
                       idempotency_key: Optional[str] = None, **kw) -> SMSResult:
        r = await send_sms_direct(to_number, message, sender_id or self.sender_id)
        return SMSResult(
            success=r["success"],
            provider_message_id=r.get("provider_message_id", ""),
            status=r.get("status", "unknown"),
            error=r.get("error"),
            raw_response=r.get("raw"),
        )

    async def get_message_status(self, provider_message_id: str) -> DeliveryStatus:
        if not provider_message_id:
            return DeliveryStatus(provider_message_id="", status="unknown")
        results = await poll_status_for_ids([provider_message_id])
        if results:
            return DeliveryStatus(
                provider_message_id=provider_message_id,
                status=results[0]["status"],
            )
        return DeliveryStatus(provider_message_id=provider_message_id, status="unknown")

    async def health_check(self) -> GatewayHealth:
        ok = await self.test_connection()
        return GatewayHealth(
            is_healthy=ok,
            status="healthy" if ok else "unhealthy",
            last_checked=datetime.now(timezone.utc),
        )

    # -- Inbound parsing -------------------------------------------------
    # Dmobili's callback format is part of the private spec; accept the
    # common field spellings so the webhook endpoint can be wired up before
    # the exact names are confirmed.

    def parse_inbound_message(self, raw: dict) -> InboundMessage:
        p = raw if isinstance(raw, dict) else {}
        return InboundMessage(
            provider_message_id=str(p.get("message_id") or p.get("messageId") or p.get("id") or uuid.uuid4()),
            from_number=str(p.get("from") or p.get("sender") or p.get("msisdn") or p.get("from_number") or "").strip(),
            to_number=str(p.get("to") or p.get("recipient") or p.get("dest") or "").strip(),
            body=str(p.get("message") or p.get("text") or p.get("msg") or p.get("body") or ""),
            received_at=self._parse_ts(p.get("date") or p.get("time") or p.get("received_at") or p.get("timestamp")) or datetime.now(timezone.utc),
            raw=raw,
        )

    def parse_delivery_status(self, raw: dict) -> DeliveryStatus:
        p = raw if isinstance(raw, dict) else {}
        status = map_dlr_status(str(p.get("status") or p.get("state") or p.get("dlr") or ""))
        return DeliveryStatus(
            provider_message_id=str(p.get("message_id") or p.get("messageId") or p.get("id") or ""),
            status=status,
            error=p.get("reason") if status == "failed" else None,
            raw=raw,
        )

    @staticmethod
    def _parse_ts(value):
        if not value:
            return None
        s = str(value).strip()
        # Unix timestamp (seconds) form.
        if re.fullmatch(r"\d{9,11}", s):
            try:
                return datetime.fromtimestamp(int(s), tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                return None
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)

    def supports_webhooks(self) -> bool:
        # Inbound SMS and DLRs arrive as HTTP callbacks pushed by the
        # platform (the Developers page promises "Receive SMS to your
        # application" and "Get Delivery Reports").
        return True

    def supports_polling(self) -> bool:
        # Only when a report endpoint is configured.
        return bool((settings.DMOBILI_REPORT_PATH or "").strip())

    @staticmethod
    def validate_callback_secret(provided: str) -> bool:
        """Compare the callback's shared secret against the configured one."""
        import hmac
        secret = settings.DMOBILI_WEBHOOK_SECRET
        if not (secret and provided):
            return False
        return hmac.compare_digest(str(secret), str(provided))
