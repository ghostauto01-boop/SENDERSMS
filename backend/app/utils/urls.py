"""Helpers for building absolute URLs that point back at this deployment.

WHY THIS IS REQUEST-AWARE
-------------------------
Every URL in the MCP OAuth metadata (the protected-resource document, the
authorization-server document, the ``WWW-Authenticate`` challenge, the client
registration and redirect URLs) must be **absolute and reachable from the
internet**. ChatGPT and Claude both refuse a relative ``resource``, so a
deployment that never set ``PUBLIC_BASE_URL`` used to answer the connector with
a 503 and the settings screen with "PUBLIC_BASE_URL is not set" — which reads as
"the MCP is broken" when the endpoint itself is fine.

So the base URL is resolved in this order:

1. ``PUBLIC_BASE_URL`` — the explicit, authoritative setting. Use it when the
   public address differs from the address the request arrived on (a proxy that
   rewrites Host, a custom domain, a tunnel).
2. ``RENDER_EXTERNAL_URL`` — injected by Render, so existing deployments keep
   working untouched.
3. **The request in flight** — scheme and host taken from the forwarded headers
   a reverse proxy sets (``X-Forwarded-Proto``/``-Host``, ``Forwarded``), then
   from ``Origin``, then from ``Host``. A browser session, an in-app connector
   test or a curl call therefore produces working metadata even with nothing
   configured.

The request is stashed in a :class:`contextvars.ContextVar` by the middleware in
``app.main`` so the hundred call sites that already call :func:`public_base_url`
stay unchanged (they are not all inside a request handler: the poller and the
Celery tasks call some of them, and those keep getting ``None`` unless
PUBLIC_BASE_URL is set — which is correct, because a background job has no
request to learn the public address from).
"""

from __future__ import annotations

import contextvars
import os
from typing import Optional
from urllib.parse import urlsplit

from starlette.requests import Request

from app.config import settings

WEBHOOK_PATH = "/api/v1/webhooks/smsgateway"

#: Hosts that cannot be the public address of a deployment. Used only to decide
#: whether an ``http://`` request should be upgraded to ``https://``.
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1"}

_CURRENT_BASE: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_public_base_url", default=None
)
_CURRENT_BASE_SOURCE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_public_base_url_source", default="none"
)


def normalize_base(value: Optional[str]) -> Optional[str]:
    """Turn whatever is configured into ``scheme://host[:port]`` (no trailing /)."""
    raw = (value or "").strip()
    if not raw:
        return None
    if "//" not in raw:
        # "my-app.onrender.com" and "my-app.onrender.com/" both mean https.
        raw = f"https://{raw}"
    parsed = urlsplit(raw)
    if not parsed.netloc:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _first_header(request: Request, name: str) -> str:
    """First value of a comma-separated header (``https, http`` -> ``https``)."""
    raw = request.headers.get(name) or ""
    return raw.split(",")[0].strip()


def base_url_from_request(request: Request) -> Optional[str]:
    """Derive this deployment's public base URL from a request in flight.

    Proxy-aware, and deliberately conservative: a wrong absolute URL is worse
    than none, because it sends the AI client somewhere that does not exist.
    """
    # 1. The standards-track ``Forwarded: proto=https;host=app.example.com``.
    forwarded = request.headers.get("forwarded") or ""
    fwd_proto = fwd_host = ""
    for part in forwarded.split(";"):
        key, _, value = part.strip().partition("=")
        value = value.strip('"')
        if key.lower() == "proto":
            fwd_proto = value
        elif key.lower() == "host":
            fwd_host = value

    proto = fwd_proto or _first_header(request, "x-forwarded-proto")
    host = fwd_host or _first_header(request, "x-forwarded-host")

    origin = request.headers.get("origin") or ""
    if not host and origin:
        parsed = urlsplit(origin)
        host = parsed.netloc
        proto = proto or parsed.scheme

    if not host:
        host = request.headers.get("host") or ""
    if not host:
        return None

    if not proto:
        proto = request.url.scheme or "http"

    # A host that is not loopback and not a bare IP, reached over http, is
    # almost always a TLS-terminating proxy that reported http upstream
    # (``X-Forwarded-Proto`` missing). Clients reject http OAuth metadata, so
    # assume https there rather than hand them a URL they will refuse.
    name = host.split(":")[0].lower()
    if proto == "http" and name not in _LOOPBACK_HOSTS and not _looks_like_ip(name):
        proto = "https"

    return normalize_base(f"{proto}://{host}")


def _looks_like_ip(name: str) -> bool:
    parts = name.split(".")
    return len(parts) == 4 and all(p.isdigit() for p in parts)


def bind_request_base(request: Request) -> None:
    """Remember this request's base URL for :func:`public_base_url`.

    Called once per request by the middleware; the values are cheap to compute
    and the alternative — threading a Request through every helper that builds
    a URL — would touch code that also runs outside a request.
    """
    if _CURRENT_BASE.get() is not None:
        # Already bound (an in-process sub-request, e.g. the connector
        # self-test). The outermost request wins: it is the one the client
        # actually used.
        return
    derived = base_url_from_request(request)
    if derived:
        _CURRENT_BASE.set(derived)
        _CURRENT_BASE_SOURCE.set("request")


def clear_request_base() -> None:
    _CURRENT_BASE.set(None)
    _CURRENT_BASE_SOURCE.set("none")


def public_base_url_source() -> str:
    """``env`` | ``render`` | ``request`` | ``none`` — what the base came from.

    Shown in Settings → AI (MCP) and returned by the connector self-test so an
    operator can tell "this is configured" from "this was guessed from the
    request" without reading the server logs.
    """
    if normalize_base(settings.PUBLIC_BASE_URL):
        return "env"
    if normalize_base(os.environ.get("RENDER_EXTERNAL_URL")):
        return "render"
    if _CURRENT_BASE.get():
        return "request"
    return "none"


def public_base_url() -> Optional[str]:
    """The externally reachable base URL of this deployment, if it can be known."""
    for candidate in (
        settings.PUBLIC_BASE_URL,
        os.environ.get("RENDER_EXTERNAL_URL"),
        _CURRENT_BASE.get(),
    ):
        normalized = normalize_base(candidate)
        if normalized:
            return normalized
    return None


def webhook_url() -> Optional[str]:
    """Absolute URL the SMS gateway should POST events to."""
    base = public_base_url()
    return f"{base}{WEBHOOK_PATH}" if base else None
