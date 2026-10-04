"""Brevo (formerly Sendinblue) transactional email provider.

Everything the app needs from Brevo lives here and nowhere else, so an outage
or an API change has exactly one place to be fixed:

* :func:`send_email` — POST ``/v3/smtp/email`` (multipart HTML + text)
* :func:`test_api_key` — GET ``/v3/account``, used by the "Test connection"
  button so a bad key is caught when it is typed, not when a campaign runs
* :func:`list_senders` — GET ``/v3/senders`` so the UI can offer the addresses
  Brevo has actually verified
* :func:`sync_contact` / :func:`create_list` — optional CRM mirroring

The API key is passed in per call rather than read from settings: this app
supports MANY Brevo accounts at once (that is the whole point of the
"multiple API keys" feature), so the key belongs to the caller's account row.

Errors are returned as dicts (``{"success": False, "error": "..."}``) rather
than raised, matching the SMS-Gate provider contract, so the send pipeline can
record the failure on the message and try the fallback account.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://api.brevo.com/v3"
TIMEOUT = 30.0


def _base() -> str:
    """Brevo API root.

    ``BREVO_API_BASE`` lets a test environment point at a stand-in Brevo so a
    full send can be exercised without spending real sends or reaching the
    internet. Production never sets it.
    """
    import os

    return os.getenv("BREVO_API_BASE") or DEFAULT_BASE


def _headers(api_key: str) -> dict:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": (api_key or "").strip(),
    }


def _clean_error(response: httpx.Response, data: Any) -> str:
    """Turn a Brevo error body into one readable line."""
    if isinstance(data, dict):
        msg = data.get("message") or data.get("error") or ""
        code = data.get("code")
        if msg and code:
            return f"{msg} ({code})"[:500]
        if msg:
            return str(msg)[:500]
    text = (response.text or "").strip()
    return (text[:500] or f"HTTP {response.status_code}")


def _parse_recipients(to_email: str | list[str]) -> list[dict]:
    if isinstance(to_email, str):
        to_email = [to_email]
    return [{"email": a.strip()} for a in to_email if a and a.strip()]


async def send_email(
    api_key: str,
    *,
    to_email: str | list[str],
    to_name: str | None,
    subject: str,
    html: str | None,
    text: str | None,
    from_email: str,
    from_name: str,
    reply_to: str | None = None,
    tags: list[str] | None = None,
    track_opens: bool = True,
    track_clicks: bool = True,
    headers: dict | None = None,
    params: dict | None = None,
    attachments: list[dict] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> dict:
    """Send one transactional email through Brevo.

    Supports everything the transactional endpoint accepts that this app uses:
    HTML + plain-text alternatives, an envelope reply-to, custom headers (which
    is how threading and List-Unsubscribe are expressed), tags for analytics,
    open/click tracking, dynamic template params and file attachments.

    * ``attachments`` — ``[{"name": "quote.pdf", "content": "<base64>"}]``.
      Brevo caps a message at ~10MB once encoded, so callers must size-check
      before queueing (the API layer does).
    * ``headers`` — ``In-Reply-To`` / ``References`` keep a reply inside the
      recipient's existing thread; ``List-Unsubscribe`` +
      ``List-Unsubscribe-Post`` are what Gmail/Yahoo require from bulk senders.
    * ``cc`` / ``bcc`` — copied recipients, same as any mail client.

    Returns ``{"success": True, "provider_message_id": ..., "raw": {...}}`` or
    ``{"success": False, "error": "...", "status_code": ...}``.
    """
    recipients = _parse_recipients(to_email)
    if not recipients:
        return {"success": False, "error": "No recipient address"}
    if not from_email:
        return {"success": False, "error": "No From address configured on this account"}

    sender = {"email": from_email}
    if from_name:
        sender["name"] = from_name

    payload: dict = {
        "sender": sender,
        "to": recipients,
        "subject": subject or "(no subject)",
        "trackingOptions": {"open": bool(track_opens), "click": bool(track_clicks)},
    }
    if to_name and len(recipients) == 1:
        recipients[0]["name"] = to_name
    if html:
        payload["htmlContent"] = html
    if text:
        payload["textContent"] = text
    if not html and not text:
        payload["textContent"] = " "
    if cc:
        copied = _parse_recipients(cc)
        if copied:
            payload["cc"] = copied
    if bcc:
        blind = _parse_recipients(bcc)
        if blind:
            payload["bcc"] = blind
    if reply_to:
        payload["replyTo"] = {"email": reply_to}
    if tags:
        payload["tags"] = [t for t in tags if t][:10]
    if headers:
        # Brevo rejects an empty header object; only send real headers.
        clean_headers = {k: v for k, v in headers.items() if k and v}
        if clean_headers:
            payload["headers"] = clean_headers
    if params:
        payload["params"] = params
    if attachments:
        # Each entry: {"name": ..., "content": base64} and optionally a URL
        # instead of content. Anything malformed is dropped rather than failing
        # the whole send on a bad attachment.
        clean = []
        for item in attachments:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            content = item.get("content") or item.get("content_base64")
            url = item.get("url")
            if not name or not (content or url):
                continue
            entry = {"name": name[:255]}
            if content:
                entry["content"] = str(content).strip()
            elif url:
                entry["url"] = url
            clean.append(entry)
        if clean:
            payload["attachment"] = clean[:20]

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT)) as client:
            r = await client.post(f"{_base()}/smtp/email", headers=_headers(api_key), json=payload)
            data = r.json() if r.text else {}
            if r.status_code < 400:
                return {
                    "success": True,
                    "provider_message_id": str(data.get("messageId") or ""),
                    "raw": data,
                }
            logger.warning("BREVO: send failed HTTP %s %s", r.status_code, json.dumps(data)[:300])
            return {
                "success": False,
                "error": _clean_error(r, data),
                "status_code": r.status_code,
                "raw": data,
            }
    except httpx.TimeoutException:
        return {"success": False, "error": "Brevo request timed out"}
    except Exception as exc:  # noqa: BLE001 — network/DNS/TLS, never crash a send
        logger.warning("BREVO: send error %s", exc)
        return {"success": False, "error": str(exc)[:500]}


async def test_api_key(api_key: str) -> dict:
    """GET /v3/account — proves the key works and returns quota info."""
    if not (api_key or "").strip():
        return {"success": False, "error": "No API key saved"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            r = await client.get(f"{_base()}/account", headers=_headers(api_key))
            data = r.json() if r.text else {}
            if r.status_code < 400:
                plan = (data.get("plan") or [{}])
                first = plan[0] if isinstance(plan, list) and plan else {}
                return {
                    "success": True,
                    "email": data.get("email"),
                    "company_name": data.get("companyName"),
                    "plan": first.get("type") if isinstance(first, dict) else None,
                    "credits": first.get("credits") if isinstance(first, dict) else None,
                    "raw": data,
                }
            return {"success": False, "error": _clean_error(r, data), "status_code": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)[:500]}


async def list_senders(api_key: str) -> dict:
    """GET /v3/senders — the From addresses Brevo has verified for this key."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            r = await client.get(f"{_base()}/senders", headers=_headers(api_key))
            data = r.json() if r.text else {}
            if r.status_code < 400:
                senders = data.get("senders") or []
                return {
                    "success": True,
                    "senders": [
                        {"id": s.get("id"), "email": s.get("email"), "name": s.get("name"), "active": s.get("active")}
                        for s in senders
                    ],
                }
            return {"success": False, "error": _clean_error(r, data)}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)[:500]}


async def list_domains(api_key: str) -> dict:
    """GET /v3/senders/domains — which sending domains are authenticated.

    This is the single biggest deliverability lever a user has: mail sent from a
    domain whose SPF/DKIM Brevo has not verified lands in spam or is rejected
    outright. The Deliverability tab shows exactly this, per sender.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            r = await client.get(f"{_base()}/senders/domains", headers=_headers(api_key))
            data = r.json() if r.text else {}
            if r.status_code < 400:
                domains = data.get("domains") or []
                return {
                    "success": True,
                    "domains": [
                        {
                            "domain": d.get("domain_name") or d.get("domain"),
                            "verified": bool(d.get("verified")),
                            "authenticated": bool(d.get("authenticated")),
                            "dkim": d.get("dkim"),
                            "spf": d.get("spf"),
                        }
                        for d in domains
                    ],
                    "raw": data,
                }
            return {"success": False, "error": _clean_error(r, data), "status_code": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)[:500]}


async def create_list(api_key: str, name: str, folder_id: int | None = None) -> dict:
    """Create a Brevo contact list (used to mirror an app list into Brevo)."""
    payload: dict = {"name": name}
    if folder_id:
        payload["folderId"] = folder_id
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            r = await client.post(f"{_base()}/contacts/lists", headers=_headers(api_key), json=payload)
            data = r.json() if r.text else {}
            if r.status_code < 400:
                return {"success": True, "list_id": data.get("id"), "raw": data}
            return {"success": False, "error": _clean_error(r, data)}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)[:500]}


async def add_contacts_to_list(api_key: str, list_id: int, emails: list[str]) -> dict:
    """POST /v3/contacts/lists/{id}/contacts/add — best-effort mirroring."""
    if not emails:
        return {"success": True, "added": 0}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            r = await client.post(
                f"{_base()}/contacts/lists/{list_id}/contacts/add",
                headers=_headers(api_key),
                json={"emails": emails[:1000]},
            )
            data = r.json() if r.text else {}
            if r.status_code < 400:
                return {"success": True, "added": len(emails), "raw": data}
            return {"success": False, "error": _clean_error(r, data)}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)[:500]}


async def upsert_contact(
    api_key: str,
    *,
    email: str,
    attributes: Optional[dict] = None,
    list_ids: Optional[list[int]] = None,
) -> dict:
    """POST /v3/contacts — create or update one contact in Brevo."""
    payload: dict = {"email": email, "updateEnabled": True}
    if attributes:
        payload["attributes"] = attributes
    if list_ids:
        payload["listIds"] = list_ids
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            r = await client.post(f"{_base()}/contacts", headers=_headers(api_key), json=payload)
            data = r.json() if r.text else {}
            if r.status_code < 400:
                return {"success": True, "raw": data}
            return {"success": False, "error": _clean_error(r, data)}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)[:500]}


async def account_statistics(api_key: str, *, days: int = 14) -> dict:
    """Aggregated delivery statistics, used by the Email Manager analytics tab."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            end = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).date()
            start = end - __import__("datetime").timedelta(days=days - 1)
            r = await client.get(
                f"{_base()}/smtp/statistics/aggregatedReport",
                headers=_headers(api_key),
                params={"startDate": start.isoformat(), "endDate": end.isoformat()},
            )
            data = r.json() if r.text else {}
            if r.status_code < 400:
                return {"success": True, "stats": data}
            return {"success": False, "error": _clean_error(r, data)}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)[:500]}
