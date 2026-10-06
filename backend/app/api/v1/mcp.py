"""
HTTP surface for the MCP server, its OAuth 2.1 authorization server, and the
token/connection UI that manages them.

Three routers, on purpose:

* ``protocol_router`` — mounted at ``/mcp``. The canonical endpoint every client
  can use, and the one existing tokens and documentation already point at.
* ``public_router`` — mounted at the app root, because the URLs an AI client is
  told to read are absolute and fixed by the specs:
  ``/.well-known/oauth-protected-resource/<path>`` (RFC 9728),
  ``/.well-known/oauth-authorization-server`` (RFC 8414),
  ``/.well-known/openid-configuration`` (OIDC discovery, which the 2025-11-25
  MCP revision accepts in place of the OAuth one),
  ``/connectors/<key>/oauth/{register,authorize,token,revoke}`` and
  ``/connectors/<key>/mcp`` — one endpoint per client, so ChatGPT, Claude and an
  Arena agent each get their own resource identifier, redirect-URI allowlist and
  tool set.
* ``router`` — ``/api/v1/mcp/*``, the app's own management endpoints, protected
  by the normal login session rather than by an MCP credential.

Session discipline matters here: reading the credential, running the tool, and
writing the audit row each get their own short-lived session, because the tool
call goes back into this same app and must not fight the request's own
transaction for the database (see ``app/mcp/server.py``).
"""

import hashlib
import json
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory, get_db
from app.mcp import connectors as C
from app.mcp import oauth as oauth_flow
from app.mcp import server as mcp_server
from app.models.mcp import McpCall, McpToken
from app.models.mcp_oauth import McpOAuthClient, McpOAuthToken
from app.models.user import User
from app.security.auth import apply_session_cookie, create_access_token, get_current_user
from app.utils.urls import public_base_url, public_base_url_source

logger = logging.getLogger(__name__)

#: The stdio bridge shipped in the repo (tools/arena-connector). Served by
#: /api/v1/mcp/bridge so the operator can fetch it without cloning anything.
BRIDGE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..",
                 "tools", "arena-connector", "arena_mcp_bridge.py")
)

#: Token management + connector administration, for the app's own settings screen.
router = APIRouter()
#: The canonical MCP endpoint, mounted at /mcp.
protocol_router = APIRouter()
#: Discovery documents, per-connector MCP endpoints and the OAuth server itself.
public_router = APIRouter()


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

#: In-memory rate limits for the unauthenticated OAuth surface. A deployed
#: single-instance app is the only shape this project runs as, so a dict is
#: enough and avoids a Redis dependency on the path ChatGPT retries hardest.
_BUCKETS: dict[str, deque] = defaultdict(deque)
_RATE_LIMITS = {"register": (20, 3600), "token": (120, 600), "authorize": (120, 600),
                "revoke": (60, 600)}


def _rate_limited(action: str, request: Request) -> bool:
    from app.mcp.diagnostics import is_selftest

    if is_selftest(request.headers):
        # The built-in connector test replays a whole handshake from one
        # address; counting it would lock the operator out of the real thing.
        return False
    limit, window = _RATE_LIMITS.get(action, (60, 600))
    key = f"{action}:{(request.client.host if request.client else '?')}"
    now = time.time()
    bucket = _BUCKETS[key]
    while bucket and bucket[0] < now - window:
        bucket.popleft()
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False


def _wants_sse(request: Request) -> bool:
    """Should the reply be one SSE event instead of a JSON body?

    The spec lets a server answer a POST with either ``application/json`` or
    ``text/event-stream``, and the reference implementations pick SSE whenever
    the client says it accepts one — so that is what this does.
    """
    accept = (request.headers.get("accept") or "").lower()
    return "text/event-stream" in accept


_EVENT_COUNTER = 0


def _rpc_response(payload: dict | list | None, *, sse: bool, status_code: int = 200,
                  session_id: str | None = None) -> Response:
    """MCP responses: plain JSON, or one SSE event carrying the same JSON."""
    global _EVENT_COUNTER
    headers: dict[str, str] = {}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    if payload is None:
        # A notification (e.g. notifications/initialized) gets 202 and no body.
        return Response(status_code=202, headers=headers)
    if sse:
        _EVENT_COUNTER += 1
        body = (
            f"id: {_EVENT_COUNTER}\n"
            f"event: message\n"
            f"data: {json.dumps(payload)}\n\n"
        )
        return Response(content=body, media_type="text/event-stream",
                        status_code=status_code, headers=headers)
    return JSONResponse(content=payload, status_code=status_code, headers=headers)


def _challenge(profile: C.ConnectorProfile) -> str:
    """The ``WWW-Authenticate`` header Claude and ChatGPT both look for.

    Claude's documentation is explicit: "Return an actual 401 Unauthorized with
    a WWW-Authenticate challenge. Claude does not honor the challenge on a 200
    response." The ``resource_metadata`` parameter is how the client discovers
    where to start the OAuth flow, so it must be here, on a 401.
    """
    prm = profile.well_known_prm()
    scopes = " ".join(C.SCOPES)
    if prm.startswith("http"):
        return f'Bearer resource_metadata="{prm}", scope="{scopes}"'
    return f'Bearer scope="{scopes}"'


def _unauthorized(profile: C.ConnectorProfile) -> JSONResponse:
    modes = ", ".join(profile.auth_modes)
    detail = (
        f"Not authorized on the {profile.label} connector. This endpoint ({profile.resource}) "
        f"authenticates with: {modes}. "
    )
    if "oauth" in profile.auth_modes:
        detail += (
            f"OAuth clients should read {profile.well_known_prm()} and follow the authorization "
            "server it names. "
        )
    if "bearer" in profile.auth_modes:
        detail += (
            "Header clients should create a token in the app under Settings → AI (MCP) and send "
            "'Authorization: Bearer <token>' (a ?token= query parameter also works)."
        )
    return JSONResponse(
        status_code=401,
        content={
            "jsonrpc": "2.0",
            "error": {"code": -32001, "message": detail},
            "id": None,
        },
        headers={"WWW-Authenticate": _challenge(profile)},
    )


async def _dispatch(db: AsyncSession, token: mcp_server.TokenView, message: dict | list,
                    profile: C.ConnectorProfile, toolset: str | None = None):
    """Run one JSON-RPC message (or a batch) and return the reply payload."""
    if isinstance(message, list):
        # Batches were dropped from MCP, but a JSON-RPC batch is still legal
        # JSON-RPC, so answer each element instead of failing the whole request.
        replies = []
        for item in message:
            if isinstance(item, dict):
                reply = await mcp_server.dispatch(db, token, item, profile, toolset)
                if reply is not None:
                    replies.append(reply)
        return replies or None
    if not isinstance(message, dict):
        return {
            "jsonrpc": "2.0", "id": None,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
    return await mcp_server.dispatch(db, token, message, profile, toolset)


async def _handle(request: Request, profile: C.ConnectorProfile) -> Response:
    """One MCP POST, on whichever connector endpoint it arrived at."""
    raw_token = mcp_server.token_from_request(request)

    # 1. Identify the caller and read this connector's switches, then let that
    #    session go. The tool set is the operator's choice per connector, so it
    #    has to be read before any tool list is published.
    toolset = profile.toolset
    async with async_session_factory() as db:
        token = await mcp_server.authenticate(db, raw_token, profile=profile)
        if token is not None:
            connection = await oauth_flow.get_connection(db, profile.key)
            toolset = connection.toolset or profile.toolset
            await db.commit()
    if token is None:
        return _unauthorized(profile)

    try:
        message = await request.json()
    except Exception:
        return _rpc_response(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": -32700, "message": "Parse error: body must be JSON"}},
            sse=_wants_sse(request),
        )

    # 2. Establish the authority the tool call runs with, in its own session.
    #    An OAuth token already names the operator who approved the connection;
    #    a static token acts as the app's operator account.
    async with async_session_factory() as db:
        admin_id = int(token.id) if token.kind == "oauth" and token.id else None
        admin_id = admin_id or await mcp_server.admin_user_id(db)
    mcp_server.CURRENT_ADMIN_ID.set(admin_id)

    # 3. Run the tool, then write the audit trail — again, nothing overlapping.
    async with async_session_factory() as db:
        reply = await _dispatch(db, token, message, profile, toolset)
        await db.commit()

    return _rpc_response(reply, sse=_wants_sse(request),
                         session_id=request.headers.get("mcp-session-id"))


def _stream_unavailable(profile: C.ConnectorProfile) -> JSONResponse:
    """GET on an MCP endpoint: 405, because this server has nothing to push.

    Streamable HTTP makes the server-initiated GET stream optional. Answering
    405 (rather than a JSON body) is what the spec asks for, and it is what
    tells a client to fall back to POST-only.
    """
    return JSONResponse(
        status_code=405,
        content={
            "error": "This MCP server is stateless and opens no server-initiated stream.",
            "hint": f"POST your JSON-RPC requests to {profile.resource}.",
        },
        headers={"Allow": "POST, DELETE, OPTIONS"},
    )


# ---------------------------------------------------------------------------
# The canonical MCP endpoint (/mcp) and the per-connector ones
# ---------------------------------------------------------------------------


@protocol_router.post("")
@protocol_router.post("/")
async def mcp_endpoint(request: Request) -> Response:
    """The MCP endpoint every client can use. Stateless: each call carries its token."""
    return await _handle(request, C.GENERIC)


@protocol_router.get("")
@protocol_router.get("/")
async def mcp_stream(request: Request) -> Response:
    return _stream_unavailable(C.GENERIC)


@protocol_router.delete("")
@protocol_router.delete("/")
async def mcp_teardown() -> Response:
    """Session teardown — nothing to tear down, but clients expect a 200."""
    return Response(status_code=200)


@public_router.post("/connectors/{key}/mcp")
async def connector_endpoint(key: str, request: Request) -> Response:
    """One MCP endpoint per AI client, each with its own resource identifier."""
    profile = C.get(key)
    if profile.key == "generic" and key != "generic":
        return JSONResponse(status_code=404, content={"error": f"Unknown connector '{key}'",
                                                      "connectors": [c.key for c in C.CONNECTORS]})
    return await _handle(request, profile)


@public_router.get("/connectors/{key}/mcp")
async def connector_stream(key: str, request: Request) -> Response:
    return _stream_unavailable(C.get(key))


@public_router.delete("/connectors/{key}/mcp")
async def connector_teardown(key: str) -> Response:
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Discovery documents
# ---------------------------------------------------------------------------


def _profile_for_resource_path(path: str) -> C.ConnectorProfile:
    """Which connector does a well-known path describe?

    RFC 9728/8414 both insert the resource's path after the well-known prefix,
    so ``/.well-known/oauth-protected-resource/connectors/chatgpt/mcp`` describes
    ``/connectors/chatgpt/mcp``. Clients differ on whether they include the path
    segment at all, so every spelling resolves.
    """
    clean = "/" + (path or "").strip("/")
    if clean in ("/", ""):
        return C.GENERIC
    return C.by_path(clean)


@public_router.get("/.well-known/oauth-protected-resource")
@public_router.get("/.well-known/oauth-protected-resource/{path:path}")
async def protected_resource_metadata(path: str = "") -> JSONResponse:
    """RFC 9728 — tells a client which authorization server protects this MCP URL."""
    profile = _profile_for_resource_path(path)
    if not public_base_url():
        return JSONResponse(
            status_code=503,
            content={
                "error": "PUBLIC_BASE_URL is not set on this deployment.",
                "detail": (
                    "Every URL in the OAuth metadata has to be absolute and reachable from the "
                    "internet, so without PUBLIC_BASE_URL a connector cannot be established. "
                    "Set it in the environment (e.g. https://your-app.onrender.com) and restart."
                ),
            },
        )
    return JSONResponse(
        C.protected_resource_metadata(profile),
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"},
    )


@public_router.get("/.well-known/oauth-authorization-server")
@public_router.get("/.well-known/oauth-authorization-server/{path:path}")
@public_router.get("/.well-known/openid-configuration")
@public_router.get("/.well-known/openid-configuration/{path:path}")
async def authorization_server_metadata(path: str = "") -> JSONResponse:
    """RFC 8414 (and the OIDC discovery alias the 2025-11-25 MCP revision allows).

    Served unauthenticated and fast — Claude's testing guide wants metadata
    endpoints to answer in well under five seconds, and both clients read this
    before they will show a sign-in window at all.
    """
    profile = _profile_for_resource_path(path)
    body = C.authorization_server_metadata(profile)
    body["issuer"] = oauth_flow.issuer()
    return JSONResponse(body, headers={"Access-Control-Allow-Origin": "*",
                                       "Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# OAuth 2.1: dynamic client registration
# ---------------------------------------------------------------------------


async def _oauth_body(request: Request) -> dict:
    raw = await request.body()
    return oauth_flow.parse_form(raw, request.headers.get("content-type") or "")


@public_router.post("/oauth/register", status_code=201)
@public_router.post("/connectors/{key}/oauth/register", status_code=201)
async def register(request: Request, key: str = "generic", db: AsyncSession = Depends(get_db)):
    """RFC 7591 dynamic client registration.

    ChatGPT calls this once per connector when the plugin builder chose DCR over
    a Client ID Metadata Document; Claude calls it whenever the server does not
    advertise CIMD. The body is JSON, which is the only encoding DCR uses.
    """
    profile = C.get(key)
    if not profile.supports_oauth():
        raise HTTPException(400, f"The {profile.label} connector does not use OAuth")
    if _rate_limited("register", request):
        raise HTTPException(429, "Too many registrations from this address; try again later")
    payload = await _oauth_body(request)
    try:
        result = await oauth_flow.register_client(db, profile, payload)
        await db.commit()
    except oauth_flow.OAuthError as exc:
        await db.rollback()
        return JSONResponse(status_code=exc.status_code, content=exc.body())
    return JSONResponse(status_code=201, content=result)


@public_router.get("/oauth/register/{client_id}")
@public_router.get("/connectors/{key}/oauth/register/{client_id}")
async def read_registration(client_id: str, request: Request, key: str = "generic",
                            db: AsyncSession = Depends(get_db)):
    """RFC 7592 — a client reading its own registration back."""
    bearer = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    client = (
        await db.execute(select(McpOAuthClient).where(McpOAuthClient.client_id == client_id))
    ).scalar_one_or_none()
    if client is None or not client.registration_access_token_hash:
        raise HTTPException(404, "Unknown registration")
    if not secrets.compare_digest(client.registration_access_token_hash,
                                  hashlib.sha256(bearer.encode()).hexdigest()):
        raise HTTPException(401, "Wrong registration access token")
    return JSONResponse({
        "client_id": client.client_id,
        "client_name": client.client_name,
        "redirect_uris": oauth_flow.client_redirect_uris(client),
        "grant_types": json.loads(client.grant_types or "[]"),
        "response_types": json.loads(client.response_types or "[]"),
        "token_endpoint_auth_method": client.token_endpoint_auth_method,
        "scope": client.scope,
    })


# ---------------------------------------------------------------------------
# OAuth 2.1: authorize (login + consent, server-rendered)
# ---------------------------------------------------------------------------


def _ticket(admin_id: int) -> str:
    """A short-lived proof that the operator signed in on *this* popup.

    The session cookie is also set, but a connector's authorization window is
    sometimes an iframe on chatgpt.com or claude.ai, where a SameSite=Lax cookie
    is not sent. Carrying the ticket in the form means the flow works either way.
    """
    from datetime import timedelta

    return create_access_token({"sub": str(admin_id), "flow": "mcp_oauth"},
                               expires_delta=timedelta(minutes=10))


def _ticket_admin(ticket: str | None) -> int | None:
    """The operator id inside a flow ticket, or None if it is missing/expired."""
    if not ticket:
        return None
    from app.security.auth import decode_access_token

    claims = decode_access_token(ticket)
    if not claims or claims.get("flow") != "mcp_oauth":
        return None
    try:
        return int(claims.get("sub"))
    except (TypeError, ValueError):
        return None


async def _session_admin(request: Request, db: AsyncSession) -> int | None:
    cookie = request.cookies.get("sendsms_session")
    if not cookie:
        return None
    from app.security.auth import decode_access_token

    claims = decode_access_token(cookie)
    if not claims:
        return None
    try:
        admin_id = int(claims.get("sub"))
    except (TypeError, ValueError):
        return None
    user = (await db.execute(select(User).where(User.id == admin_id))).scalar_one_or_none()
    return admin_id if user is not None and user.is_active else None


async def _authorize_get(request: Request, profile: C.ConnectorProfile,
                         db: AsyncSession) -> Response:
    """Render the right page for where this authorization has got to."""
    if not public_base_url():
        return HTMLResponse(
            oauth_flow.error_page(
                "PUBLIC_BASE_URL is not set on this deployment.",
                "OAuth needs absolute, publicly reachable URLs. Set PUBLIC_BASE_URL in the "
                "environment and restart.",
                connector=profile.label,
            ),
            status_code=503,
        )
    params = dict(request.query_params)
    try:
        authorization = await oauth_flow.validate_authorization_request(db, profile, params)
    except oauth_flow.OAuthError as exc:
        return HTMLResponse(
            oauth_flow.error_page(f"{exc.error}: {exc.description}", "", connector=profile.label),
            status_code=exc.status_code,
        )
    await db.commit()

    admin_id = await _session_admin(request, db)
    action = f"{profile.oauth_path}/authorize?{request.url.query}"
    client_name = authorization.client.client_name or profile.label

    if admin_id is None:
        return HTMLResponse(oauth_flow.login_page(
            action,
            connector=profile.label,
            client_name=client_name,
            allow_one_tap=not settings.MCP_OAUTH_REQUIRE_LOGIN,
        ))

    return HTMLResponse(oauth_flow.consent_page(
        action,
        connector=profile.label,
        client_name=client_name,
        scope=authorization.scope,
        endpoint=profile.resource,
        client_id=authorization.client.client_id,
        ticket=_ticket(admin_id),
    ))


async def _authorize_post(request: Request, profile: C.ConnectorProfile,
                          db: AsyncSession) -> Response:
    """Handle a login submission or a consent decision."""
    form_data = await request.form()
    form = dict(form_data)
    params = dict(request.query_params)
    action = f"{profile.oauth_path}/authorize?{request.url.query}"

    try:
        authorization = await oauth_flow.validate_authorization_request(db, profile, params)
    except oauth_flow.OAuthError as exc:
        return HTMLResponse(
            oauth_flow.error_page(f"{exc.error}: {exc.description}", "", connector=profile.label),
            status_code=exc.status_code,
        )
    client_name = authorization.client.client_name or profile.label

    if "decision" not in form:
        # --- the login step -------------------------------------------------
        if _rate_limited("authorize", request):
            return HTMLResponse(oauth_flow.login_page(
                action, connector=profile.label, client_name=client_name,
                error="Too many attempts from this address. Wait a minute and try again.",
                allow_one_tap=not settings.MCP_OAUTH_REQUIRE_LOGIN,
            ), status_code=429)
        password = str(form.get("password") or "").strip()
        one_tap = bool(form.get("one_tap"))
        if not password and (one_tap or not settings.MCP_OAUTH_REQUIRE_LOGIN):
            # The app's own login wall is one tap; a connector may not be a
            # weaker door than the app it opens.
            from app.security.auth import ensure_admin

            user = await ensure_admin(db)
        else:
            from app.api.v1.auth import _authenticate as authenticate_credentials

            user = await authenticate_credentials(db, settings.ADMIN_USERNAME, password)
        if user is None:
            await db.rollback()
            return HTMLResponse(oauth_flow.login_page(
                action, connector=profile.label, client_name=client_name,
                error="That password was not accepted.",
                allow_one_tap=not settings.MCP_OAUTH_REQUIRE_LOGIN,
            ), status_code=401)
        await db.commit()
        response = HTMLResponse(oauth_flow.consent_page(
            action,
            connector=profile.label,
            client_name=client_name,
            scope=authorization.scope,
            endpoint=profile.resource,
            client_id=authorization.client.client_id,
            ticket=_ticket(user.id),
        ))
        apply_session_cookie(response, create_access_token({"sub": str(user.id), "role": user.role}))
        return response

    # --- the consent step ---------------------------------------------------
    admin_id = _ticket_admin(str(form.get("ticket") or "")) or await _session_admin(request, db)
    if admin_id is None:
        return HTMLResponse(oauth_flow.login_page(
            action, connector=profile.label, client_name=client_name,
            error="Your approval expired. Sign in again.",
            allow_one_tap=not settings.MCP_OAUTH_REQUIRE_LOGIN,
        ))

    decision = str(form.get("decision") or "").lower()
    if decision != "approve":
        return RedirectResponse(
            oauth_flow.build_redirect(authorization, None, error="access_denied",
                                      description="The operator declined this connection."),
            status_code=302,
        )

    # The consent form posts one checkbox per scope; read is always on, and the
    # result is never wider than what the operator was shown on that page.
    ticked = {str(v) for v in form_data.getlist("scope")} | {"read"}
    allowed = set(authorization.scope.split())
    granted = " ".join(name for name in ("read", "write") if name in allowed and name in ticked) \
        or "read"

    code = await oauth_flow.issue_code(db, authorization, admin_id, scope=granted)
    await db.commit()
    return RedirectResponse(oauth_flow.build_redirect(authorization, code), status_code=302)


@public_router.get("/oauth/authorize")
@public_router.get("/connectors/{key}/oauth/authorize")
async def authorize_get(request: Request, key: str = "generic",
                        db: AsyncSession = Depends(get_db)) -> Response:
    return await _authorize_get(request, C.get(key), db)


@public_router.post("/oauth/authorize")
@public_router.post("/connectors/{key}/oauth/authorize")
async def authorize_post(request: Request, key: str = "generic",
                         db: AsyncSession = Depends(get_db)) -> Response:
    return await _authorize_post(request, C.get(key), db)


@public_router.get("/oauth/approved")
async def approved_page() -> HTMLResponse:
    """Shown only if a client displays our page instead of following the redirect."""
    return HTMLResponse(oauth_flow.approved_page(connector="MCP", scope="read"))


# ---------------------------------------------------------------------------
# OAuth 2.1: token
# ---------------------------------------------------------------------------


async def _authenticate_client(db: AsyncSession, profile: C.ConnectorProfile, request: Request,
                               params: dict) -> McpOAuthClient:
    """Prove which client is asking for tokens.

    ``none`` (public client + PKCE) is what ChatGPT and Claude use. The secret
    methods and ``private_key_jwt`` are supported because our own metadata
    advertises them and a client is entitled to take us at our word.
    """
    client_id = str(params.get("client_id") or "").strip()
    header_auth = request.headers.get("authorization") or ""
    if not client_id and header_auth.lower().startswith("basic "):
        import base64

        try:
            decoded = base64.b64decode(header_auth[6:].strip()).decode()
            client_id, _, secret = decoded.partition(":")
            params = {**params, "client_id": client_id, "client_secret": secret}
        except Exception:  # noqa: BLE001
            raise oauth_flow.OAuthError("invalid_request", "Malformed client credentials")

    client = await oauth_flow.resolve_client(db, profile, client_id)
    if client is None:
        raise oauth_flow.OAuthError("invalid_client", "Unknown client_id", status_code=401)

    method = client.token_endpoint_auth_method or "none"
    if method == "none":
        return client

    if method in ("client_secret_post", "client_secret_basic"):
        presented = str(params.get("client_secret") or "")
        if not client.client_secret_hash or not secrets.compare_digest(
            client.client_secret_hash, hashlib.sha256(presented.encode()).hexdigest()
        ):
            raise oauth_flow.OAuthError("invalid_client", "Wrong client secret", status_code=401)
        return client

    if method == "private_key_jwt":
        await _verify_client_assertion(client, params)
        return client

    raise oauth_flow.OAuthError("invalid_client", f"Unsupported auth method '{method}'",
                                status_code=401)


async def _verify_client_assertion(client: McpOAuthClient, params: dict) -> None:
    """Verify a ``private_key_jwt`` client assertion against the client's JWKS.

    OpenAI publishes ChatGPT's JWKS at ``/oauth/jwks.json`` on the metadata
    origin, and a CIMD may also carry ``jwks``/``jwks_uri`` inline. Anything we
    fetch is host-allowlisted by the same rule as the metadata document itself.
    """
    assertion = str(params.get("client_assertion") or "")
    assertion_type = str(params.get("client_assertion_type") or "")
    if assertion_type != "urn:ietf:params:oauth:client-assertion-type:jwt-bearer":
        raise oauth_flow.OAuthError("invalid_client", "Missing or wrong client_assertion_type",
                                    status_code=401)
    if not assertion or not client.cimd_url:
        raise oauth_flow.OAuthError("invalid_client", "Missing client_assertion", status_code=401)

    document = await oauth_flow._fetch_cimd(client.cimd_url)
    keys = document.get("jwks") or {}
    if isinstance(keys, dict):
        keys = keys.get("keys") or []
    if not keys and document.get("jwks_uri"):
        keys = (await oauth_flow._fetch_cimd(str(document["jwks_uri"]))).get("keys") or []
    if not keys:
        # Fall back to OpenAI's documented location on the metadata origin.
        from urllib.parse import urlsplit

        origin = urlsplit(client.cimd_url)
        keys = (await oauth_flow._fetch_cimd(
            f"{origin.scheme}://{origin.netloc}/oauth/jwks.json"
        )).get("keys") or []

    from jose import jwk as jose_jwk
    from jose import jwt as jose_jwt

    header = jose_jwt.get_unverified_header(assertion)
    key = next((k for k in keys if k.get("kid") in (None, header.get("kid"))), None)
    if key is None:
        raise oauth_flow.OAuthError("invalid_client", "No matching key in the client JWKS",
                                    status_code=401)
    try:
        constructed = jose_jwk.construct(key, algorithm=str(header.get("alg") or "RS256"))
        # The audience is this server's token endpoint; a client that signs for a
        # different one is replaying an assertion meant for another service.
        claims = jose_jwt.decode(
            assertion,
            constructed.public_key_to_pem(),
            algorithms=[str(header.get("alg") or "RS256")],
            options={"verify_aud": False, "verify_exp": True},
        )
    except Exception as exc:  # noqa: BLE001
        raise oauth_flow.OAuthError("invalid_client", f"Client assertion rejected: {exc}",
                                    status_code=401) from exc
    if str(claims.get("iss") or "") != client.client_id and str(claims.get("sub") or "") \
            != client.client_id:
        raise oauth_flow.OAuthError("invalid_client", "Assertion issuer does not match client_id",
                                    status_code=401)


async def _token(request: Request, profile: C.ConnectorProfile, db: AsyncSession) -> Response:
    if _rate_limited("token", request):
        return JSONResponse(status_code=429,
                            content={"error": "slow_down",
                                     "error_description": "Too many token requests"})
    params = await _oauth_body(request)
    grant = str(params.get("grant_type") or "")
    resource = str(params.get("resource") or "").strip() or profile.resource

    try:
        if grant == "authorization_code":
            return await _token_from_code(request, profile, db, params, resource)
        if grant == "refresh_token":
            return await _token_from_refresh(request, profile, db, params, resource)
        raise oauth_flow.OAuthError(
            "unsupported_grant_type",
            f"grant_type '{grant or '(missing)'}' is not supported. Use authorization_code or "
            "refresh_token.",
        )
    except oauth_flow.OAuthError as exc:
        await db.rollback()
        return JSONResponse(status_code=exc.status_code, content=exc.body())
    except Exception as exc:  # noqa: BLE001 — a bug must not leak a stack trace to the client
        await db.rollback()
        logger.exception("MCP OAuth token endpoint failed")
        return JSONResponse(status_code=500,
                            content={"error": "server_error", "error_description": str(exc)[:200]})


async def _token_from_code(request: Request, profile: C.ConnectorProfile, db: AsyncSession,
                           params: dict, resource: str) -> Response:
    from app.models.mcp_oauth import McpOAuthCode

    code = str(params.get("code") or "")
    if not code:
        raise oauth_flow.OAuthError("invalid_request", "code is required")
    digest = oauth_flow._hash(code)
    row = (
        await db.execute(select(McpOAuthCode).where(McpOAuthCode.code_hash == digest))
    ).scalar_one_or_none()
    if row is None:
        raise oauth_flow.OAuthError("invalid_grant", "Unknown authorization code")
    if row.used_at is not None:
        # A replayed code: revoke everything it produced, per OAuth 2.1.
        await oauth_flow.revoke_client(db, row.client_id)
        await db.delete(row)
        await db.commit()
        raise oauth_flow.OAuthError("invalid_grant", "This authorization code was already used")
    if row.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        await db.delete(row)
        await db.commit()
        raise oauth_flow.OAuthError("invalid_grant", "The authorization code expired")

    client = await _authenticate_client(db, profile, request, {**params, "client_id":
                                                              params.get("client_id")
                                                              or row.client_id})
    if client.client_id != row.client_id:
        raise oauth_flow.OAuthError("invalid_grant", "client_id does not match the code")
    if str(params.get("redirect_uri") or "") != row.redirect_uri:
        raise oauth_flow.OAuthError("invalid_grant", "redirect_uri does not match the code")
    if not oauth_flow.verify_pkce(str(params.get("code_verifier") or ""), row.code_challenge,
                                  row.code_challenge_method):
        raise oauth_flow.OAuthError("invalid_grant", "PKCE verification failed (code_verifier)")
    if row.resource and resource.rstrip("/") != row.resource.rstrip("/"):
        raise oauth_flow.OAuthError("invalid_target",
                                    "resource does not match the one the code was issued for")

    row.used_at = datetime.now(timezone.utc)
    granted = await oauth_flow.grant_tokens(
        db,
        admin_id=row.admin_id or 0,
        client_id=row.client_id,
        scope=row.scope,
        resource=row.resource or resource,
        connector=row.connector or profile.key,
    )
    await db.commit()
    logger.info("MCP OAuth: %s authorized %s (%s)", profile.key, row.client_id[:40], row.scope)
    return JSONResponse(granted.as_response(), headers={"Cache-Control": "no-store"})


async def _token_from_refresh(request: Request, profile: C.ConnectorProfile, db: AsyncSession,
                              params: dict, resource: str) -> Response:
    presented = str(params.get("refresh_token") or "")
    if not presented:
        raise oauth_flow.OAuthError("invalid_request", "refresh_token is required")
    row = (
        await db.execute(
            select(McpOAuthToken).where(
                McpOAuthToken.kind == "refresh",
                McpOAuthToken.token_hash == oauth_flow._hash(presented),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise oauth_flow.OAuthError("invalid_grant", "Unknown refresh token")
    if row.revoked_at is not None:
        # Rotation makes a reused refresh token a replay: cut the client off.
        await oauth_flow.revoke_client(db, row.client_id)
        await db.commit()
        raise oauth_flow.OAuthError("invalid_grant",
                                    "This refresh token was already rotated; the connection was "
                                    "revoked. Re-authorize the connector.")
    if row.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise oauth_flow.OAuthError("invalid_grant", "The refresh token expired")

    client = await _authenticate_client(db, profile, request,
                                        {**params, "client_id": params.get("client_id")
                                                  or row.client_id})
    if client.client_id != row.client_id:
        raise oauth_flow.OAuthError("invalid_grant", "client_id does not match the refresh token")

    scope = oauth_flow.normalize_scope(params.get("scope"), maximum=row.scope) or row.scope
    row.revoked_at = datetime.now(timezone.utc)
    granted = await oauth_flow.grant_tokens(
        db,
        admin_id=row.admin_id or 0,
        client_id=row.client_id,
        scope=scope,
        resource=row.resource or resource,
        connector=row.connector or profile.key,
    )
    await db.commit()
    return JSONResponse(granted.as_response(), headers={"Cache-Control": "no-store"})


@public_router.post("/oauth/token")
@public_router.post("/connectors/{key}/oauth/token")
async def token_endpoint(request: Request, key: str = "generic",
                         db: AsyncSession = Depends(get_db)) -> Response:
    """The token endpoint. Accepts form-encoded *and* JSON bodies — Claude uses
    the first, other clients the second, and the spec lets a server support both.
    """
    return await _token(request, C.get(key), db)


@public_router.post("/oauth/revoke")
@public_router.post("/connectors/{key}/oauth/revoke")
async def revoke_endpoint(request: Request, key: str = "generic",
                          db: AsyncSession = Depends(get_db)) -> Response:
    """RFC 7009 revocation. Always 200, even for an unknown token."""
    if _rate_limited("revoke", request):
        return JSONResponse(status_code=429, content={"error": "slow_down"})
    params = await _oauth_body(request)
    token = str(params.get("token") or "")
    if token:
        await oauth_flow.revoke_by_token(db, token)
        await db.commit()
    return JSONResponse({"revoked": bool(token)})


# ---------------------------------------------------------------------------
# Management (login session) — tokens, connectors, diagnostics
# ---------------------------------------------------------------------------


def _token_out(token: McpToken) -> dict:
    return {
        "id": token.id,
        "name": token.name,
        "prefix": token.prefix,
        "scope": token.scope,
        "is_active": bool(token.is_active),
        "call_count": token.call_count or 0,
        "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
        "last_error": token.last_error,
        "created_at": token.created_at.isoformat() if token.created_at else None,
    }


@router.get("/tokens")
async def list_tokens(db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    """The AI tokens that can operate this app. Plaintext is never stored."""
    rows = (await db.execute(select(McpToken).order_by(McpToken.id.desc()))).scalars().all()
    return {"items": [_token_out(t) for t in rows]}


@router.post("/tokens", status_code=201)
async def create_token(data: dict, db: AsyncSession = Depends(get_db),
                       cu: User = Depends(get_current_user)):
    """Create a static token. The value is returned ONCE — it cannot be read back."""
    name = str((data or {}).get("name") or "").strip() or "AI assistant"
    scope = str((data or {}).get("scope") or "write").lower()
    if scope not in ("read", "write"):
        raise HTTPException(422, "scope must be 'read' or 'write'")
    connector = str((data or {}).get("connector") or "generic").lower()
    if connector not in C.BY_KEY:
        connector = "generic"

    raw, hashed, prefix = mcp_server.create_token_value()
    token = McpToken(name=f"{name}"[:120], token_hash=hashed, prefix=prefix, scope=scope,
                     is_active=True)
    db.add(token)
    await db.commit()
    await db.refresh(token)
    return {
        "id": token.id,
        "name": token.name,
        "scope": token.scope,
        "prefix": token.prefix,
        "connector": connector,
        # Shown once, on creation only.
        "token": raw,
    }


@router.delete("/tokens/{token_id}", status_code=204)
async def revoke_token(token_id: int, db: AsyncSession = Depends(get_db),
                       cu: User = Depends(get_current_user)):
    """Revoke a token: whatever is using it stops working immediately."""
    token = (
        await db.execute(select(McpToken).where(McpToken.id == token_id))
    ).scalar_one_or_none()
    if token is None:
        raise HTTPException(404, "Token not found")
    token.is_active = False
    await db.commit()


@router.get("/activity")
async def activity(limit: int = 50, db: AsyncSession = Depends(get_db),
                   cu: User = Depends(get_current_user)):
    """What the assistant actually did — every tool call, newest first."""
    rows = (
        await db.execute(select(McpCall).order_by(McpCall.id.desc()).limit(min(limit, 200)))
    ).scalars().all()
    total = (await db.execute(select(func.count()).select_from(McpCall))).scalar() or 0
    return {
        "total": total,
        "items": [
            {
                "id": c.id,
                "tool": c.tool,
                "token_name": c.token_name,
                "request": c.request,
                "ok": bool(c.ok),
                "status_code": c.status_code,
                "error": c.error,
                "arguments": c.arguments,
                "duration_ms": c.duration_ms,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in rows
        ],
    }


@router.get("/connectors")
async def list_connectors(db: AsyncSession = Depends(get_db),
                          cu: User = Depends(get_current_user)):
    """One card per AI client: its endpoint, its auth, and whether it works.

    ``ready`` means "an absolute base URL is known", which — since the request's
    own host is used when PUBLIC_BASE_URL is unset — is true for any request
    that arrived on a real host. ``base_url_source`` says where it came from, so
    the screen can distinguish "configured" from "detected from this browser
    session" and tell the operator which one to keep.
    """
    items = []
    for profile in C.CONNECTORS:
        connection = await oauth_flow.get_connection(db, profile.key)
        items.append(oauth_flow.connection_out(connection, profile))
    await db.commit()
    base = public_base_url()
    source = public_base_url_source()
    return {
        "items": items,
        "public_base_url": base,
        "base_url_source": source,
        "ready": bool(base),
        "problem": None if base else (
            "This deployment's public address could not be determined, so every URL in the OAuth "
            "metadata would be relative and no client could complete a connection. Set "
            "PUBLIC_BASE_URL to this deployment's public https address."
        ),
        "note": (
            "Detected from this browser session. Set PUBLIC_BASE_URL to the deployment's permanent "
            "https address so the connector also works for clients that reach it on another host."
            if source == "request" else None
        ),
        "require_login": bool(settings.MCP_OAUTH_REQUIRE_LOGIN),
        "protocol_versions": list(C.PROTOCOL_VERSIONS),
        "bridge": _bridge_info(base),
    }


def _bridge_info(base: str | None) -> dict:
    """The paste-in fallback for hosts with no connector form (Arena, Cursor…).

    A stdio bridge and a bearer token work everywhere, including a sandbox with
    only bash — which is the one path that never depends on OAuth discovery,
    redirect URIs or a public URL being configured correctly.
    """
    sample_endpoint = f"{base}/connectors/arena/mcp" if base else "/connectors/arena/mcp"
    return {
        "available": os.path.isfile(BRIDGE_PATH),
        "download_url": "/api/v1/mcp/bridge",
        "script": "arena_mcp_bridge.py",
        "endpoint": sample_endpoint,
        "run": (
            f'SENDERSMS_MCP_URL="{sample_endpoint}" '
            'SENDERSMS_MCP_TOKEN="<token from Access tokens>" '
            "python3 arena_mcp_bridge.py"
        ),
        "config": {
            "mcpServers": {
                "sendersms": {
                    "command": "python3",
                    "args": ["arena_mcp_bridge.py"],
                    "env": {
                        "SENDERSMS_MCP_URL": sample_endpoint,
                        "SENDERSMS_MCP_TOKEN": "<token from Access tokens>",
                    },
                }
            }
        },
    }


@router.get("/bridge", response_class=PlainTextResponse)
async def download_bridge(cu: User = Depends(get_current_user)) -> PlainTextResponse:
    """Serve the stdio bridge so it can be fetched straight from the app.

    Dashboard-only (unlike the MCP endpoints themselves): the script contains no
    secret, but it is not something to publish to the open internet either.
    """
    if not os.path.isfile(BRIDGE_PATH):
        raise HTTPException(404, "The stdio bridge is not bundled with this build.")
    with open(BRIDGE_PATH, "r", encoding="utf-8") as handle:
        body = handle.read()
    return PlainTextResponse(
        body,
        headers={
            "Content-Disposition": 'attachment; filename="arena_mcp_bridge.py"',
            "Cache-Control": "no-store",
        },
    )



@router.post("/connectors/{key}")
async def update_connector(key: str, data: dict, db: AsyncSession = Depends(get_db),
                           cu: User = Depends(get_current_user)):
    """Switches per connector: OAuth on/off, the scope ceiling, the tool set."""
    profile = C.get(key)
    connection = await oauth_flow.get_connection(db, profile.key)
    payload = data or {}
    if "oauth_enabled" in payload:
        connection.oauth_enabled = bool(payload["oauth_enabled"])
    if "default_scope" in payload:
        scope = str(payload["default_scope"]).lower()
        connection.default_scope = scope if scope in C.SCOPES else "write"
    if "toolset" in payload:
        toolset = str(payload["toolset"]).lower()
        connection.toolset = toolset if toolset in ("core", "full") else profile.toolset
    await db.commit()
    return oauth_flow.connection_out(connection, profile)


@router.get("/clients")
async def list_clients(db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    """The assistants that have registered as OAuth clients."""
    rows = (
        await db.execute(select(McpOAuthClient).order_by(McpOAuthClient.id.desc()).limit(100))
    ).scalars().all()
    grants = (
        await db.execute(
            select(McpOAuthToken.client_id, func.count())
            .where(McpOAuthToken.kind == "refresh", McpOAuthToken.revoked_at.is_(None))
            .group_by(McpOAuthToken.client_id)
        )
    ).all()
    live = {client_id: count for client_id, count in grants}
    return {
        "items": [
            {
                "id": c.id,
                "client_id": c.client_id,
                "client_name": c.client_name,
                "connector": c.connector,
                "source": c.source,
                "redirect_uris": oauth_flow.client_redirect_uris(c),
                "auth_method": c.token_endpoint_auth_method,
                "scope": c.scope,
                "is_active": bool(c.is_active),
                "active_grants": live.get(c.client_id, 0),
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None,
            }
            for c in rows
        ],
    }


@router.delete("/clients/{client_pk}", status_code=204)
async def remove_client(client_pk: int, db: AsyncSession = Depends(get_db),
                        cu: User = Depends(get_current_user)):
    """Cut a registered client off: its tokens are revoked and it must re-register."""
    client = (
        await db.execute(select(McpOAuthClient).where(McpOAuthClient.id == client_pk))
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(404, "Unknown client")
    await oauth_flow.revoke_client(db, client.client_id)
    client.is_active = False
    await db.commit()


@router.post("/connectors/{key}/revoke")
async def revoke_connector(key: str, db: AsyncSession = Depends(get_db),
                           cu: User = Depends(get_current_user)):
    """Revoke every OAuth grant made through one connector."""
    profile = C.get(key)
    rows = (
        await db.execute(
            select(McpOAuthToken).where(
                McpOAuthToken.connector == profile.key, McpOAuthToken.revoked_at.is_(None)
            )
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for row in rows:
        row.revoked_at = now
    connection = await oauth_flow.get_connection(db, profile.key)
    connection.status = "unknown"
    await db.commit()
    return {"revoked": len(rows)}


@router.post("/connectors/{key}/test")
async def test_connector(key: str, db: AsyncSession = Depends(get_db),
                         cu: User = Depends(get_current_user)):
    """Run the handshake a real client runs, against this deployment, and report.

    This is the answer to "the connector is not working": it performs the exact
    sequence ChatGPT and Claude perform — discovery, registration, authorize,
    token, initialize, tools/list, tools/call — in-process, and records what each
    step returned. It also checks the two things clients reject us for without
    saying why: a 401 that carries no ``WWW-Authenticate`` challenge, and
    metadata whose ``resource`` does not match the endpoint.
    """
    from app.mcp.diagnostics import run_connector_test

    profile = C.get(key)
    report = await run_connector_test(profile, db)
    connection = await oauth_flow.get_connection(db, profile.key)
    connection.last_test_at = datetime.now(timezone.utc)
    connection.last_test_report = json.dumps(report["steps"])[:12000]
    connection.status = report["status"]
    connection.last_error = report["error"]
    await db.commit()
    return report


@router.get("/endpoints")
async def connector_endpoints(cu: User = Depends(get_current_user)):
    """The absolute URLs to paste into each client, and the config blocks."""
    base = (public_base_url() or "").rstrip("/")
    return {
        "public_base_url": base or None,
        "items": [
            {
                "key": p.key,
                "label": p.label,
                "endpoint": p.resource,
                "protected_resource_metadata": p.well_known_prm(),
                "authorization_server_metadata": f"{base}/.well-known/oauth-authorization-server",
                "register": f"{base}{p.oauth_path}/register",
                "authorize": f"{base}{p.oauth_path}/authorize",
                "token": f"{base}{p.oauth_path}/token",
                "auth_modes": list(p.auth_modes),
            }
            for p in C.CONNECTORS
        ],
    }
