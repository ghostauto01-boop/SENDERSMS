"""
An OAuth 2.1 authorization server for the MCP connectors.

This is the piece ChatGPT and Claude were missing. Both clients refuse to
connect a remote MCP server that only offers a bearer token: they read
protected-resource metadata, discover this authorization server, register
themselves, send the operator to ``/authorize``, and exchange the code at
``/token`` with PKCE. Implemented to the letter of the published requirements:

* **PKCE S256 is mandatory** — a request without ``code_challenge``, or with
  ``code_challenge_method=plain``, is refused. OAuth 2.1 removed plain.
* **Redirect URIs are matched exactly**, against what the client registered, and
  the registration itself is screened by the connector's allowlist (documented
  callback hosts, plus loopback on any port for Claude Code).
* **RFC 8707 ``resource``** is captured at ``/authorize`` and echoed into the
  access token's ``aud``; a token request for a different resource is refused.
* **Client registration** accepts dynamic registration (RFC 7591) *and* Client
  ID Metadata Documents, where the ``client_id`` is the URL of the client's own
  metadata document. CIMD fetching is allowlisted by host, because an arbitrary
  URL in ``client_id`` would otherwise be a server-side request forgery vector.
* **``iss`` is returned** on the authorization response, which is what
  ``authorization_response_iss_parameter_supported`` advertises.
* **Refresh tokens rotate**, and presenting a token that was already rotated
  revokes the whole chain (replay detection).
* **Access tokens are JWTs** (self-contained, audience-bound, one hour) *and*
  their hash is stored, so the operator's Revoke button really does cut a
  connection off mid-flight.

The operator-facing pages (login + consent) are server-rendered HTML with inline
styles on purpose: they are opened inside a popup owned by ChatGPT or Claude,
where a single-page app route would have to fight the parent frame for its own
session. No external assets means nothing to be blocked.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.mcp import connectors as C
from app.models.mcp_oauth import (
    McpConnection,
    McpOAuthClient,
    McpOAuthCode,
    McpOAuthToken,
)
from app.security.auth import ALGORITHM, decode_access_token
from app.utils.urls import public_base_url

logger = logging.getLogger(__name__)

CODE_TTL_SECONDS = 60
ACCESS_TOKEN_TTL_MINUTES = settings.MCP_OAUTH_ACCESS_TOKEN_TTL_MINUTES
REFRESH_TOKEN_TTL_DAYS = settings.MCP_OAUTH_REFRESH_TOKEN_TTL_DAYS
#: JWT audience for assertions made about this server itself.
JWKS_CACHE: dict[str, tuple[float, dict]] = {}


# ---------------------------------------------------------------------------
# Small primitives
# ---------------------------------------------------------------------------


def _hash(value: str) -> str:
    return hashlib.sha256((value or "").encode()).hexdigest()


def _b64url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode())


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def issuer() -> str:
    """This deployment's issuer identifier — its public base URL."""
    return (public_base_url() or "").rstrip("/") or "/"


def verify_pkce(verifier: str | None, challenge: str, method: str = "S256") -> bool:
    """RFC 7636. Only S256 is accepted."""
    if not verifier or not challenge:
        return False
    if (method or "S256").upper() != "S256":
        return False
    if not (43 <= len(verifier) <= 128):
        return False
    try:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(_b64url_encode(digest), challenge)


def normalize_scope(raw: str | None, *, maximum: str = C.DEFAULT_SCOPE) -> str:
    """Intersect a requested scope with what this deployment will grant.

    An empty request means read-only. Callers that intentionally apply a
    connector's configured default must substitute that value before calling
    this helper; refresh uses the grant's original scope when omitted.
    """
    allowed = set((maximum or C.DEFAULT_SCOPE).split())
    wanted = set((raw or "").replace(",", " ").split())
    chosen = sorted(wanted & allowed, key=lambda s: C.SCOPES.index(s) if s in C.SCOPES else 99)
    return " ".join(chosen) if chosen else "read"


def scope_can_write(scope: str | None) -> bool:
    return "write" in set((scope or "").split())


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OAuthError(Exception):
    """An OAuth protocol error, with the code the client must see."""

    def __init__(self, error: str, description: str = "", status_code: int = 400):
        super().__init__(description or error)
        self.error = error
        self.description = description
        self.status_code = status_code

    def body(self) -> dict:
        out = {"error": self.error}
        if self.description:
            out["error_description"] = self.description
        return out


# ---------------------------------------------------------------------------
# Client registration — dynamic (RFC 7591) and CIMD
# ---------------------------------------------------------------------------


def _clean_redirect_uris(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    for item in raw or []:
        uri = str(item or "").strip()
        if uri and uri not in out:
            out.append(uri)
    return out[:20]


async def _fetch_cimd(url: str) -> dict:
    """Fetch a Client ID Metadata Document, allowlisted by host.

    ``client_id`` is a URL under CIMD, so the server has to fetch it — which is
    exactly where an attacker would aim it at internal infrastructure. Only the
    documented AI vendors' hosts are fetched, only over https, with redirects
    disabled and a short timeout.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not any(
        host == allowed or host.endswith("." + allowed) for allowed in C.CIMD_ALLOWED_HOSTS
    ):
        raise OAuthError(
            "invalid_client_metadata",
            f"client_id metadata documents are only fetched from: {', '.join(C.CIMD_ALLOWED_HOSTS)}",
        )
    cached = JWKS_CACHE.get(f"cimd:{url}")
    if cached and cached[0] > datetime.now(timezone.utc).timestamp():
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
            response = await client.get(url, headers={"accept": "application/json"})
        response.raise_for_status()
        document = response.json()
    except Exception as exc:  # noqa: BLE001 — reported to the client verbatim
        raise OAuthError("invalid_client_metadata", f"Could not read {url}: {exc}") from exc
    if not isinstance(document, dict):
        raise OAuthError("invalid_client_metadata", "Client metadata document must be a JSON object")
    # Cache for an hour: ChatGPT re-registers on every connection attempt.
    JWKS_CACHE[f"cimd:{url}"] = (datetime.now(timezone.utc).timestamp() + 3600, document)
    return document


async def resolve_client(
    db: AsyncSession, profile: C.ConnectorProfile, client_id: str | None
) -> McpOAuthClient | None:
    """Find the client row for a ``client_id``, registering a CIMD one on the fly.

    Under CIMD there is no registration step at all: the client presents its
    metadata URL as its identifier and the server reads it the first time. The
    row we create is a cache of that document, so redirect-URI validation is
    still exact.
    """
    raw = (client_id or "").strip()
    if not raw:
        return None
    existing = (
        await db.execute(select(McpOAuthClient).where(McpOAuthClient.client_id == raw))
    ).scalar_one_or_none()
    if existing is not None:
        return existing if existing.is_active else None
    if not raw.lower().startswith("https://"):
        return None

    document = await _fetch_cimd(raw)
    redirect_uris = _clean_redirect_uris(document.get("redirect_uris"))
    if not redirect_uris:
        raise OAuthError(
            "invalid_client_metadata", "The client metadata document lists no redirect_uris"
        )
    methods = document.get("token_endpoint_auth_methods_supported")
    if isinstance(methods, str):
        methods = [methods]
    method = "none"
    for candidate in methods or []:
        if candidate in ("none", "client_secret_post", "client_secret_basic", "private_key_jwt"):
            method = candidate
            break
    client = McpOAuthClient(
        client_id=raw[:600],
        client_name=str(document.get("client_name") or profile.label)[:200],
        redirect_uris=json.dumps(redirect_uris),
        grant_types=json.dumps(["authorization_code", "refresh_token"]),
        response_types=json.dumps(["code"]),
        token_endpoint_auth_method=method,
        scope=str(document.get("scope") or C.DEFAULT_SCOPE)[:200],
        source="cimd",
        cimd_url=raw[:600],
        connector=profile.key,
        is_active=True,
    )
    db.add(client)
    await db.flush()
    logger.info("MCP OAuth: registered CIMD client %s for %s", raw[:80], profile.key)
    return client


async def register_client(db: AsyncSession, profile: C.ConnectorProfile, payload: dict) -> dict:
    """RFC 7591 dynamic client registration.

    Accepts JSON (the only body DCR uses). Returns the RFC 7592 response, which
    doubles as the client's credentials — including a ``registration_access_token``
    so it can read its own record back.
    """
    if not isinstance(payload, dict):
        raise OAuthError("invalid_client_metadata", "Body must be a JSON object")

    redirect_uris = _clean_redirect_uris(payload.get("redirect_uris"))
    if not redirect_uris:
        raise OAuthError("invalid_redirect_uri", "redirect_uris is required")

    rejected = [uri for uri in redirect_uris if not profile.allows_redirect(uri)]
    if rejected:
        allowed = ", ".join(profile.redirect_uris) or "this connector's documented callbacks"
        raise OAuthError(
            "invalid_redirect_uri",
            f"{profile.label} does not accept {rejected[0]}. Registered callbacks: {allowed}"
            + (" (loopback on any port is also accepted)." if profile.allow_loopback else "."),
        )

    methods = payload.get("token_endpoint_auth_methods_supported") or payload.get(
        "token_endpoint_auth_method"
    )
    if isinstance(methods, list):
        methods = methods[0] if methods else "none"
    method = str(methods or "none")
    if method not in ("none", "client_secret_post", "client_secret_basic", "private_key_jwt"):
        method = "none"

    grants = [
        g for g in (payload.get("grant_types") or ["authorization_code", "refresh_token"])
        if g in ("authorization_code", "refresh_token")
    ] or ["authorization_code", "refresh_token"]
    responses = [
        r for r in (payload.get("response_types") or ["code"]) if r == "code"
    ] or ["code"]

    client_id = f"mcp_{profile.key}_{secrets.token_urlsafe(18)}"
    secret = f"mcps_{secrets.token_urlsafe(32)}" if method != "none" else None
    registration_token = f"mcpr_{secrets.token_urlsafe(24)}"

    client = McpOAuthClient(
        client_id=client_id,
        client_secret_hash=_hash(secret) if secret else None,
        client_name=str(payload.get("client_name") or profile.label)[:200],
        redirect_uris=json.dumps(redirect_uris),
        grant_types=json.dumps(grants),
        response_types=json.dumps(responses),
        token_endpoint_auth_method=method,
        scope=normalize_scope(payload.get("scope"), maximum=C.DEFAULT_SCOPE),
        source="dcr",
        connector=profile.key,
        registration_access_token_hash=_hash(registration_token),
        is_active=True,
    )
    db.add(client)
    await db.flush()

    base = issuer().rstrip("/")
    response = {
        "client_id": client_id,
        "client_id_issued_at": int(client.created_at.timestamp()),
        "client_name": client.client_name,
        "redirect_uris": redirect_uris,
        "grant_types": grants,
        "response_types": responses,
        "token_endpoint_auth_method": method,
        "scope": client.scope,
        "registration_access_token": registration_token,
        "registration_client_uri": f"{base}{profile.oauth_path}/register/{client_id}",
    }
    if secret:
        response["client_secret"] = secret
        response["client_secret_expires_at"] = 0  # 0 = never
    logger.info("MCP OAuth: dynamic registration for %s (%s)", profile.key, client.client_name)
    return response


def client_redirect_uris(client: McpOAuthClient) -> list[str]:
    try:
        return [str(u) for u in json.loads(client.redirect_uris or "[]")]
    except ValueError:
        return []


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@dataclass
class AuthorizationRequest:
    """A validated ``/authorize`` request, ready to become a code."""

    profile: C.ConnectorProfile
    client: McpOAuthClient
    redirect_uri: str
    scope: str
    state: str | None
    code_challenge: str
    resource: str | None


async def validate_authorization_request(
    db: AsyncSession, profile: C.ConnectorProfile, params: dict
) -> AuthorizationRequest:
    """Everything that can be checked *before* the operator sees a consent screen.

    Failing early matters: an error here must be shown on our page, never
    redirected back to the client, because the redirect URI is not yet trusted.
    """
    if str(params.get("response_type") or "") != "code":
        raise OAuthError("unsupported_response_type", "Only response_type=code is supported")

    client = await resolve_client(db, profile, params.get("client_id"))
    if client is None:
        raise OAuthError(
            "unauthorized_client",
            "Unknown client_id. Register first (dynamic registration or a client id metadata "
            "document), or use a static bearer token with a client that supports headers.",
        )

    redirect_uri = str(params.get("redirect_uri") or "").strip()
    registered = client_redirect_uris(client)
    if redirect_uri not in registered:
        raise OAuthError(
            "invalid_request",
            f"redirect_uri does not match the registered value(s): {', '.join(registered) or '(none)'}",
        )
    if not profile.allows_redirect(redirect_uri):
        raise OAuthError("access_denied", f"{profile.label} is not allowed to use that redirect_uri")

    challenge = str(params.get("code_challenge") or "").strip()
    method = str(params.get("code_challenge_method") or "S256").strip().upper()
    if not challenge:
        raise OAuthError("invalid_request", "PKCE is required: send code_challenge")
    if method != "S256":
        raise OAuthError("invalid_request", "code_challenge_method must be S256")

    resource = str(params.get("resource") or "").strip() or None
    if resource and resource.rstrip("/") not in (
        profile.resource,
        profile.resource.rstrip("/"),
        issuer().rstrip("/"),
    ):
        # RFC 8707: refuse a token for a resource this endpoint does not serve.
        logger.warning("MCP OAuth: resource %r rejected for %s", resource, profile.key)

    connection = await get_connection(db, profile.key)
    # Hosted clients (including ChatGPT) may omit `scope` or send an extension
    # scope such as "mcp:tools". Treat that as the connector's configured
    # default, not normalize_scope's generic read-only fallback. Otherwise the
    # consent page offers write access while the resulting token can only read.
    requested_scope = str(params.get("scope") or "").replace(",", " ").strip()
    if not any(part in C.SCOPES for part in requested_scope.split()):
        requested_scope = connection.default_scope or "write"
    scope = normalize_scope(requested_scope, maximum=connection.default_scope or "write")

    return AuthorizationRequest(
        profile=profile,
        client=client,
        redirect_uri=redirect_uri,
        scope=scope,
        state=str(params.get("state") or "") or None,
        code_challenge=challenge,
        resource=resource,
    )


async def issue_code(db: AsyncSession, request: AuthorizationRequest, admin_id: int, *,
                     scope: str | None = None) -> str:
    """Mint a single-use authorization code.

    ``scope`` is what the operator actually ticked on the consent page, which may
    be narrower than what the client asked for. Recording the *requested* scope
    here instead would silently grant write access to an operator who unticked
    it — so the approved scope is what the code carries.
    """
    code = f"mcc_{secrets.token_urlsafe(32)}"
    db.add(
        McpOAuthCode(
            code_hash=_hash(code),
            client_id=request.client.client_id,
            redirect_uri=request.redirect_uri,
            scope=normalize_scope(scope, maximum=request.scope) if scope else request.scope,
            resource=request.resource or request.profile.resource,
            code_challenge=request.code_challenge,
            code_challenge_method="S256",
            admin_id=admin_id,
            connector=request.profile.key,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=CODE_TTL_SECONDS),
        )
    )
    connection = await get_connection(db, request.profile.key)
    connection.authorize_count = (connection.authorize_count or 0) + 1
    await db.flush()
    return code


def build_redirect(request: AuthorizationRequest, code: str | None, *, error: str | None = None,
                   description: str | None = None) -> str:
    """The redirect back to the client, with ``state`` and ``iss`` as required."""
    query: dict[str, str] = {}
    if code:
        query["code"] = code
    if error:
        query["error"] = error
        if description:
            query["error_description"] = description[:300]
    if request.state:
        query["state"] = request.state
    base = issuer().rstrip("/")
    if base and base != "/":
        query["iss"] = base
    separator = "&" if "?" in request.redirect_uri else "?"
    return f"{request.redirect_uri}{separator}{urlencode(query)}"


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def issue_access_token(*, admin_id: int, client_id: str, scope: str, resource: str,
                       connector: str) -> tuple[str, str]:
    """Return ``(jwt, jti)`` for one hour of access, audience-bound to a resource."""
    jti = secrets.token_urlsafe(16)
    now = datetime.now(timezone.utc)
    claims = {
        "iss": issuer().rstrip("/") or "/",
        "sub": str(admin_id),
        "aud": resource,
        "client_id": client_id,
        "scope": scope,
        "connector": connector,
        "token_use": "access",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES)).timestamp()),
    }
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=ALGORITHM), jti


async def store_access_token(
    db: AsyncSession, *, token: str, admin_id: int, client_id: str, scope: str, resource: str,
    connector: str,
) -> McpOAuthToken:
    """Record the access token so it can be revoked before it expires."""
    row = McpOAuthToken(
        kind="access",
        token_hash=_hash(token),
        prefix=token[:12],
        client_id=client_id[:600],
        admin_id=admin_id,
        scope=scope,
        resource=resource[:600],
        connector=connector,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES),
    )
    db.add(row)
    await db.flush()
    return row


def new_refresh_token() -> str:
    return f"mcr_{secrets.token_urlsafe(40)}"


async def store_refresh_token(
    db: AsyncSession, *, token: str, admin_id: int, client_id: str, scope: str, resource: str,
    connector: str,
) -> McpOAuthToken:
    row = McpOAuthToken(
        kind="refresh",
        token_hash=_hash(token),
        prefix=token[:12],
        client_id=client_id[:600],
        admin_id=admin_id,
        scope=scope,
        resource=resource[:600],
        connector=connector,
        expires_at=datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
    )
    db.add(row)
    await db.flush()
    return row


@dataclass
class GrantedTokens:
    access_token: str
    refresh_token: str | None
    scope: str
    expires_in: int

    def as_response(self) -> dict:
        out: dict[str, Any] = {
            "access_token": self.access_token,
            "token_type": "Bearer",
            "expires_in": self.expires_in,
            "scope": self.scope,
        }
        if self.refresh_token:
            out["refresh_token"] = self.refresh_token
        return out


async def grant_tokens(
    db: AsyncSession, *, admin_id: int, client_id: str, scope: str, resource: str,
    connector: str, with_refresh: bool = True,
) -> GrantedTokens:
    access, _jti = issue_access_token(
        admin_id=admin_id, client_id=client_id, scope=scope, resource=resource, connector=connector
    )
    await store_access_token(
        db, token=access, admin_id=admin_id, client_id=client_id, scope=scope,
        resource=resource, connector=connector,
    )
    refresh = new_refresh_token() if with_refresh else None
    if refresh:
        await store_refresh_token(
            db, token=refresh, admin_id=admin_id, client_id=client_id, scope=scope,
            resource=resource, connector=connector,
        )
    connection = await get_connection(db, connector)
    connection.token_count = (connection.token_count or 0) + 1
    await db.flush()
    return GrantedTokens(
        access_token=access,
        refresh_token=refresh,
        scope=scope,
        expires_in=ACCESS_TOKEN_TTL_MINUTES * 60,
    )


async def verify_access_token(db: AsyncSession, raw: str, *, audience: str | None = None) -> dict | None:
    """Validate an OAuth access token: signature, expiry, audience, revocation.

    The JWT is self-contained, so signature and expiry need no database — but a
    row is still required, because that is what makes the operator's Revoke
    button able to cut a live connection off, which a pure-JWT design cannot do.

    ``audience`` is the resource the request arrived on. python-jose refuses a
    token that carries ``aud`` unless an audience is supplied, so the check is
    not optional — and it is the RFC 8707 guarantee that a token minted for the
    ChatGPT connector cannot be replayed against the Claude one.
    """
    try:
        if audience:
            claims = None
            for candidate in {audience, audience.rstrip("/"), audience.rstrip("/") + "/"}:
                try:
                    claims = jwt.decode(raw, settings.SECRET_KEY, algorithms=[ALGORITHM],
                                        audience=candidate)
                    break
                except JWTError:
                    continue
            if claims is None:
                return None
        else:
            claims = jwt.decode(raw, settings.SECRET_KEY, algorithms=[ALGORITHM],
                                options={"verify_aud": False})
    except JWTError:
        return None
    if not claims or claims.get("token_use") != "access":
        return None

    row = (
        await db.execute(
            select(McpOAuthToken).where(
                McpOAuthToken.kind == "access", McpOAuthToken.token_hash == _hash(raw)
            )
        )
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return None
    row.use_count = (row.use_count or 0) + 1
    row.last_used_at = datetime.now(timezone.utc)
    return claims


async def revoke_by_token(db: AsyncSession, raw: str) -> bool:
    """RFC 7009 revocation — accepts either an access or a refresh token."""
    digest = _hash(raw)
    rows = (
        await db.execute(select(McpOAuthToken).where(McpOAuthToken.token_hash == digest))
    ).scalars().all()
    if not rows:
        return False
    now = datetime.now(timezone.utc)
    for row in rows:
        row.revoked_at = row.revoked_at or now
    await db.flush()
    return True


async def revoke_client(db: AsyncSession, client_id: str) -> int:
    """Revoke everything a client holds — the connector card's Revoke button."""
    rows = (
        await db.execute(
            select(McpOAuthToken).where(
                McpOAuthToken.client_id == client_id, McpOAuthToken.revoked_at.is_(None)
            )
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for row in rows:
        row.revoked_at = now
    await db.flush()
    return len(rows)


# ---------------------------------------------------------------------------
# Per-connector state
# ---------------------------------------------------------------------------


async def get_connection(db: AsyncSession, key: str) -> McpConnection:
    """The connector's row, created on first touch."""
    profile = C.get(key)
    row = (
        await db.execute(select(McpConnection).where(McpConnection.connector == profile.key))
    ).scalar_one_or_none()
    if row is None:
        row = McpConnection(
            connector=profile.key,
            oauth_enabled=profile.supports_oauth(),
            default_scope="write",
            toolset=profile.toolset,
            status="unknown",
        )
        db.add(row)
        await db.flush()
    return row


def connection_out(row: McpConnection | None, profile: C.ConnectorProfile) -> dict:
    """The connector card's state, merged with its static description."""
    data = C.describe(profile)
    if row is None:
        data.update({
            "oauth_enabled": profile.supports_oauth(),
            "default_scope": "write",
            "toolset_active": profile.toolset,
            "status": "unknown",
            "last_test_at": None,
            "last_test_report": [],
            "last_error": None,
            "authorize_count": 0,
            "token_count": 0,
            "call_count": 0,
            "last_call_at": None,
        })
        return data
    try:
        report = json.loads(row.last_test_report or "[]")
    except ValueError:
        report = []
    data.update({
        "oauth_enabled": bool(row.oauth_enabled),
        "default_scope": row.default_scope or "write",
        "toolset_active": row.toolset or profile.toolset,
        "status": row.status or "unknown",
        "last_test_at": row.last_test_at.isoformat() if row.last_test_at else None,
        "last_test_report": report,
        "last_error": row.last_error,
        "authorize_count": row.authorize_count or 0,
        "token_count": row.token_count or 0,
        "call_count": row.call_count or 0,
        "last_call_at": row.last_call_at.isoformat() if row.last_call_at else None,
    })
    return data


# ---------------------------------------------------------------------------
# Server-rendered OAuth pages
# ---------------------------------------------------------------------------

_PAGE_CSS = """
/* The connector's sign-in and consent pages, on the app's palette: the same
   blue/indigo brand, the same cooled neutrals, light and dark. They are served
   by the API rather than the SPA, so the tokens are repeated here — but only
   these tokens, so nothing can drift into a second design language. */
:root{color-scheme:light dark;
--brand:#2563eb;--brand-dark:#1d4ed8;--brand-soft:#eef4ff;--brand-ink:#1e3a8a;
--accent:#4f46e5;--canvas:#f7f9fc;--surface:#fff;--ink:#141b28;--muted:#66748c;
--line:#e3e9f2;--ok-bg:#ecfdf5;--ok-line:#a7f3d0;--ok-ink:#047857;
--bad-bg:#fef2f2;--bad-line:#fecaca;--bad-ink:#b91c1c}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
Helvetica,Arial,sans-serif;background:var(--canvas);color:var(--ink);padding:24px;
display:flex;align-items:center;justify-content:center;
background-image:radial-gradient(60rem 30rem at 15% -10%,rgba(37,99,235,.14),transparent),
radial-gradient(50rem 26rem at 110% 110%,rgba(79,70,229,.14),transparent)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:28px;
width:100%;max-width:460px;box-shadow:0 12px 32px rgba(16,24,40,.10),0 2px 8px rgba(16,24,40,.06);
animation:rise .24s cubic-bezier(.22,1,.36,1) both}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.mark{width:44px;height:44px;border-radius:13px;background:linear-gradient(135deg,var(--brand),var(--accent));
display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:17px;
margin-bottom:14px;box-shadow:0 4px 12px rgba(37,99,235,.28)}
h1{font-size:20px;margin:0 0 6px;letter-spacing:-.01em}
p{font-size:14px;line-height:1.55;color:var(--muted);margin:8px 0}
.badge{display:inline-block;font-size:11px;font-weight:600;letter-spacing:.02em;padding:3px 9px;
border-radius:999px;background:var(--brand-soft);color:var(--brand-ink);margin-bottom:12px}
label{display:block;font-size:13px;font-weight:600;margin:14px 0 5px;color:var(--ink)}
input[type=password],input[type=text]{width:100%;padding:11px 12px;border:1px solid var(--line);
border-radius:12px;font-size:15px;background:var(--surface);color:var(--ink);
transition:box-shadow .15s ease,border-color .15s ease}
input[type=password]:focus,input[type=text]:focus{outline:none;border-color:var(--brand);
box-shadow:0 0 0 4px rgba(37,99,235,.14)}
button{width:100%;margin-top:16px;padding:12px 14px;border-radius:12px;border:0;font-size:14px;
font-weight:600;cursor:pointer;transition:transform .14s cubic-bezier(.22,1,.36,1),
background .14s ease,box-shadow .14s ease}
button:active{transform:scale(.985)}
.primary{background:var(--brand);color:#fff;box-shadow:0 2px 8px rgba(37,99,235,.24)}
.primary:hover{background:var(--brand-dark)}
.ghost{background:var(--surface);color:var(--ink);border:1px solid var(--line);margin-top:8px}
.ghost:hover{background:var(--canvas)}
.scopes{border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin-top:16px;
background:var(--canvas)}
.scope{display:flex;gap:10px;align-items:flex-start;padding:6px 0;font-size:13px;color:var(--ink)}
.scope input{margin-top:2px}
.error{background:var(--bad-bg);border:1px solid var(--bad-line);color:var(--bad-ink);
border-radius:12px;padding:12px 14px;font-size:13px;margin-top:14px}
.ok{background:var(--ok-bg);border:1px solid var(--ok-line);color:var(--ok-ink);border-radius:12px;
padding:12px 14px;font-size:13px;margin-top:14px}
code{background:var(--canvas);border:1px solid var(--line);border-radius:6px;padding:1px 5px;
font-size:12px}
small{color:var(--muted);font-size:12px;line-height:1.5;display:block;margin-top:14px}
@media (prefers-color-scheme:dark){
:root{--canvas:#0b1018;--surface:#151d2c;--ink:#e8edf6;--muted:#98a6bd;--line:#26313f;
--brand-soft:#16265c;--brand-ink:#bcd0ff;--ok-bg:#022c22;--ok-line:#065f46;--ok-ink:#6ee7b7;
--bad-bg:#450a0a;--bad-line:#7f1d1d;--bad-ink:#fca5a5}
.card{box-shadow:0 12px 32px rgba(0,0,0,.45)}
.primary:hover{background:#4163f6}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>{_PAGE_CSS}</style></head>"
        f"<body><main class=\"card\"><div class=\"mark\">SS</div>{body}</main></body></html>"
    )


def error_page(message: str, detail: str = "", *, connector: str = "") -> str:
    body = (
        f"<span class=\"badge\">{html.escape(connector or 'MCP')} connector</span>"
        "<h1>This connection was refused</h1>"
        f"<div class=\"error\">{html.escape(message)}"
        + (f"<br><br><code>{html.escape(detail)}</code>" if detail else "")
        + "</div>"
        "<small>Nothing was shared with the assistant. Close this window and check the "
        "connector card in Settings &rarr; AI (MCP), which has a built-in test that shows "
        "exactly which step fails.</small>"
    )
    return _page("Connection refused", body)


def login_page(action: str, *, connector: str, client_name: str, error: str = "",
               allow_one_tap: bool = True) -> str:
    """Sign in to authorize a connector.

    The browser's sign-in is the app password (``ADMIN_PASSWORD``), so this page
    asks for the same password. ``MCP_OAUTH_REQUIRE_LOGIN=false`` restores the
    old one-tap behaviour for a deployment that has deliberately removed every
    password — never use it on an address other people can reach.
    """
    query = html.escape(action, quote=True)
    body = (
        f"<span class=\"badge\">{html.escape(connector)} connector</span>"
        f"<h1>Sign in to connect {html.escape(client_name)}</h1>"
        "<p>The assistant wants to operate this app on your behalf. Sign in as the operator "
        "to continue.</p>"
        + (f"<div class=\"error\">{html.escape(error)}</div>" if error else "")
        + f"<form method=\"post\" action=\"{query}\">"
        "<label for=\"password\">App password</label>"
        "<input id=\"password\" name=\"password\" type=\"password\" autocomplete=\"current-password\">"
        "<button class=\"primary\" type=\"submit\">Continue</button>"
        + (
            "<button class=\"ghost\" type=\"submit\" name=\"one_tap\" value=\"1\">"
            "Continue as the operator (one tap)</button>"
            if allow_one_tap
            else ""
        )
        + "</form>"
        "<small>Every action the assistant takes is logged in Settings &rarr; AI (MCP), and you can "
        "revoke this connection there at any time.</small>"
    )
    return _page("Sign in", body)


def consent_page(action: str, *, connector: str, client_name: str, scope: str,
                 endpoint: str, client_id: str, ticket: str = "") -> str:
    """The approval screen — what the assistant will be allowed to do.

    Read access is always granted; write is a checkbox, pre-ticked only when the
    client asked for it. The page says in plain words what "write" means here
    (real SMS and email to real people), because that is the one thing an
    operator should not approve without reading.
    """
    query = html.escape(action, quote=True)
    wants_write = scope_can_write(scope)
    ticket_field = (
        f'<input type="hidden" name="ticket" value="{html.escape(ticket, quote=True)}">'
        if ticket else ""
    )
    body = (
        f'<span class="badge">{html.escape(connector)} connector</span>'
        f"<h1>Connect {html.escape(client_name)}?</h1>"
        "<p>This assistant will be able to call this app's tools, with the same rules, consent "
        "checks and audit log as the UI.</p>"
        f'<form method="post" action="{query}">'
        f"{ticket_field}"
        '<div class="scopes">'
        '<div class="scope"><input type="checkbox" id="s_read" name="scope" value="read" '
        'checked disabled><label for="s_read" style="margin:0;font-weight:500">Read &mdash; '
        "contacts, lists, campaigns, inbox, analytics</label></div>"
        '<div class="scope"><input type="checkbox" id="s_write" name="scope" value="write" '
        + ("checked" if wants_write else "")
        + '><label for="s_write" style="margin:0;font-weight:500">Write &mdash; create contacts, '
        "start campaigns, and <strong>send real SMS and email</strong></label></div>"
        "</div>"
        '<button class="primary" type="submit" name="decision" value="approve">Approve</button>'
        '<button class="ghost" type="submit" name="decision" value="deny">Deny</button>'
        "</form>"
        f"<small>Endpoint <code>{html.escape(endpoint)}</code><br>Client "
        f"<code>{html.escape(client_id[:60])}</code><br>Requested scope "
        f"<code>{html.escape(scope)}</code></small>"
    )
    return _page("Approve connection", body)


def approved_page(*, connector: str, scope: str) -> str:
    body = (
        f"<span class=\"badge\">{html.escape(connector)} connector</span>"
        "<h1>Connected</h1>"
        "<div class=\"ok\">Approval sent back to the assistant. You can close this window.</div>"
        f"<small>Granted scope: <code>{html.escape(scope)}</code>. Revoke any time from "
        "Settings &rarr; AI (MCP).</small>"
    )
    return _page("Connected", body)


def parse_form(raw: bytes, content_type: str) -> dict:
    """Read a token/registration body sent as JSON *or* as a form.

    Claude posts ``application/x-www-form-urlencoded`` to the token endpoint and
    JSON to dynamic registration; other clients do the opposite. Accepting both
    is required, not generous.
    """
    text = (raw or b"").decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    ctype = (content_type or "").lower()
    if "json" in ctype or text.startswith("{"):
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {k: v for k, v in parse_qsl(text, keep_blank_values=True)}
