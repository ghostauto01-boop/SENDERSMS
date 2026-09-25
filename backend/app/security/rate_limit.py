"""Rate limiting setup.

RATE_LIMIT_LOGIN / RATE_LIMIT_API were defined in config but never wired to
anything, leaving the login endpoint open to unlimited brute force. This
module builds the shared limiter and installs it on the app.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.config import settings

logger = logging.getLogger(__name__)


def _client_key(request: Request) -> str:
    """Identify the caller, honouring the proxy header used on Render.

    Render/nginx terminate TLS in front of the app, so request.client.host is
    the proxy address and would put every user in one bucket.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(
    key_func=_client_key,
    default_limits=[settings.RATE_LIMIT_API],
    headers_enabled=True,
    # Share counters across workers via Redis when available, but keep
    # working (in-memory, per-process) if the broker is down — a Redis
    # outage must not take authentication offline.
    storage_uri=getattr(settings, "REDIS_URL", None) or "memory://",
    in_memory_fallback_enabled=True,
    swallow_errors=True,
)


# Paths that must never be throttled by the global API limit:
#  - health: uptime pingers would eat the whole budget
#  - webhooks: signature-verified already, and inbound SMS arrives in bursts
#  - static/SPA assets: a single page load is many requests
EXEMPT_PREFIXES = (
    "/api/v1/health",
    "/api/v1/webhooks",
)


class ApiOnlySlowAPIMiddleware(SlowAPIMiddleware):
    """Apply the global rate limit to API traffic only."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/") or path.startswith(EXEMPT_PREFIXES):
            return await call_next(request)
        return await super().dispatch(request, call_next)


async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return a JSON 429 the frontend can display."""
    logger.warning(
        "Rate limit hit: %s %s from %s", request.method, request.url.path, _client_key(request)
    )
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Too many requests. Please slow down and try again shortly.",
        },
        headers={"Retry-After": "60"},
    )


def install_rate_limiting(app: FastAPI) -> None:
    """Attach the limiter, its error handler and middleware to the app."""
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
    app.add_middleware(ApiOnlySlowAPIMiddleware)
