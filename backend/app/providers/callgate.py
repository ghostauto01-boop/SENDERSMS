"""CallGate.app — call control, webhooks, health.

CallGate is the companion Android app to SMS-Gate: it exposes a small local
REST API on the handset (default ``http://<device-ip>:8084/api/v1``) protected
by HTTP Basic auth. It does NOT handle audio — it only starts / ends the
phone's own GSM call, which is exactly what we want: the user's phone places
the real call.

API surface (from https://github.com/call-gate-app/android-app):
    POST   /calls              {"call": {"phoneNumber": "+234..."}}  -> start call
    DELETE /calls                                            -> end active call
    POST   /webhooks           {"event": "call:started", "url": ...}
    GET    /webhooks
    DELETE /webhooks/{id}

Events: call:ringing | call:started | call:ended
Webhook envelope + HMAC signing are the same shape as SMS-Gate's.

Because CallGate is a LOCAL server (no cloud relay like SMS-Gate), the backend
can only reach it when the handset is network-reachable from the server
(same LAN, VPN/Tailscale, or a tunnel such as ngrok on the phone). When it is
not reachable the frontend falls back to a plain ``tel:`` link so the user can
still place the call from their own phone in one tap.
"""

import base64
import json
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8084
WEBHOOK_PATH_SUFFIX = "/api/v1/webhooks/callgate"
CALL_EVENTS = ("call:ringing", "call:started", "call:ended")
REQUIRED_EVENTS = ("call:started", "call:ended")


def _base(override: Optional[str] = None) -> str:
    raw = (override or settings.CALLGATE_BASE_URL or "").strip().rstrip("/")
    if not raw:
        return ""
    # Accept a bare "192.168.1.5" or "192.168.1.5:8084" and expand it.
    if not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}"
    if "/api/" not in raw:
        raw = f"{raw}/api/v1"
    return raw


def _creds(username: Optional[str] = None, password: Optional[str] = None):
    u = (username if username is not None else settings.CALLGATE_USERNAME or "").strip()
    p = (password if password is not None else settings.CALLGATE_PASSWORD or "").strip()
    return u, p


def _auth_header(username: Optional[str] = None, password: Optional[str] = None) -> dict:
    u, p = _creds(username, password)
    if not u or not p:
        return {}
    token = base64.b64encode(f"{u}:{p}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def is_configured(base_url: Optional[str] = None,
                  username: Optional[str] = None,
                  password: Optional[str] = None) -> bool:
    b = _base(base_url) if (base_url or settings.CALLGATE_BASE_URL) else ""
    u, p = _creds(username, password)
    return bool(b and u and p)


async def start_call_direct(phone_number: str,
                            base_url: Optional[str] = None,
                            username: Optional[str] = None,
                            password: Optional[str] = None) -> dict:
    """Ask the handset to place a GSM call. Returns success + detail."""
    base = _base(base_url)
    if not base:
        return {"success": False, "error": "CallGate base URL is not configured"}
    headers = {"Content-Type": "application/json", **_auth_header(username, password)}
    if "Authorization" not in headers:
        return {"success": False, "error": "CallGate credentials are not configured"}
    number = (phone_number or "").strip()
    if not number:
        return {"success": False, "error": "No phone number"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.CALLGATE_TIMEOUT)) as c:
            r = await c.post(f"{base}/calls", headers=headers,
                             json={"call": {"phoneNumber": number}})
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {"raw": r.text[:500]}
        logger.info("CALLGATE start %s -> HTTP %s %s", number, r.status_code,
                    json.dumps(data)[:200])
        if r.status_code < 400:
            return {"success": True, "status": "initiated", "raw": data}
        if r.status_code == 401:
            return {"success": False, "error": "Invalid CallGate username/password", "raw": data}
        if r.status_code == 400:
            return {"success": False,
                    "error": str(data.get("message") or data.get("error") or "Invalid number format")[:300],
                    "raw": data}
        return {"success": False,
                "error": str(data.get("message") or data.get("error") or f"HTTP {r.status_code}")[:300],
                "raw": data}
    except httpx.TimeoutException:
        return {"success": False,
                "error": "Timed out reaching the phone — is CallGate running and reachable from the server?"}
    except httpx.ConnectError:
        return {"success": False,
                "error": "Cannot reach the phone — check the IP/hostname, port 8084, and that server + phone share a network (or tunnel)."}
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}


async def end_call_direct(base_url: Optional[str] = None,
                          username: Optional[str] = None,
                          password: Optional[str] = None) -> dict:
    """Hang up the active call on the handset."""
    base = _base(base_url)
    if not base:
        return {"success": False, "error": "CallGate base URL is not configured"}
    headers = {"Content-Type": "application/json", **_auth_header(username, password)}
    if "Authorization" not in headers:
        return {"success": False, "error": "CallGate credentials are not configured"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.CALLGATE_TIMEOUT)) as c:
            r = await c.delete(f"{base}/calls", headers=headers)
        if r.status_code in (200, 201, 204):
            return {"success": True, "status": "ended"}
        if r.status_code == 404:
            return {"success": False, "error": "No active call on the phone"}
        if r.status_code == 401:
            return {"success": False, "error": "Invalid CallGate username/password"}
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {}
        return {"success": False,
                "error": str(data.get("message") or data.get("error") or f"HTTP {r.status_code}")[:300]}
    except httpx.TimeoutException:
        return {"success": False, "error": "Timed out reaching the phone"}
    except httpx.ConnectError:
        return {"success": False, "error": "Cannot reach the phone"}
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}


async def test_connection_direct(base_url: Optional[str] = None,
                                 username: Optional[str] = None,
                                 password: Optional[str] = None) -> dict:
    """Cheap connectivity check: list webhooks (auth + reachability)."""
    base = _base(base_url)
    u, p = _creds(username, password)
    if not base or not u or not p:
        return {"success": False, "online": False,
                "message": "CallGate base URL / username / password are not all set"}
    headers = {"Content-Type": "application/json", **_auth_header(username, password)}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.CALLGATE_TIMEOUT)) as c:
            r = await c.get(f"{base}/webhooks", headers=headers)
        if r.status_code == 401:
            return {"success": False, "online": False,
                    "message": "Phone reachable but username/password rejected (401)"}
        if r.status_code < 500:
            try:
                data = r.json() if r.text else []
            except Exception:
                data = []
            count = len(data) if isinstance(data, list) else 0
            return {"success": True, "online": True, "http": r.status_code,
                    "webhooks": count,
                    "message": f"Connected to CallGate ({count} webhook(s) registered)"}
        return {"success": False, "online": False, "http": r.status_code,
                "message": f"Phone answered with HTTP {r.status_code}"}
    except httpx.TimeoutException:
        return {"success": False, "online": False,
                "message": "Timed out — is the CallGate server started (tap Offline -> Online) and reachable?"}
    except httpx.ConnectError:
        return {"success": False, "online": False,
                "message": "Cannot reach the phone at that address — check IP, port 8084 and network"}
    except Exception as e:
        return {"success": False, "online": False, "message": str(e)[:300]}


async def list_webhooks_direct(base_url: Optional[str] = None,
                               username: Optional[str] = None,
                               password: Optional[str] = None) -> dict:
    base = _base(base_url)
    headers = {"Content-Type": "application/json", **_auth_header(username, password)}
    if not base or "Authorization" not in headers:
        return {"success": False, "error": "CallGate is not configured", "webhooks": []}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as c:
            r = await c.get(f"{base}/webhooks", headers=headers)
        data = r.json() if r.text else []
        if r.status_code >= 400:
            return {"success": False,
                    "error": str(data.get("message") if isinstance(data, dict) else f"HTTP {r.status_code}"),
                    "webhooks": []}
        return {"success": True, "webhooks": data if isinstance(data, list) else []}
    except Exception as e:
        return {"success": False, "error": str(e)[:300], "webhooks": []}


async def register_webhook_direct(url: str,
                                  events=None,
                                  base_url: Optional[str] = None,
                                  username: Optional[str] = None,
                                  password: Optional[str] = None) -> dict:
    """Register `url` for each call event. Idempotent; prunes stale URLs."""
    base = _base(base_url)
    headers = {"Content-Type": "application/json", **_auth_header(username, password)}
    if not base or "Authorization" not in headers:
        return {"success": False, "error": "CallGate is not configured"}
    if not url:
        return {"success": False, "error": "No webhook URL"}
    events = tuple(events or CALL_EVENTS)
    created, kept, deleted, errors = [], [], [], []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20)) as c:
            existing = []
            try:
                r = await c.get(f"{base}/webhooks", headers=headers)
                if r.status_code in (401, 403):
                    return {"success": False, "url": url, "registered": [],
                            "created": [], "kept": [], "deleted_stale": [],
                            "errors": [], "missing_required": list(REQUIRED_EVENTS),
                            "error": "Invalid CallGate username/password (401). Fix Settings -> Calls first."}
                if r.status_code < 400 and r.text:
                    existing = r.json() or []
            except Exception as e:
                logger.warning("CALLGATE list webhooks: %s", e)

            have = {(w.get("url"), w.get("event")) for w in existing if isinstance(w, dict)}
            for ev in events:
                if (url, ev) in have:
                    kept.append(ev)
                    continue
                try:
                    r = await c.post(f"{base}/webhooks", headers=headers,
                                     json={"event": ev, "url": url})
                    if r.status_code < 400:
                        created.append(ev)
                    else:
                        body = r.text[:200]
                        # Optional events may be rejected by older builds; only
                        # required ones fail the whole registration.
                        if ev in REQUIRED_EVENTS:
                            errors.append(f"{ev}: HTTP {r.status_code} {body}")
                        else:
                            logger.warning("CALLGATE optional event %s rejected: %s", ev, body)
                except Exception as e:
                    if ev in REQUIRED_EVENTS:
                        errors.append(f"{ev}: {e}")
            # Prune our own stale registrations pointing at an old URL.
            for w in existing:
                if not isinstance(w, dict):
                    continue
                wurl = w.get("url") or ""
                if (wurl.endswith(WEBHOOK_PATH_SUFFIX) and wurl != url
                        and (w.get("event") in events)):
                    try:
                        wid = w.get("id")
                        if wid is None:
                            continue
                        r = await c.delete(f"{base}/webhooks/{wid}", headers=headers)
                        if r.status_code < 400:
                            deleted.append(str(wid))
                    except Exception:
                        pass
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}

    registered = sorted(set(created) | set(kept))
    missing = [e for e in REQUIRED_EVENTS if e not in registered]
    return {"success": not missing and not errors, "url": url,
            "registered": registered, "created": created, "kept": kept,
            "deleted_stale": deleted, "errors": errors,
            "missing_required": missing}


async def delete_webhook_direct(webhook_id: str,
                                base_url: Optional[str] = None,
                                username: Optional[str] = None,
                                password: Optional[str] = None) -> dict:
    base = _base(base_url)
    headers = {"Content-Type": "application/json", **_auth_header(username, password)}
    if not base or "Authorization" not in headers:
        return {"success": False, "error": "CallGate is not configured"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as c:
            r = await c.delete(f"{base}/webhooks/{webhook_id}", headers=headers)
        if r.status_code < 400:
            return {"success": True}
        return {"success": False, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}
