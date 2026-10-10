"""
A Model Context Protocol server that lets an AI assistant operate this app.

Transport: Streamable HTTP (JSON-RPC 2.0 over POST /mcp) — the shape ChatGPT
connectors and Claude connectors/`mcp-remote` speak. Authentication is a bearer
token created in the app's settings.

Why this is safe to expose:

* every tool call is authenticated twice — the MCP token, then (internally) a
  real operator session, so the assistant can never do more than the app's own
  UI could;
* the token carries a scope: ``read`` tokens can look at everything but change
  nothing, and write tools are refused with a clear message;
* each call is journalled (tool, endpoint, result, duration) and the log is
  visible in the app.

Why there is no duplicated business logic:

* tools are declarative (see ``registry.py``) and are executed by calling the
  app's own REST API in-process through the ASGI app. Consent checks, opt-out
  rules, validation, campaign counters and audit trails are therefore identical
  to what a human clicking in the UI gets.

One structural note, because it is easy to get wrong: the request that carries a
tool call must not hold a database transaction while the tool runs, or the two
transactions collide (on SQLite that shows up as "database is locked"). So the
flow is strictly: read the token in a short-lived session → close it → run the
tool → open another session to write the audit row. Nothing overlaps.
"""

import contextvars
import hashlib
import json
import logging
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.mcp import connectors as connector_profiles
from app.mcp import response as tool_response
from app.mcp.connectors import ConnectorProfile
from app.mcp.registry import TOOLS, TOOLS_BY_NAME, Tool, tools_for
from app.models.mcp import McpCall, McpToken
from app.security.auth import create_access_token, ensure_admin

logger = logging.getLogger(__name__)

SERVER_NAME = "sendersms"
SERVER_VERSION = "1.1.0"
#: Newest first. The client's requested version is echoed when we support it.
#: Shared with the connector profiles so the metadata documents and the
#: handshake can never disagree about what this server speaks.
PROTOCOL_VERSIONS = connector_profiles.PROTOCOL_VERSIONS
DEFAULT_PROTOCOL = connector_profiles.DEFAULT_PROTOCOL

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: The operator whose authority a tool call borrows. Set once per request.
CURRENT_ADMIN_ID: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "mcp_admin_id", default=None
)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@dataclass
class TokenView:
    """A detached, read-only view of the caller's authority.

    Safe to use after the session that read it closes. Two kinds of credential
    arrive here:

    * ``kind="static"`` — an ``mcp_…`` token created in Settings, backed by a
      :class:`McpToken` row;
    * ``kind="oauth"`` — a JWT from the OAuth 2.1 flow a ChatGPT or Claude
      connector just completed, backed by :class:`McpOAuthToken`.

    Both carry the same coarse ``scope`` switch, so the read/write fence in
    :func:`run_tool` is one code path either way.
    """

    id: int
    name: str
    scope: str
    prefix: str
    #: static | oauth
    kind: str = "static"
    #: Which connector endpoint the request arrived on (chatgpt / claude / …).
    connector: str = "generic"
    #: The OAuth client_id, when this is an OAuth access token.
    client_id: str | None = None
    #: The resource the token was issued for; checked against the endpoint asked.
    audience: str | None = None

    @property
    def can_write(self) -> bool:
        return (self.scope or "write") == "write" or "write" in set((self.scope or "").split())


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_token_value() -> tuple[str, str, str]:
    """Return ``(plaintext, sha256, prefix)`` for a new token."""
    raw = f"mcp_{secrets.token_urlsafe(32)}"
    return raw, hash_token(raw), raw[:12]


def token_from_request(request: Request) -> str | None:
    """Bearer header first, then X-MCP-Token, then ?token= (easiest to paste)."""
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    direct = request.headers.get("x-mcp-token")
    if direct:
        return direct.strip()
    query = request.query_params.get("token")
    return query.strip() if query else None


async def authenticate(
    db: AsyncSession,
    raw_token: str | None,
    *,
    profile: ConnectorProfile | None = None,
) -> TokenView | None:
    """Look the caller up, by static token or by OAuth access token.

    Read-only: usage counters are written later, in their own session, so this
    one can be closed before any tool runs.

    Order matters for one practical reason — a static token is a single indexed
    lookup, while an OAuth token has to be signature-verified and then checked
    against the revocation table. Trying the cheap one first keeps the common
    case (Claude Code with ``--header``, the Arena bridge, curl) fast.
    """
    connector = profile.key if profile else "generic"
    if not raw_token:
        return None

    token = (
        await db.execute(
            select(McpToken).where(
                McpToken.token_hash == hash_token(raw_token),
                McpToken.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if token is not None:
        return TokenView(
            id=token.id,
            name=token.name or "AI assistant",
            scope=token.scope or "write",
            prefix=token.prefix or "",
            kind="static",
            connector=connector,
        )

    # OAuth 2.1 access token (what a ChatGPT or Claude connector presents).
    from app.mcp import oauth as mcp_oauth

    if "." not in raw_token:
        return None
    claims = await mcp_oauth.verify_access_token(
        db, raw_token, audience=profile.resource if profile else None
    )
    if not claims:
        return None
    audience = str(claims.get("aud") or "")
    if profile is not None and audience and audience.rstrip("/") != profile.resource.rstrip("/"):
        # RFC 8707: the token was issued for a different resource. Refuse rather
        # than let a connector's token be replayed against another endpoint.
        logger.warning(
            "MCP OAuth: token audience %r does not match %r", audience, profile.resource
        )
        return None
    admin_id = int(claims.get("sub") or 0) or None
    client_id = str(claims.get("client_id") or "")
    scope = str(claims.get("scope") or "read")
    client_name = client_id.split("/")[-1][:40] if client_id.startswith("http") else client_id[:24]
    return TokenView(
        id=admin_id or 0,
        name=f"{profile.label if profile else 'AI'} ({client_name or 'OAuth'})",
        scope=scope,
        prefix=raw_token[:12],
        kind="oauth",
        connector=str(claims.get("connector") or connector),
        client_id=client_id or None,
        audience=audience or None,
    )


async def touch_token(db: AsyncSession, token: TokenView, error: str | None = None) -> None:
    """Record that the credential was used (and what went wrong, if anything)."""
    if token.kind == "oauth":
        # OAuth usage is counted on the connector row and on the access-token
        # row; the error, if any, lands on the connection so the settings card
        # can show it next to the client that caused it.
        from app.mcp import oauth as mcp_oauth
        from app.models.mcp_oauth import McpConnection

        connection = await mcp_oauth.get_connection(db, token.connector)
        connection.call_count = (connection.call_count or 0) + 1
        connection.last_call_at = datetime.now(timezone.utc)
        if error:
            connection.last_error = f"{token.name}: {error}"[:500]
            connection.status = "error"
        await db.execute(
            update(McpConnection)
            .where(McpConnection.connector == connection.connector)
            .values(
                call_count=connection.call_count,
                last_call_at=connection.last_call_at,
                last_error=connection.last_error,
                status=connection.status,
            )
        )
        return

    values: dict[str, Any] = {
        "call_count": McpToken.call_count + 1,
        "last_used_at": datetime.now(timezone.utc),
    }
    if error:
        values["last_error"] = error[:500]
    await db.execute(update(McpToken).where(McpToken.id == token.id).values(**values))


async def admin_user_id(db: AsyncSession) -> int:
    """The operator a tool call acts as.

    ``ensure_admin`` also keeps the stored password in step with the environment,
    so it is the right thing to call — it just has to happen in a session of its
    own, before any tool runs, never while a tool's transaction is open.
    """
    admin = await ensure_admin(db)
    await db.commit()
    return admin.id


# ---------------------------------------------------------------------------
# The in-process bridge to the app's own API
# ---------------------------------------------------------------------------


#: The most of a non-JSON body held in memory for one tool call.
_RAW_CEILING = 5_000_000


async def call_app(
    method: str,
    path: str,
    *,
    query: dict | None = None,
    body: Any = None,
    form: dict | None = None,
    files: dict | None = None,
) -> tuple[int, Any]:
    """Call one of this app's own endpoints in-process, as the operator.

    The operator session is a signed JWT minted here and valid for five minutes,
    so no database row is created and nothing is left behind after the call.
    """
    from app.main import app as fastapi_app

    user_id = CURRENT_ADMIN_ID.get()
    if user_id is None:  # pragma: no cover — dispatch always sets it
        raise RuntimeError("MCP call made without an operator identity")
    cookie = create_access_token({"sub": str(user_id)}, expires_delta=timedelta(minutes=5))

    clean_query = {k: v for k, v in (query or {}).items() if v is not None and v != ""}
    request_kwargs: dict = {"params": clean_query or None}
    if files:
        request_kwargs["files"] = files
        request_kwargs["data"] = {k: str(v) for k, v in (form or {}).items() if v is not None}
    elif body is not None:
        request_kwargs["json"] = body
    elif form:
        request_kwargs["data"] = {k: str(v) for k, v in form.items() if v is not None}

    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://mcp.internal", timeout=180.0
    ) as client:
        response = await client.request(
            method.upper(),
            path,
            headers={"Cookie": f"sendsms_session={cookie}"},
            **request_kwargs,
        )

    try:
        payload = response.json()
    except ValueError:
        # A body that is not JSON (a CSV download). It used to be cut to its first
        # 4,000 characters here -- 29 of 1,297 rows -- with nothing saying so. Keep it
        # whole (up to a sanity ceiling) and report its true size; the response
        # builder shortens it by whole lines and says how many it kept.
        text = response.text
        payload = {
            "raw": text[:_RAW_CEILING],
            "content_type": response.headers.get("content-type"),
            "chars": len(text),
        }
    return response.status_code, payload


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


def _one_line(value: Any, limit: int = 1200) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text[:limit]


@dataclass
class ToolOutcome:
    """What the assistant sees, plus what the operator's token log should say."""

    result: dict
    token_error: str | None = None


def _tool_result(
    text: str, *, is_error: bool = False, data: Any = None, envelope: dict | None = None
) -> dict:
    """An MCP tool result whose text is always ONE valid JSON object.

    Payload results pass their ``envelope`` (see :mod:`app.mcp.response`); a plain
    message -- a refusal, the guide, a validation problem -- becomes
    ``{"ok": ..., "message": ...}``. A client can ``json.loads`` any tool response
    without special cases.
    """
    document = envelope if envelope is not None else tool_response.message_envelope(
        text, is_error=is_error
    )
    result: dict = {
        "content": [{"type": "text", "text": json.dumps(document, default=str, ensure_ascii=False)}],
        "isError": is_error,
    }
    if isinstance(data, (dict, list)):
        # Structured clients (and newer spec revisions) get the JSON too, bounded the
        # same way as the text and flagged (``_truncated``) when anything was cut.
        result["structuredContent"] = data if isinstance(data, dict) else {"items": data}
    return result


def _payload_outcome(
    method: str, path: str, status: int, payload: Any, query: dict | None = None
) -> "ToolOutcome":
    """Build the outcome for an endpoint's answer: the envelope, bounded and honest."""
    envelope, structured = tool_response.build_envelope(method, path, status, payload, query=query)
    if status >= 400:
        failure = envelope["message"]
        return ToolOutcome(
            _tool_result(failure, is_error=True, envelope=envelope), token_error=failure
        )
    return ToolOutcome(_tool_result("", data=structured, envelope=envelope))


async def run_tool(db: AsyncSession, token: TokenView, tool: Tool, args: dict) -> ToolOutcome:
    """Execute one tool and return an MCP tool result.

    ``db`` is used only for the audit row, and only after the tool's own request
    has finished, so the two never hold transactions at the same time.
    """
    started = time.time()

    refusal: str | None = None
    if tool.method == "REQUEST":
        # The escape hatch works in both directions, so the guard has to look at
        # the method the caller asked for.
        requested = str((args or {}).get("method") or "GET").upper()
        if not token.can_write and requested in WRITE_METHODS:
            refusal = (
                f"Refused: this MCP token is read-only, and {requested} {args.get('path')} "
                "would change data. Create a token with the 'write' scope for that."
            )
    elif tool.scope == "write" and not token.can_write:
        refusal = (
            f"Refused: this MCP token is read-only. Create/write tools need a token with the "
            f"'write' scope (tool '{tool.name}' would have changed data)."
        )
    if refusal:
        # A refusal is worth recording: it is the difference between "the AI
        # misbehaved" and "the AI was fenced in, as configured".
        await _audit(db, token, tool.name, f"{tool.method} {tool.path}", False, 403, refusal,
                     args, started)
        # A refusal is the configuration working, not an error worth flagging on
        # the token itself.
        return ToolOutcome(_tool_result(refusal, is_error=True))

    try:
        if tool.method == "GUIDE":
            return ToolOutcome(_tool_result(APP_GUIDE))
        if tool.method == "OPENAPI":
            return ToolOutcome(_tool_result(await _endpoint_catalogue()))
        if tool.method == "PREVIEW_TEMPLATE":
            return await _run_preview_template(db, token, tool, args)
        if tool.method == "REQUEST":
            return await _run_escape_hatch(db, token, tool, args)
        return await _run_declared(db, token, tool, args)
    except Exception as exc:  # noqa: BLE001 — a tool bug must not kill the session
        logger.exception("MCP tool %s failed", tool.name)
        await _audit(db, token, tool.name, f"{tool.method} {tool.path}", False, None, str(exc),
                     args, started)
        return ToolOutcome(_tool_result(f"The tool failed: {exc}", is_error=True),
                           token_error=str(exc))


_KIND_ALIASES = {"campaign": "campaign", "legacy": "campaign", "classic": "campaign", "ads": "ads"}


async def _route_by_kind(
    db: AsyncSession, token: TokenView, tool: Tool, args: dict, started: float
) -> "ToolOutcome | tuple[str, str]":
    """Decide which campaign system a campaign tool should act on.

    The classic campaigns and the Ads Manager number independently, so
    ``pause_campaign(2)`` can name two different campaigns. Acting on whichever
    the classic API happened to find would pause (or start, or delete) the wrong
    one -- and the one that is actually sending is usually the other. So: an
    explicit ``kind`` is honoured; without one the id is looked up, an id that
    exists in both systems is *refused* with the candidates listed, and only an
    unambiguous id is acted on.

    Returns ``(method, path)`` to call, or a finished :class:`ToolOutcome` (the
    refusal) when it must not proceed.
    """
    campaign_id = args.get("campaign_id")
    raw = args.get("kind")
    if raw not in (None, ""):
        kind = _KIND_ALIASES.get(str(raw).strip().lower())
        if kind is None:
            message = f"Unknown kind {raw!r}: use 'campaign' (alias 'legacy') or 'ads'."
            return ToolOutcome(_tool_result(message, is_error=True), token_error=message)
    else:
        lookup = f"/api/v1/overview/campaigns/{urllib.parse.quote(str(campaign_id))}"
        status, payload = await call_app("GET", lookup)
        if status >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            failure = f"HTTP {status} from GET {lookup}: {_one_line(detail or payload, 700)}"
            await _audit(db, token, tool.name, f"GET {lookup}", False, status, failure, args, started)
            return ToolOutcome(_tool_result(failure, is_error=True), token_error=failure)
        kind = payload.get("kind")

    if kind != "ads":
        return tool.method, tool.path
    alternative = tool.kind_paths.get("ads")
    if alternative is None:
        message = (
            f"{tool.name} is not available for Ads Manager campaigns (kind 'ads'). "
            "Use pause_campaign / resume_campaign / start_campaign / validate_campaign for them, "
            "or app_api_request for anything else under /api/v1/ads/campaigns/{id}."
        )
        await _audit(db, token, tool.name, f"{tool.method} {tool.path}", False, 400, message,
                     args, started)
        return ToolOutcome(_tool_result(message, is_error=True), token_error=message)
    return alternative


async def _run_declared(
    db: AsyncSession, token: TokenView, tool: Tool, args: dict
) -> ToolOutcome:
    started = time.time()
    path = tool.path
    method_override: str | None = None
    if tool.by_kind:
        routed = await _route_by_kind(db, token, tool, args, started)
        if isinstance(routed, ToolOutcome):
            return routed
        method_override, path = routed
    query: dict = {}
    body: dict = {}
    form: dict = {}
    files: dict = {}
    #: Some endpoints take the array itself as the body (POST /lists/{id}/contacts).
    root_body: Any = None

    for param in tool.params:
        if param.name not in args or args[param.name] is None:
            if param.required:
                missing = f"Missing required argument '{param.name}'."
                return ToolOutcome(_tool_result(missing, is_error=True), token_error=missing)
            continue
        value = args[param.name]
        if param.root:
            root_body = value
        elif param.where == "meta":
            continue  # consumed by the server (routing), not sent to the endpoint
        elif param.where == "path":
            path = path.replace("{" + param.name + "}", urllib.parse.quote(str(value)))
        elif param.where == "query":
            if value == "":
                # A blank filter is never what the caller means; dropping it here
                # keeps the request (and the audit row) honest.
                continue
            query[param.name] = value
        elif param.where == "body":
            body[param.name] = value
        elif param.where == "form":
            form[param.name] = value
        elif param.where == "file":
            raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
            files["file"] = ("import.csv", raw, "text/csv")

    method = (method_override or tool.method).upper()
    status, payload = await call_app(
        method,
        path,
        query=query,
        body=(
            root_body if root_body is not None
            else body or ({} if tool.body_always else None)
        ),
        form=form or None,
        files=files or None,
    )
    ok = status < 400
    await _audit(db, token, tool.name, f"{method} {path}", ok, status,
                 None if ok else _one_line(payload), args, started)

    return _payload_outcome(method, path, status, payload, query)


async def _run_escape_hatch(
    db: AsyncSession, token: TokenView, tool: Tool, args: dict
) -> ToolOutcome:
    started = time.time()
    method = str(args.get("method") or "GET").upper()
    target = str(args.get("path") or "")
    if not target.startswith("/"):
        bad_path = "'path' must start with '/', e.g. /api/v1/campaigns/"
        return ToolOutcome(_tool_result(bad_path, is_error=True), token_error=bad_path)

    parsed = urllib.parse.urlsplit(target)
    query = dict(urllib.parse.parse_qsl(parsed.query))
    query.update({k: v for k, v in (args.get("query") or {}).items() if v is not None})

    status, payload = await call_app(
        method, parsed.path or "/", query=query, body=args.get("body")
    )
    ok = status < 400
    await _audit(db, token, tool.name, f"{method} {target}", ok, status,
                 None if ok else _one_line(payload), args, started)
    return _payload_outcome(method, target, status, payload, query)


#: Contact fields the app's renderer fills in. Kept in step with the sample
#: values the templates API accepts.
_SAMPLE_FIELDS = (
    "first_name", "last_name", "business_name", "phone_number", "email",
    "city", "state", "website", "industry",
)


async def _run_preview_template(
    db: AsyncSession, token: TokenView, tool: Tool, args: dict
) -> ToolOutcome:
    """Render a saved template with real values.

    Two calls, no second renderer: read the template, read the contact it should
    be filled in for, then hand both to the app's own preview endpoint (the same
    one the Templates page uses). Re-implementing the rendering here is exactly
    how a preview stops matching what actually gets sent.
    """
    started = time.time()
    template_id = args.get("template_id")
    step = f"PREVIEW_TEMPLATE /api/v1/templates/{template_id}"

    status, template = await call_app("GET", f"/api/v1/templates/{template_id}")
    if status >= 400:
        failure = f"HTTP {status} reading template {template_id}"
        await _audit(db, token, tool.name, step, False, status, failure, args, started)
        return ToolOutcome(_tool_result(failure, is_error=True), token_error=failure)

    query: dict = {
        "body": template.get("body") or "",
        "subject": template.get("subject"),
        "html_body": template.get("html_body"),
        "channel": args.get("channel") or template.get("channel") or "sms",
    }
    contact_id = args.get("contact_id")
    if contact_id:
        contact_status, contact = await call_app("GET", f"/api/v1/contacts/{contact_id}")
        if contact_status >= 400:
            failure = f"HTTP {contact_status} reading contact {contact_id}"
            await _audit(db, token, tool.name, step, False, contact_status, failure, args,
                         started)
            return ToolOutcome(_tool_result(failure, is_error=True), token_error=failure)
        for field in _SAMPLE_FIELDS:
            if contact.get(field):
                query[field] = contact[field]

    status, payload = await call_app("POST", "/api/v1/templates/preview", query=query)
    ok = status < 400
    await _audit(db, token, tool.name, step, ok, status,
                 None if ok else _one_line(payload), args, started)
    if not ok:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        failure = f"HTTP {status} rendering template {template_id}: {_one_line(detail or payload, 400)}"
        return ToolOutcome(_tool_result(failure, is_error=True), token_error=failure)

    payload = {"template_id": template_id, "template_name": template.get("name"), **payload}
    return _payload_outcome("PREVIEW_TEMPLATE", f"/api/v1/templates/{template_id}", 200, payload)


async def _endpoint_catalogue() -> str:
    """Read the app's own OpenAPI so the assistant can discover everything."""
    from app.main import app as fastapi_app

    spec = fastapi_app.openapi()
    lines = ["Every endpoint this app exposes (call them with app_api_request):", ""]
    by_tag: dict[str, list[str]] = {}
    for path, operations in sorted(spec.get("paths", {}).items()):
        for method, op in operations.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            if not path.startswith("/api/v1"):
                continue
            tag = (op.get("tags") or ["other"])[0]
            summary = (op.get("summary") or "").strip()
            by_tag.setdefault(tag, []).append(
                f"  {method.upper():6} {path}" + (f"  — {summary}" if summary else "")
            )
    for tag in sorted(by_tag):
        lines.append(f"[{tag}]")
        lines.extend(by_tag[tag])
        lines.append("")
    return "\n".join(lines)


async def _audit(
    db: AsyncSession,
    token: TokenView,
    tool: str,
    request: str | None,
    ok: bool,
    status: int | None,
    error: str | None,
    args: dict,
    started: float,
) -> None:
    try:
        db.add(
            McpCall(
                # An OAuth caller has no mcp_tokens row; the connection is named
                # instead so the audit log still says who did it.
                token_id=None if token.kind == "oauth" else token.id,
                token_name=token.name,
                tool=tool,
                request=request,
                ok=ok,
                status_code=status,
                error=(error or None),
                arguments=_one_line(args, 2000),
                duration_ms=int((time.time() - started) * 1000),
            )
        )
        await db.flush()
    except Exception as exc:  # noqa: BLE001 — auditing must never break a call
        logger.warning("MCP audit write failed: %s", exc)


# ---------------------------------------------------------------------------
# JSON-RPC dispatch
# ---------------------------------------------------------------------------


def _rpc_result(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id: Any, code: int, message: str, data: Any = None) -> dict:
    error: dict = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": error}


def server_info(profile: ConnectorProfile | None = None) -> dict:
    """The ``serverInfo`` block returned from ``initialize``.

    ``title`` is what ChatGPT and Claude show as the connector's name when the
    client did not get one from its own registration form.
    """
    info = {
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
        "title": f"{settings.APP_NAME} — {profile.label}" if profile else str(settings.APP_NAME),
    }
    return info


def capabilities() -> dict:
    return {
        "tools": {"listChanged": False},
        "resources": {"subscribe": False, "listChanged": False},
        "prompts": {"listChanged": False},
    }


def negotiate_version(requested: Any) -> str:
    """Echo the client's version when we speak it, else offer our newest.

    A client on an older revision keeps working (the spec requires the server to
    continue with the version it offered), and a client from the future gets the
    newest one we implement rather than a hard failure.
    """
    value = str(requested or DEFAULT_PROTOCOL)
    return value if value in PROTOCOL_VERSIONS else DEFAULT_PROTOCOL


async def dispatch(
    db: AsyncSession,
    token: TokenView,
    message: dict,
    profile: ConnectorProfile | None = None,
    toolset: str | None = None,
) -> dict | None:
    """Handle one JSON-RPC message. Returns None for notifications."""
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}

    if method is None:
        return _rpc_error(msg_id, -32600, "Invalid Request: no method")

    # ------------------------------------------------------------- lifecycle
    if method == "initialize":
        return _rpc_result(msg_id, {
            "protocolVersion": negotiate_version(params.get("protocolVersion")),
            "capabilities": capabilities(),
            "serverInfo": server_info(profile),
            "instructions": APP_GUIDE_SUMMARY,
        })

    if method in ("notifications/initialized", "notifications/cancelled", "initialized",
                  "notifications/root/list_changed"):
        return None

    if method == "ping":
        return _rpc_result(msg_id, {})

    # The 2026-07-28 revision replaces the initialize handshake with an
    # on-demand capability query. Answer it with the same block, so a client on
    # the stateless core needs no handshake at all.
    if method == "server/discover":
        return _rpc_result(msg_id, {
            "protocolVersion": DEFAULT_PROTOCOL,
            "capabilities": capabilities(),
            "serverInfo": server_info(profile),
            "instructions": APP_GUIDE_SUMMARY,
        })

    # ----------------------------------------------------------------- tools
    published = tools_for(connector_profiles.tool_names(profile, toolset))
    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": [t.as_mcp() for t in published]})

    if method == "tools/call":
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        tool = TOOLS_BY_NAME.get(name)
        if tool is None:
            return _rpc_error(msg_id, -32602, f"Unknown tool '{name}'")
        if published and tool not in published:
            return _rpc_error(
                msg_id, -32602,
                f"'{name}' is not published on the {profile.label if profile else 'this'} "
                "connector (it exposes a focused tool set). Ask the operator to switch this "
                "connector's tool set to 'full' in Settings → AI (MCP), or use app_api_request.",
            )
        if not isinstance(args, dict):
            return _rpc_error(msg_id, -32602, "arguments must be an object")
        outcome = await run_tool(db, token, tool, args)
        await touch_token(db, token, error=outcome.token_error)
        await db.commit()
        return _rpc_result(msg_id, outcome.result)

    # ------------------------------------------------------------- resources
    if method == "resources/list":
        return _rpc_result(msg_id, {"resources": RESOURCES})

    if method == "resources/read":
        uri = str(params.get("uri") or "")
        for resource in RESOURCES:
            if resource["uri"] == uri:
                text = await _read_resource(uri)
                return _rpc_result(msg_id, {
                    "contents": [{"uri": uri, "mimeType": resource["mimeType"], "text": text}]
                })
        return _rpc_error(msg_id, -32602, f"Unknown resource '{uri}'")

    # --------------------------------------------------------------- prompts
    if method == "prompts/list":
        return _rpc_result(msg_id, {
            "prompts": [
                {k: v for k, v in prompt.items() if k != "render"} for prompt in PROMPTS
            ]
        })

    if method == "prompts/get":
        name = str(params.get("name") or "")
        prompt = next((p for p in PROMPTS if p["name"] == name), None)
        if prompt is None:
            return _rpc_error(msg_id, -32602, f"Unknown prompt '{name}'")
        return _rpc_result(msg_id, prompt["render"](params.get("arguments") or {}))

    return _rpc_error(msg_id, -32601, f"Method not found: {method}")


# ---------------------------------------------------------------------------
# Resources & prompts
# ---------------------------------------------------------------------------

RESOURCES = [
    {
        "uri": "sendsms://guide",
        "name": "How this app works",
        "description": "Workflow, channels, consent rules and the safe order of operations.",
        "mimeType": "text/markdown",
    },
    {
        "uri": "sendsms://reference",
        "name": "Sending reference",
        "description": "Channels, Brevo senders, contact lists, variable registry and counts.",
        "mimeType": "application/json",
    },
    {
        "uri": "sendsms://inbox/recent",
        "name": "Recent replies",
        "description": "The newest conversations across SMS and email.",
        "mimeType": "application/json",
    },
]

PROMPTS = [
    {
        "name": "run_a_campaign",
        "description": "Step-by-step: build and launch a campaign without breaking consent rules.",
        "arguments": [
            {"name": "goal", "description": "What the campaign should achieve", "required": False},
            {"name": "channel", "description": "sms or email", "required": False},
        ],
        "render": lambda args: {
            "description": "Run a campaign end to end",
            "messages": [{
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Goal: {args.get('goal') or 'get replies from cold leads'}\n"
                        f"Channel: {args.get('channel') or 'email'}\n\n"
                        "Work in this order and stop to confirm before anything is sent:\n"
                        "1. how_to_use_this_app()\n"
                        "2. search_contacts(channel=…) or list_contact_lists() — pick or build the audience\n"
                        "3. Write the message (create_template if it should be reused). For email, "
                        "use real HTML: a short opener, one clear ask, one link, and the "
                        "{{first_name}} variable.\n"
                        "4. send_test_email() or preview_template() and show me the result.\n"
                        "5. create_campaign(status draft) → validate_campaign() → ask me to confirm "
                        "→ start_campaign().\n"
                        "Then report what was sent and to how many people."
                    ),
                },
            }],
        },
    },
    {
        "name": "triage_the_inbox",
        "description": "Read new replies, summarise them and draft answers.",
        "arguments": [
            {"name": "channel", "description": "sms, email or all", "required": False},
        ],
        "render": lambda args: {
            "description": "Triage the inbox",
            "messages": [{
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Look at the newest conversations ({args.get('channel') or 'all'} channels) "
                        "with inbox_conversations(). For each unread one: summarise what they want in "
                        "one line, say whether they look interested, and draft a short reply. Show "
                        "me the drafts BEFORE sending anything, then reply with reply_to_email() or "
                        "reply_to_conversation(), and mark the conversation with mark_conversation()."
                    ),
                },
            }],
        },
    },
]


def _resource_json(document: Any) -> str:
    """A JSON resource, complete or -- if too large -- shortened and marked as such.

    These used to be ``json.dumps(...)[:12000]``: cut mid-string, no longer JSON, and
    silent about it.
    """
    _, structured = tool_response.build_envelope("GET", "resource", 200, document, budget=24_000)
    return json.dumps(structured if isinstance(structured, dict) else document, default=str,
                      ensure_ascii=False)


async def _read_resource(uri: str) -> str:
    if uri == "sendsms://guide":
        return APP_GUIDE
    if uri == "sendsms://reference":
        status, payload = await call_app("GET", "/api/v1/email/reference")
        lists_status, lists = await call_app("GET", "/api/v1/lists/")
        variables_status, variables = await call_app("GET", "/api/v1/variables/")
        return _resource_json(
            {
                "reference": payload if status < 400 else {"error": payload},
                "lists": lists if lists_status < 400 else [],
                "variables": variables if variables_status < 400 else [],
            }
        )
    if uri == "sendsms://inbox/recent":
        status, payload = await call_app(
            "GET", "/api/v1/inbox/conversations", query={"per_page": 15}
        )
        return _resource_json(payload if status < 400 else {"error": payload})
    return "{}"


APP_GUIDE_SUMMARY = (
    "SMS + email outreach app. Always read the sendsms://guide resource (or call "
    "how_to_use_this_app) first: email and SMS have separate consent rules, campaigns must go "
    "to a contact list, and nothing sends until a campaign is validated and started."
)

APP_GUIDE = """# Operating this app (SMS + Email outreach)

This app sends **SMS** and **email**, and both live in one inbox. Read this before you
send anything.

## The two channels
- **sms** — Nigerian numbers in `+234…` form, sent through the operator's SIM gateway.
  Consent is `is_opted_out` (a contact who texted STOP).
- **email** — sent through the operator's **Brevo** account(s). Consent is separate:
  `is_email_opted_out` / `is_email_undeliverable`. A contact can be unsubscribed from
  email and still be textable — never assume one means the other.

Campaigns, templates, conversations, automations and follow-ups are all channel-aware.
Pass `channel: "sms"` or `"email"` explicitly.

## The safe order of operations
1. **Audience** — `search_contacts`, `list_contact_lists`, `create_contact_list`,
   `import_contacts_csv` (CSV text, ideally straight into a list), `add_contacts_to_list`.
   A campaign needs a `list_id`. A contact needs a **phone number or an email address** —
   not both, but at least one, and rows carrying neither are dropped by the import. A row
   with both is stored as one record with both channels, and re-importing a person you
   already have **merges** into the existing contact rather than creating a second one.
   Check the import result (`imported` / `merged` / `invalid` / `phone_only` / `email_only`)
   and report the skipped rows instead of assuming everyone landed.
2. **Message** — `create_template` for anything reusable, or pass `message_body`
   straight to a campaign. Personalise with `{{first_name}}`, `{{business_name}}`,
   `{{city}}`, `{{website}}` and any column imported from CSV (`list_variables` shows
   them all).
3. **Email specifics** — an email campaign needs `subject`, and a sender from
   `list_email_accounts`. Put HTML in `html_body` (images by URL, links, one clear CTA);
   the plain-text `body` is the fallback every mail client shows, so always write it.
   Attachments are `[{"name": "quote.pdf", "content_base64": "…", "content_type": "application/pdf"}]`
   (10 files, 4 MB each, 8 MB total).
4. **Check before sending** — `validate_campaign` (audience + message + sender + consent),
   and for email `send_test_email` to the operator's own address. Show the operator what
   will go out.
5. **Send** — `start_campaign` (whole list) or `send_email_now` / `send_sms_now` (one
   person). Both really deliver to real people. Confirm with the operator first unless
   they explicitly asked you to send.
6. **Follow up** — `create_campaign_followup` (wait N minutes, then nudge everyone who
   has not replied) or `create_followup` for one contact. Follow-ups land **inside the
   same chat/thread** as the original message.

## Reading the other side
- `inbox_conversations(channel="all")` — every conversation; `get_inbox_conversation`
  for one thread's history.
- `reply_to_email` replies from the same sender, threaded — the prospect sees one
  conversation, not a new email.
- `email_engagement(contact_id)` — opens, clicks and **which links** that person clicked.
  Use it: someone who clicked three times and never replied is warm, someone who never
  opened after five sends is not.
- `mark_conversation(mark="mark-interested" | "mark-not-interested" | "mark-close")`
  keeps the pipeline honest.

## Rules that are enforced by the app (do not fight them)
- Opted-out/unsubscribed/bounced contacts are **refused**, not silently skipped — if a
  send is refused, say so instead of retrying or inventing a new address.
- Placeholder addresses (`example.com`, `noreply@…`) are rejected as undeliverable.
- A campaign cannot be edited once it has started; duplicate it instead.
- Anything you cannot find a tool for: `list_api_endpoints` then `app_api_request`.
"""
