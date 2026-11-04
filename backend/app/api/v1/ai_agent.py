"""AI Agent API — sessions, streaming chat, approvals, files, voice, memory.

Everything the browser needs and nothing it must not have:

  * POST   /models                         live model list per provider
  * GET    /health                         real 1-token ping per provider
  * GET    /settings                       keys (masked), models, toggles
  * PUT    /settings                       save a key / model / toggle
  * POST   /settings/test                  test the *saved* key (or a typed one)
  * GET    /sessions                       list chats
  * POST   /sessions                       new chat
  * GET    /sessions/{id}                  one chat + messages + files + pending cards
  * PATCH  /sessions/{id}                  rename, switch model, set skills
  * DELETE /sessions/{id}
  * POST   /sessions/{id}/chat             SSE stream (application/x-ndjson fallback)
  * POST   /actions/{id}/approve|cancel    the confirmation card's two buttons
  * GET    /activity                       the audit log, with undo
  * POST   /activity/{id}/undo
  * POST   /files , /sessions/{id}/files   upload with parse + preview
  * POST   /transcribe                     voice (Whisper, then Gemini)
  * GET/PUT/DELETE /memory/{scope}          global + per-session memory
  * GET/POST/DELETE /skills                 instruction skills

Rate limits are set per route (the agent runs tools with owner privileges).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.ai_agent import (
    AIAgentAction,
    AIAgentFile,
    AIAgentMessage,
    AIAgentSession,
    AIAgentSkill,
)
from app.models.user import User
from app.security.auth import get_current_user
from app.security.rate_limit import limiter
from app.services import agent_files, agent_runtime, agent_tools, ai_providers
from app.config import settings as app_settings

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def _session_or_404(db: AsyncSession, session_id: str) -> AIAgentSession:
    session = await agent_runtime.get_session(db, session_id)
    if session is None:
        raise HTTPException(404, "Chat not found")
    return session


def _serialise_message(message: AIAgentMessage) -> dict:
    payload: dict[str, Any] = {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "tool_calls": message.tool_calls,
        "tool_call_id": message.tool_call_id,
    }
    if message.role == "assistant" and (message.content or "").startswith('{"__error__"'):
        try:
            payload["error"] = json.loads(message.content)["__error__"]
        except Exception:
            pass
    return payload


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------
@router.get("/models")
async def list_models(
    provider: str = Query("groq", pattern="^(groq|gemini)$"),
    refresh: bool = Query(True, description="Ask the provider now instead of using a cache"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The models the provider currently serves, plus the one selected here.

    This is the only place a model name can come from — there is no default
    model constant anywhere in the codebase, which is what stopped the
    hard-coded `llama-3.3-70b-versatile` / `gemini-1.5-flash` 404s.
    """
    try:
        models = await ai_providers.list_models(db, provider)
    except ai_providers.ProviderError as error:
        return JSONResponse(
            status_code=200,
            content={
                "provider": provider,
                "models": [],
                "selected": await ai_providers.get_model(db, provider),
                "error": error.to_dict(),
            },
        )
    selected = await ai_providers.get_model(db, provider)
    ids = {m.id for m in models}
    if not selected or selected not in ids:
        selected = ai_providers.pick_default(provider, models) or (models[0].id if models else None)
        if selected:
            await ai_providers.set_model(db, provider, selected)
            await db.commit()
    return {
        "provider": provider,
        "models": [
            {
                "id": m.id,
                "label": m.label,
                "tools": m.tools,
                "context": m.context,
                # The UI marks this so nobody wonders why it was chosen.
                "recommended": m.id == ai_providers.pick_default(provider, models),
            }
            for m in models
        ],
        "selected": selected,
        "error": None,
    }


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
@router.get("/health")
async def agent_health(
    deep: bool = Query(False, description="Deprecated: the check is always live"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Live status for both providers.

    The old version reported "configured" because a row existed in the settings
    table — while every chat failed on a dead model. This one asks the provider
    for a one-token completion, so the dot in the header means something.
    """
    checks = await ai_providers.ping_all(db)
    default_provider = await ai_providers.get_default_provider(db)
    healthy = [c for c in checks if c.ok]
    active = next((c for c in checks if c.provider == default_provider), checks[0] if checks else None)
    await db.commit()
    return {
        "providers": [c.to_dict() for c in checks],
        "active_provider": active.provider if active else None,
        "active_model": active.model if active else None,
        # A single colour for the header dot: green / amber / red.
        "state": (
            "ok" if healthy
            else "degraded" if any(c.state in ("rate_limited",) for c in checks)
            else "down"
        ),
        "tools": len(agent_tools.REGISTRY),
        # The browser renders a list of area names. areas() itself is a count
        # map used by the runtime prompt, so do not leak that shape here.
        "tool_areas": sorted(agent_tools.areas()),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
class SettingsUpdate(BaseModel):
    provider: Optional[str] = Field(None, pattern="^(groq|gemini)$")
    api_key: Optional[str] = None
    clear_key: bool = False
    model: Optional[str] = None
    default_provider: Optional[str] = None
    auto_fallback: Optional[bool] = None
    auto_approve: Optional[bool] = None


@router.get("/settings")
async def get_settings(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    summary = await ai_providers.keys_summary(db)
    return {
        **summary,
        "models": {p: await ai_providers.get_model(db, p) for p in ai_providers.PROVIDERS},
        "default_provider": await ai_providers.get_default_provider(db),
        "auto_fallback": await ai_providers.get_flag(db, ai_providers.AUTO_FALLBACK_SETTING, True),
        "auto_approve": await ai_providers.get_flag(db, ai_providers.AUTO_APPROVE_SETTING, False),
        "voice": await _voice_settings(db),
    }


class VoiceSettings(BaseModel):
    auto_send: Optional[bool] = None
    language: Optional[str] = None
    read_aloud: Optional[bool] = None


async def _voice_settings(db: AsyncSession) -> dict:
    raw = await ai_providers._get_value(db, ai_providers.VOICE_SETTING)
    try:
        stored = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        stored = {}
    return {
        "auto_send": bool(stored.get("auto_send", False)),
        "language": stored.get("language") or "",
        "read_aloud": bool(stored.get("read_aloud", False)),
    }


@router.put("/settings")
async def put_settings(
    body: SettingsUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if body.provider and (body.api_key is not None or body.clear_key):
        if body.clear_key:
            await ai_providers.set_api_key(db, body.provider, "")
        elif body.api_key and body.api_key.strip():
            await ai_providers.set_api_key(db, body.provider, body.api_key)
    if body.provider and body.model:
        await ai_providers.set_model(db, body.provider, body.model)
    if body.default_provider in ai_providers.PROVIDERS:
        await ai_providers._set_value(db, ai_providers.DEFAULT_PROVIDER_SETTING, body.default_provider)
    if body.auto_fallback is not None:
        await ai_providers.set_flag(db, ai_providers.AUTO_FALLBACK_SETTING, body.auto_fallback)
    if body.auto_approve is not None:
        await ai_providers.set_flag(db, ai_providers.AUTO_APPROVE_SETTING, body.auto_approve)
    await db.commit()
    return {"success": True, **(await ai_providers.keys_summary(db)),
            "models": {p: await ai_providers.get_model(db, p) for p in ai_providers.PROVIDERS}}


@router.put("/settings/voice")
async def put_voice_settings(
    body: VoiceSettings,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    current = await _voice_settings(db)
    if body.auto_send is not None:
        current["auto_send"] = body.auto_send
    if body.language is not None:
        current["language"] = body.language
    if body.read_aloud is not None:
        current["read_aloud"] = body.read_aloud
    await ai_providers._set_value(db, ai_providers.VOICE_SETTING, json.dumps(current),
                                  description="AI Agent voice options")
    await db.commit()
    return {"success": True, "voice": current}


class TestKeyRequest(BaseModel):
    provider: Optional[str] = Field(None, pattern="^(groq|gemini)$")
    api_key: Optional[str] = None


@router.post("/settings/test")
async def test_key(
    body: TestKeyRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Test a key — the saved one when none is typed, the typed one otherwise.

    The old version demanded ``api_key`` in the body, so the only key the
    operator actually had (the saved, masked one) could never be tested.
    """
    providers = [body.provider] if body.provider else list(ai_providers.PROVIDERS)
    results = []
    for provider in providers:
        typed = (body.api_key or "").strip()
        key = typed or await ai_providers.get_api_key(db, provider)
        if not key:
            results.append({
                "provider": provider,
                "success": False,
                "state": "key_missing",
                "message": f"No {provider.title()} key is saved.",
                "hint": "Paste a key and press Save.",
            })
            continue
        try:
            models = await ai_providers.list_models(db, provider, api_key=key)
        except ai_providers.ProviderError as error:
            results.append({
                "provider": provider,
                "success": False,
                "state": error.code,
                "message": error.message,
                "hint": error.hint,
                "using_saved_key": not typed,
            })
            continue
        chosen = ai_providers.pick_default(provider, models)
        results.append({
            "provider": provider,
            "success": True,
            "state": "ok",
            "message": f"{provider.title()} OK — {len(models)} models available.",
            "models": len(models),
            "suggested_model": chosen,
            "using_saved_key": not typed,
            "key_hint": ai_providers.mask(key),
        })
    single = len(results) == 1
    payload = results[0] if single else {"results": results, "success": all(r["success"] for r in results)}
    return payload


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------
class SessionCreate(BaseModel):
    name: Optional[str] = "New chat"
    model: Optional[str] = None
    system_prompt: Optional[str] = None


@router.get("/sessions")
async def list_sessions(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    sessions = await agent_runtime.list_sessions(db, user_id=user.id)
    if not sessions:
        sessions = await agent_runtime.list_sessions(db)
    out = []
    for session in sessions:
        last = (
            await db.execute(
                select(AIAgentMessage)
                .where(AIAgentMessage.session_id == session.id, AIAgentMessage.role.in_(("user", "assistant")))
                .order_by(AIAgentMessage.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        count = (
            await db.execute(
                select(func.count(AIAgentMessage.id)).where(AIAgentMessage.session_id == session.id)
            )
        ).scalar() or 0
        out.append({
            "id": session.id,
            "name": session.name,
            "model": session.model,
            "skills": session.skills or [],
            "message_count": count,
            "last_message": (last.content or "")[:160] if last else "",
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        })
    return {"sessions": out}


@router.post("/sessions")
async def create_session(
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await agent_runtime.create_session(
        db, user_id=user.id, name=body.name or "New chat",
        model=body.model, system_prompt=body.system_prompt,
    )
    await db.commit()
    return {
        "session": {
            "id": session.id,
            "name": session.name,
            "model": session.model,
            "created_at": session.created_at.isoformat() if session.created_at else None,
        }
    }


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await _session_or_404(db, session_id)
    messages = await agent_runtime.get_messages(db, session_id)
    files = list(
        (
            await db.execute(
                select(AIAgentFile).where(AIAgentFile.session_id == session_id)
                .order_by(AIAgentFile.created_at.desc())
            )
        ).scalars().all()
    )
    pending = list(
        (
            await db.execute(
                select(AIAgentAction).where(
                    AIAgentAction.session_id == session_id,
                    AIAgentAction.status == "pending",
                )
            )
        ).scalars().all()
    )
    cards = []
    for action in pending:
        title, summary, details = await agent_runtime.describe_action(db, action.tool, action.args or {})
        cards.append({
            "kind": "confirmation",
            "action_id": action.id,
            "tool": action.tool,
            "title": title,
            "summary": summary,
            "details": details,
        })
    return {
        "session": {
            "id": session.id,
            "name": session.name,
            "model": session.model,
            "memory": session.memory,
            "skills": session.skills or [],
            "system_prompt": session.system_prompt,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        },
        "messages": [_serialise_message(m) for m in messages],
        "files": [
            {
                "id": f.id,
                "filename": f.filename,
                "size": f.size_bytes,
                "preview": f.preview,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in files
        ],
        "pending_actions": cards,
    }


class SessionUpdate(BaseModel):
    name: Optional[str] = None
    model: Optional[str] = None
    skills: Optional[list[str]] = None
    system_prompt: Optional[str] = None
    memory: Optional[str] = None


@router.patch("/sessions/{session_id}")
async def update_session(
    session_id: str,
    body: SessionUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await _session_or_404(db, session_id)
    if body.name:
        session.name = body.name.strip()[:200]
    if body.model in ai_providers.PROVIDERS:
        session.model = body.model
    if body.skills is not None:
        session.skills = body.skills
    if body.system_prompt is not None:
        session.system_prompt = body.system_prompt
    if body.memory is not None:
        session.memory = body.memory[:20000]
    await db.commit()
    return {"success": True, "session": {"id": session.id, "name": session.name, "model": session.model}}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await _session_or_404(db, session_id)
    await db.delete(session)
    await db.commit()
    return {"success": True}


# ---------------------------------------------------------------------------
# chat (streaming)
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str
    file_ids: Optional[list[int]] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    #: Ask for the full JSON response instead of the stream (used by tests and
    #: by any client that cannot read a stream).
    stream: bool = True


@router.post("/sessions/{session_id}/chat")
@limiter.limit(app_settings.RATE_LIMIT_AI_AGENT)
async def chat(
    request: Request,
    response: Response,
    session_id: str,
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Stream one turn. Errors arrive as events, never as a bare 500."""
    session = await _session_or_404(db, session_id)
    text = (body.message or "").strip()
    if not text:
        raise HTTPException(400, "message is required")

    if body.model:
        await ai_providers.set_model(db, body.provider or session.model, body.model)
    if body.provider and body.provider != session.model:
        session.model = body.provider
        await db.flush()

    if not body.stream:
        events: list[dict] = []
        error_payload = None
        async for event in agent_runtime.run_turn(
            db, session, text, user=user, file_ids=body.file_ids,
            provider_override=body.provider, model_override=body.model,
        ):
            events.append(event)
            if event.get("type") == "error":
                error_payload = event.get("error")
        response_text = "".join(
            e.get("text", "") for e in events if e.get("type") == "text"
        )
        messages = await agent_runtime.get_messages(db, session.id)
        return {
            "response": response_text,
            "messages": [_serialise_message(m) for m in messages],
            "events": events,
            "error": error_payload,
        }

    async def event_stream():
        # ndjson rather than SSE: it needs no special browser API, survives
        # proxies that buffer text/event-stream, and carries the same objects.
        try:
            async for event in agent_runtime.run_turn(
                db, session, text, user=user, file_ids=body.file_ids,
                provider_override=body.provider, model_override=body.model,
            ):
                yield json.dumps(event, default=str) + "\n"
        except Exception as exc:  # pragma: no cover - last-resort guard
            logger.exception("agent: chat stream failed")
            await db.rollback()
            yield json.dumps({
                "type": "error",
                "error": {
                    "code": "upstream_error", "provider": session.model,
                    "status": 500, "message": str(exc)[:300],
                    "hint": "Retry. If it keeps failing, switch provider.",
                    "retryable": True,
                },
            }) + "\n"
            yield json.dumps({"type": "done"}) + "\n"

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# approvals
# ---------------------------------------------------------------------------
@router.post("/actions/{action_id}/approve")
async def approve_action(
    action_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    action = (await db.execute(select(AIAgentAction).where(AIAgentAction.id == action_id))).scalar_one_or_none()
    if action is None:
        raise HTTPException(404, "Action not found")
    if action.status != "pending":
        raise HTTPException(409, f"This action is already {action.status}")
    result = await agent_runtime.execute_action(db, action, user=user)
    # Leave a visible record in the transcript so the chat reads chronologically.
    await agent_runtime.add_message(
        db, action.session_id or "", "tool",
        json.dumps({"approved": True, "tool": action.tool, "result": result.get("result")}, default=str)[:12000],
        tool_calls={"name": action.tool},
    )
    await db.commit()
    return {"success": True, **result}


@router.post("/actions/{action_id}/cancel")
async def cancel_action(
    action_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    action = (await db.execute(select(AIAgentAction).where(AIAgentAction.id == action_id))).scalar_one_or_none()
    if action is None:
        raise HTTPException(404, "Action not found")
    if action.status not in ("pending",):
        raise HTTPException(409, f"This action is already {action.status}")
    action.status = "cancelled"
    await db.commit()
    return {"success": True, "action_id": action.id, "status": action.status}


# ---------------------------------------------------------------------------
# activity + undo
# ---------------------------------------------------------------------------
@router.get("/activity")
async def agent_activity(
    limit: int = Query(50, ge=1, le=200),
    session_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(AIAgentAction).order_by(AIAgentAction.id.desc()).limit(limit)
    if session_id:
        stmt = stmt.where(AIAgentAction.session_id == session_id)
    rows = list((await db.execute(stmt)).scalars().all())
    return {
        "actions": [
            {
                "id": row.id,
                "session_id": row.session_id,
                "tool": row.tool,
                "args": row.args,
                "summary": row.summary,
                "status": row.status,
                "error": row.error,
                "duration_ms": row.duration_ms,
                "undoable": bool(row.undoable and not row.undone_at),
                "undone_at": row.undone_at.isoformat() if row.undone_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    }


@router.post("/activity/{action_id}/undo")
async def undo_action(
    action_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Undo a reversible action (contact restore, list re-create).

    Only actions whose result carries an ``undo`` payload can be undone, and
    only once — the second attempt is refused rather than silently re-running.
    """
    action = (await db.execute(select(AIAgentAction).where(AIAgentAction.id == action_id))).scalar_one_or_none()
    if action is None:
        raise HTTPException(404, "Action not found")
    if action.undone_at is not None:
        raise HTTPException(409, "This action was already undone")
    undo = (action.result or {}).get("undo") if isinstance(action.result, dict) else None
    if not undo:
        raise HTTPException(400, "This action cannot be undone")

    from app.models.contact import Contact

    restored = 0
    kind = undo.get("kind")
    if kind == "restore_contacts":
        for item in undo.get("contacts") or []:
            contact = Contact(
                phone_number=item.get("phone"),
                email=item.get("email"),
                first_name=item.get("first_name"),
                last_name=item.get("last_name"),
                business_name=item.get("business_name"),
                country="Nigeria",
                source="ai_agent_undo",
            )
            db.add(contact)
            restored += 1
    elif kind == "restore_contact":
        item = undo.get("contact") or {}
        db.add(Contact(
            phone_number=item.get("phone"),
            email=item.get("email"),
            country="Nigeria",
            source="ai_agent_undo",
        ))
        restored = 1
    else:
        raise HTTPException(400, f"Cannot undo a “{kind}” action")
    action.undone_at = datetime.now(timezone.utc)
    await db.commit()
    return {"success": True, "restored": restored, "action_id": action.id}


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------
async def _store_upload(db: AsyncSession, file: UploadFile, session_id: Optional[str]) -> AIAgentFile:
    filename = file.filename or "upload.csv"
    raw = await file.read(agent_files.MAX_BYTES + 1)
    if not raw:
        raise HTTPException(400, "That file is empty.")
    try:
        parsed = agent_files.parse_file(filename, raw)
    except agent_files.FileParseError as error:
        raise HTTPException(400, str(error)) from error

    record = AIAgentFile(
        session_id=session_id,
        filename=filename[:255],
        content=parsed.text or json.dumps(parsed.preview or {}, default=str),
        preview={**parsed.preview, "summary": parsed.summary},
        size_bytes=len(raw),
    )
    db.add(record)
    await db.flush()
    return record


def _file_payload(record: AIAgentFile) -> dict:
    return {
        "id": record.id,
        "filename": record.filename,
        "size": record.size_bytes,
        "preview": record.preview,
        "summary": (record.preview or {}).get("summary"),
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


@router.post("/sessions/{session_id}/files")
async def upload_to_session(
    session_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _session_or_404(db, session_id)
    record = await _store_upload(db, file, session_id)
    await db.commit()
    return {"file": _file_payload(record)}


@router.post("/files")
async def upload_file(
    file: UploadFile = File(...),
    session_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    record = await _store_upload(db, file, session_id)
    await db.commit()
    return {"file": _file_payload(record)}


@router.get("/files")
async def list_files(
    session_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(AIAgentFile).order_by(AIAgentFile.created_at.desc()).limit(50)
    if session_id:
        stmt = stmt.where(AIAgentFile.session_id == session_id)
    rows = list((await db.execute(stmt)).scalars().all())
    return {"files": [_file_payload(row) for row in rows]}


@router.get("/files/{file_id}")
async def get_file(
    file_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    record = (await db.execute(select(AIAgentFile).where(AIAgentFile.id == file_id))).scalar_one_or_none()
    if record is None:
        raise HTTPException(404, "File not found")
    rows: list[dict] = []
    try:
        parsed = agent_files.parse_stored(record.filename, record.content)
        rows = agent_files.slice_rows(parsed.rows, offset, limit)
    except agent_files.FileParseError:
        rows = []
    return {"file": _file_payload(record), "rows": rows, "offset": offset, "limit": limit}


@router.delete("/files/{file_id}")
async def delete_file(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    record = (await db.execute(select(AIAgentFile).where(AIAgentFile.id == file_id))).scalar_one_or_none()
    if record is None:
        raise HTTPException(404, "File not found")
    await db.delete(record)
    await db.commit()
    return {"success": True}


# ---------------------------------------------------------------------------
# voice
# ---------------------------------------------------------------------------
@router.post("/transcribe")
@limiter.limit(app_settings.RATE_LIMIT_AI_TRANSCRIBE)
async def transcribe(
    request: Request,
    response: Response,
    audio: UploadFile = File(...),
    language: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Audio -> text. Whisper via Groq first, Gemini audio second.

    ``language`` is optional on purpose: forcing ``en`` hurts Nigerian English
    and Pidgin, which auto-detection handles better. The setting exists for
    anyone who wants to pin it.
    """
    raw = await audio.read()
    if not raw:
        raise HTTPException(400, "The recording was empty.")
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(413, "That recording is too large (over 25 MB). Try a shorter one.")
    try:
        result = await ai_providers.transcribe(
            db,
            raw,
            audio.filename or "voice.webm",
            audio.content_type or "audio/webm",
            language=(language or "").strip() or None,
            prompt="Nigerian English and Nigerian Pidgin. Contact names, phone numbers and business names.",
        )
    except ai_providers.ProviderError as error:
        raise HTTPException(
            status_code=400 if error.code in ("key_missing",) else 502,
            detail=error.to_dict(),
        ) from error
    return {"text": result["text"], "provider": result["provider"],
            "model": result["model"], "language": result.get("language")}


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------
class MemoryUpdate(BaseModel):
    text: str = ""


@router.get("/memory/global")
async def get_global_memory(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return {"scope": "global", "text": await agent_runtime.get_global_memory(db)}


@router.put("/memory/global")
async def put_global_memory(
    body: MemoryUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await agent_runtime.set_global_memory(db, body.text)
    await db.commit()
    return {"success": True, "text": body.text[:20000]}


@router.delete("/memory/global")
async def clear_global_memory(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await agent_runtime.set_global_memory(db, "")
    await db.commit()
    return {"success": True}


@router.get("/memory/{session_id}")
async def get_session_memory(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await _session_or_404(db, session_id)
    return {"scope": "session", "session_id": session.id, "text": session.memory or ""}


@router.put("/memory/{session_id}")
async def put_session_memory(
    session_id: str,
    body: MemoryUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await _session_or_404(db, session_id)
    session.memory = body.text[:20000]
    await db.commit()
    return {"success": True, "text": session.memory}


@router.delete("/memory/{session_id}")
async def clear_session_memory(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await _session_or_404(db, session_id)
    session.memory = ""
    await db.commit()
    return {"success": True}


# ---------------------------------------------------------------------------
# skills
# ---------------------------------------------------------------------------
@router.get("/skills")
async def list_skills(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    rows = list((await db.execute(select(AIAgentSkill).order_by(AIAgentSkill.name))).scalars().all())
    return {
        "skills": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "content": (s.content or "")[:2000],
                "is_active": s.is_active,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in rows
        ]
    }


@router.post("/skills")
async def create_skill(
    name: str = Form(...),
    description: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    content: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    raw = None
    if file is not None:
        raw = (await file.read()).decode("utf-8", "ignore")
        if (not name or name == "skill") and file.filename:
            name = file.filename.rsplit(".", 1)[0]
    body = (content or raw or "").strip()
    if not body:
        raise HTTPException(400, "Skill content is required (upload a file or send content).")
    skill = await agent_runtime_create_skill(db, name, description, body)
    await db.commit()
    return {"skill": {"id": skill.id, "name": skill.name, "description": skill.description}}


@router.post("/skills/json")
async def create_skill_json(
    body: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not body.get("name") or not body.get("content"):
        raise HTTPException(400, "name and content are required")
    skill = await agent_runtime_create_skill(db, body["name"], body.get("description"), body["content"])
    await db.commit()
    return {"skill": {"id": skill.id, "name": skill.name}}


async def agent_runtime_create_skill(db: AsyncSession, name: str, description: Optional[str], content: str) -> AIAgentSkill:
    import re
    import uuid as _uuid

    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", (name or "").strip())[:100] or f"skill_{_uuid.uuid4().hex[:6]}"
    existing = (await db.execute(select(AIAgentSkill).where(AIAgentSkill.name == safe))).scalar_one_or_none()
    if existing is not None:
        existing.description = description or existing.description
        existing.content = content
        existing.is_active = True
        await db.flush()
        return existing
    skill = AIAgentSkill(name=safe, description=description, content=content, is_active=True)
    db.add(skill)
    await db.flush()
    return skill


@router.get("/skills/{skill_id}")
async def get_skill(
    skill_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    skill = (await db.execute(select(AIAgentSkill).where(AIAgentSkill.id == skill_id))).scalar_one_or_none()
    if skill is None:
        raise HTTPException(404, "Skill not found")
    return {"skill": {"id": skill.id, "name": skill.name, "description": skill.description,
                      "content": skill.content, "is_active": skill.is_active}}


@router.delete("/skills/{skill_id}")
async def delete_skill(
    skill_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await db.execute(delete(AIAgentSkill).where(AIAgentSkill.id == skill_id))
    await db.commit()
    return {"success": True}


# ---------------------------------------------------------------------------
# tools catalogue (for the UI's "what can the agent do?" panel)
# ---------------------------------------------------------------------------
@router.get("/tools")
async def list_tools(user: User = Depends(get_current_user)):
    return {
        "tools": [
            {
                "name": t.name,
                "area": t.area,
                "description": t.description,
                "danger": t.danger,
            }
            for t in agent_tools.all_tools()
        ],
        "areas": agent_tools.areas(),
    }
