"""
HTTP surface for the MCP server plus the token UI that manages access.

Two routers, on purpose:

* ``protocol_router`` is mounted at ``/mcp`` only — the single address an
  assistant is pointed at. ``POST`` is the Model Context Protocol endpoint
  (JSON-RPC 2.0, answered as JSON or as one SSE event when the client asks for
  ``text/event-stream``); ``GET`` explains that there is no server-initiated
  stream; ``DELETE`` is a stateless no-op. Deliberately *not* mirrored under
  ``/api/v1``: one canonical URL is easier to secure and to explain.
* ``router`` carries the app's own management endpoints (``/api/v1/mcp/tokens``,
  ``/api/v1/mcp/activity``), used by the settings screen and protected by the
  normal login session — not by an MCP token.

Session discipline matters here: reading the token, running the tool, and
writing the audit row each get their own short-lived session, because the tool
call goes back into this same app and must not fight the request's own
transaction for the database (see ``app/mcp/server.py``).
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory, get_db
from app.mcp import server as mcp_server
from app.models.mcp import McpCall, McpToken
from app.models.user import User
from app.security.auth import get_current_user

logger = logging.getLogger(__name__)

#: Token management, for the app's own settings screen.
router = APIRouter()
#: The MCP protocol itself, mounted at /mcp.
protocol_router = APIRouter()


def _wants_sse(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    return "text/event-stream" in accept


def _rpc_response(payload: dict | None, *, sse: bool, status_code: int = 200) -> Response:
    """MCP responses: plain JSON, or one SSE event carrying the same JSON."""
    if payload is None:
        # A notification (e.g. notifications/initialized) gets 202, no body.
        return Response(status_code=202)
    if sse:
        body = f"event: message\ndata: {json.dumps(payload)}\n\n"
        return Response(content=body, media_type="text/event-stream", status_code=status_code)
    return JSONResponse(content=payload, status_code=status_code)


UNAUTHORIZED = {
    "jsonrpc": "2.0",
    "error": {
        "code": -32001,
        "message": (
            "Unauthorized. Create a token in the app under Settings → AI (MCP) and send it as "
            "'Authorization: Bearer <token>' (a ?token= query parameter also works)."
        ),
    },
    "id": None,
}


async def _dispatch(db: AsyncSession, token: mcp_server.TokenView, message: dict | list):
    """Run one JSON-RPC message (or a batch) and return the reply payload."""
    if isinstance(message, list):
        # Batches were dropped from MCP, but a JSON-RPC batch is still legal
        # JSON-RPC, so answer each element instead of failing the whole request.
        replies = []
        for item in message:
            if isinstance(item, dict):
                reply = await mcp_server.dispatch(db, token, item)
                if reply is not None:
                    replies.append(reply)
        return replies or None
    if not isinstance(message, dict):
        return {
            "jsonrpc": "2.0", "id": None,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
    return await mcp_server.dispatch(db, token, message)


async def _handle(request: Request) -> Response:
    raw_token = mcp_server.token_from_request(request)

    # 1. Identify the caller, then let that session go.
    async with async_session_factory() as db:
        token = await mcp_server.authenticate(db, raw_token)
    if token is None:
        return JSONResponse(
            status_code=401, content=UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"}
        )

    try:
        message = await request.json()
    except Exception:
        return _rpc_response(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": -32700, "message": "Parse error: body must be JSON"}},
            sse=_wants_sse(request),
        )

    # 2. Establish the authority the tool call runs with, in its own session.
    async with async_session_factory() as db:
        admin_id = await mcp_server.admin_user_id(db)
    mcp_server.CURRENT_ADMIN_ID.set(admin_id)

    # 3. Run the tool, then write the audit trail — again, nothing overlapping.
    async with async_session_factory() as db:
        reply = await _dispatch(db, token, message)
        await db.commit()

    return _rpc_response(reply, sse=_wants_sse(request))


@protocol_router.post("")
@protocol_router.post("/")
async def mcp_endpoint(request: Request) -> Response:
    """The MCP endpoint. Stateless: every call carries its own token."""
    return await _handle(request)


@protocol_router.get("")
@protocol_router.get("/")
async def mcp_stream(request: Request) -> Response:
    """GET is only for server-initiated streams, and this server has none."""
    return JSONResponse(
        status_code=405,
        content={
            "error": "This MCP server is stateless and has no server-initiated stream.",
            "hint": "POST your JSON-RPC requests to this same URL with "
                    "Authorization: Bearer <token>.",
        },
        headers={"Allow": "POST, DELETE"},
    )


@protocol_router.delete("")
@protocol_router.delete("/")
async def mcp_teardown() -> Response:
    """Session teardown — nothing to tear down, but clients expect a 200."""
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Token management (normal login session, not an MCP token)
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
async def list_tokens(
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """The AI tokens that can operate this app. Plaintext is never stored."""
    rows = (await db.execute(select(McpToken).order_by(McpToken.id.desc()))).scalars().all()
    return {"items": [_token_out(t) for t in rows]}


@router.post("/tokens", status_code=201)
async def create_token(
    data: dict,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Create a token. The value is returned ONCE — it cannot be read back."""
    name = str((data or {}).get("name") or "").strip() or "AI assistant"
    scope = str((data or {}).get("scope") or "write").lower()
    if scope not in ("read", "write"):
        raise HTTPException(422, "scope must be 'read' or 'write'")

    raw, hashed, prefix = mcp_server.create_token_value()
    token = McpToken(name=name[:120], token_hash=hashed, prefix=prefix, scope=scope,
                     is_active=True)
    db.add(token)
    await db.commit()
    await db.refresh(token)
    return {
        "id": token.id,
        "name": token.name,
        "scope": token.scope,
        "prefix": token.prefix,
        # Shown once, on creation only.
        "token": raw,
    }


@router.delete("/tokens/{token_id}", status_code=204)
async def revoke_token(
    token_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Revoke a token: whatever is using it stops working immediately.

    The row is kept, deactivated, so the activity log still shows what that
    assistant did and when it was cut off.
    """
    token = (
        await db.execute(select(McpToken).where(McpToken.id == token_id))
    ).scalar_one_or_none()
    if token is None:
        raise HTTPException(404, "Token not found")
    token.is_active = False
    await db.commit()


@router.get("/activity")
async def activity(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
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
