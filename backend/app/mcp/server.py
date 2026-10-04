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

from app.mcp.registry import TOOLS, TOOLS_BY_NAME, Tool
from app.models.mcp import McpCall, McpToken
from app.security.auth import create_access_token, ensure_admin

logger = logging.getLogger(__name__)

SERVER_NAME = "sendersms"
SERVER_VERSION = "1.0.0"
#: Newest first. The client's requested version is echoed when we support it.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL = PROTOCOL_VERSIONS[0]

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
    """A detached, read-only view of the token, safe to use after its session closes."""

    id: int
    name: str
    scope: str
    prefix: str

    @property
    def can_write(self) -> bool:
        return (self.scope or "write") == "write"


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


async def authenticate(db: AsyncSession, raw_token: str | None) -> TokenView | None:
    """Look the token up. Read-only: the usage counter is written later, in its
    own session, so this one can be closed before any tool runs."""
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
    if token is None:
        return None
    return TokenView(id=token.id, name=token.name or "AI assistant",
                     scope=token.scope or "write", prefix=token.prefix or "")


async def touch_token(db: AsyncSession, token: TokenView, error: str | None = None) -> None:
    """Record that the token was used (and what went wrong, if anything)."""
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
        payload = {"raw": response.text[:4000]}
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


def _tool_result(text: str, *, is_error: bool = False, data: Any = None) -> dict:
    result: dict = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if isinstance(data, (dict, list)):
        # Structured clients (and newer spec revisions) get the raw JSON too.
        result["structuredContent"] = data if isinstance(data, dict) else {"items": data}
    return result


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


async def _run_declared(
    db: AsyncSession, token: TokenView, tool: Tool, args: dict
) -> ToolOutcome:
    started = time.time()
    path = tool.path
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

    method = tool.method.upper()
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

    if not ok:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        failure = f"HTTP {status} from {method} {path}: {_one_line(detail or payload, 400)}"
        return ToolOutcome(_tool_result(failure, is_error=True), token_error=failure)
    return ToolOutcome(_tool_result(
        f"{method} {path} → HTTP {status}\n\n{_one_line(payload, 6000)}", data=payload
    ))


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
    if not ok:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        failure = f"HTTP {status} from {method} {target}: {_one_line(detail or payload, 400)}"
        return ToolOutcome(_tool_result(failure, is_error=True), token_error=failure)
    return ToolOutcome(_tool_result(
        f"{method} {target} → HTTP {status}\n\n{_one_line(payload, 6000)}", data=payload
    ))


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
    return ToolOutcome(_tool_result(
        f"Rendered \"{template.get('name')}\" with "
        f"{'contact ' + str(contact_id) if contact_id else 'sample'} values:\n\n"
        f"{json.dumps(payload, indent=2, default=str)[:6000]}",
        data=payload,
    ))


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
                token_id=token.id,
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


def server_info() -> dict:
    return {"name": SERVER_NAME, "version": SERVER_VERSION}


def capabilities() -> dict:
    return {
        "tools": {"listChanged": False},
        "resources": {"subscribe": False, "listChanged": False},
        "prompts": {"listChanged": False},
    }


async def dispatch(db: AsyncSession, token: TokenView, message: dict) -> dict | None:
    """Handle one JSON-RPC message. Returns None for notifications."""
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}

    if method is None:
        return _rpc_error(msg_id, -32600, "Invalid Request: no method")

    # ------------------------------------------------------------- lifecycle
    if method == "initialize":
        requested = str(params.get("protocolVersion") or DEFAULT_PROTOCOL)
        version = requested if requested in PROTOCOL_VERSIONS else DEFAULT_PROTOCOL
        return _rpc_result(msg_id, {
            "protocolVersion": version,
            "capabilities": capabilities(),
            "serverInfo": server_info(),
            "instructions": APP_GUIDE_SUMMARY,
        })

    if method in ("notifications/initialized", "notifications/cancelled", "initialized"):
        return None

    if method == "ping":
        return _rpc_result(msg_id, {})

    # ----------------------------------------------------------------- tools
    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": [t.as_mcp() for t in TOOLS]})

    if method == "tools/call":
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        tool = TOOLS_BY_NAME.get(name)
        if tool is None:
            return _rpc_error(msg_id, -32602, f"Unknown tool '{name}'")
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


async def _read_resource(uri: str) -> str:
    if uri == "sendsms://guide":
        return APP_GUIDE
    if uri == "sendsms://reference":
        status, payload = await call_app("GET", "/api/v1/email/reference")
        lists_status, lists = await call_app("GET", "/api/v1/lists/")
        variables_status, variables = await call_app("GET", "/api/v1/variables/")
        return json.dumps(
            {
                "reference": payload if status < 400 else {"error": payload},
                "lists": lists if lists_status < 400 else [],
                "variables": variables if variables_status < 400 else [],
            },
            indent=2, default=str,
        )[:12000]
    if uri == "sendsms://inbox/recent":
        status, payload = await call_app(
            "GET", "/api/v1/inbox/conversations", query={"per_page": 15}
        )
        return json.dumps(
            payload if status < 400 else {"error": payload}, indent=2, default=str
        )[:12000]
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
   A campaign needs a `list_id`. Records are **phone-first**: every contact needs a phone
   number (`+234…`), even if you only ever email them, and the import drops rows without a
   usable number — check the import result and report the skipped rows instead of assuming
   everyone landed.
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
