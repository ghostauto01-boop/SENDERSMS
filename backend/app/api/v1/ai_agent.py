"""AI Agent API — in-app LLM assistant with sessions, memory, skills, file upload, and full app access."""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.security.auth import get_current_user
from app.services.ai_agent_service import (
    get_ai_keys, set_ai_key,
    list_skills, get_skill_by_name, create_skill, delete_skill,
    create_session, get_session, list_sessions, delete_session, get_messages,
    save_file, run_turn,
)
from app.models.ai_agent import AIAgentFile, AIAgentSession

router = APIRouter()


# ---------------------------------------------------------------------------
# Settings (Groq / Gemini keys)
# ---------------------------------------------------------------------------
class AIKeysUpdate(BaseModel):
    provider: str  # groq | gemini
    api_key: Optional[str] = None
    groq_model: Optional[str] = None
    gemini_model: Optional[str] = None

@router.get("/settings")
async def get_ai_settings(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    keys = await get_ai_keys(db)
    # mask for response
    def mask(v: str | None) -> str | None:
        if not v:
            return None
        return v[:4] + "***" + v[-4:] if len(v) > 8 else "***"
    return {
        "groq_configured": keys["groq_configured"],
        "gemini_configured": keys["gemini_configured"],
        "groq_hint": mask(keys["groq_api_key"]),
        "gemini_hint": mask(keys["gemini_api_key"]),
    }

@router.put("/settings")
async def put_ai_settings(body: AIKeysUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    if body.provider not in ("groq", "gemini"):
        raise HTTPException(400, "provider must be groq or gemini")
    await set_ai_key(db, body.provider, body.api_key)
    await db.commit()
    keys = await get_ai_keys(db)
    return {"success": True, "groq_configured": keys["groq_configured"], "gemini_configured": keys["gemini_configured"]}

# Test a key without saving
class AITestRequest(BaseModel):
    provider: str
    api_key: str

@router.post("/settings/test")
async def test_ai_key(body: AITestRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    if body.provider == "groq":
        import httpx
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {body.api_key}"})
                if r.status_code == 200:
                    return {"success": True, "message": f"Groq OK — {len(r.json().get('data', []))} models available"}
                return {"success": False, "error": r.text[:500]}
        except Exception as e:
            return {"success": False, "error": str(e)}
    elif body.provider == "gemini":
        import httpx
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={body.api_key}")
                if r.status_code == 200:
                    return {"success": True, "message": "Gemini API key is valid"}
                return {"success": False, "error": r.text[:500]}
        except Exception as e:
            return {"success": False, "error": str(e)}
    raise HTTPException(400, "provider must be groq or gemini")

# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
class SessionCreate(BaseModel):
    name: Optional[str] = "New Session"
    model: Optional[str] = "groq"
    system_prompt: Optional[str] = None

@router.get("/sessions")
async def list_agent_sessions(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    sessions = await list_sessions(db, user_id=user.id)
    # attach message count
    out = []
    for s in sessions:
        msgs = await get_messages(db, s.id, limit=1)
        out.append({
            "id": s.id,
            "name": s.name,
            "model": s.model,
            "memory": (s.memory or "")[:200],
            "skills": s.skills or [],
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        })
    return {"sessions": out}

@router.post("/sessions")
async def create_agent_session(body: SessionCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    sess = await create_session(db, user_id=user.id, name=body.name or "New Session", model=body.model or "groq", system_prompt=body.system_prompt)
    await db.commit()
    return {"session": {"id": sess.id, "name": sess.name, "model": sess.model, "created_at": sess.created_at.isoformat()}}

@router.get("/sessions/{session_id}")
async def get_agent_session(session_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    sess = await get_session(db, session_id)
    if not sess:
        raise HTTPException(404, "Session not found")
    msgs = await get_messages(db, session_id, limit=200)
    files = list((await db.execute(select(AIAgentFile).where(AIAgentFile.session_id == session_id).order_by(AIAgentFile.created_at.desc()))).scalars().all())
    return {
        "session": {"id": sess.id, "name": sess.name, "model": sess.model, "memory": sess.memory, "skills": sess.skills, "system_prompt": sess.system_prompt, "created_at": sess.created_at.isoformat(), "updated_at": sess.updated_at.isoformat()},
        "messages": [{"id": m.id, "role": m.role, "content": m.content, "tool_calls": m.tool_calls, "created_at": m.created_at.isoformat()} for m in msgs],
        "files": [{"id": f.id, "filename": f.filename, "size": f.size_bytes, "preview": f.preview, "created_at": f.created_at.isoformat()} for f in files],
    }

@router.delete("/sessions/{session_id}")
async def delete_agent_session(session_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    sess = await get_session(db, session_id)
    if not sess:
        raise HTTPException(404, "Session not found")
    await delete_session(db, session_id)
    await db.commit()
    return {"success": True}

@router.patch("/sessions/{session_id}")
async def update_agent_session(session_id: str, body: dict, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    sess = await get_session(db, session_id)
    if not sess:
        raise HTTPException(404, "Session not found")
    if "name" in body and body["name"]:
        sess.name = str(body["name"])[:200]
    if "model" in body and body["model"] in ("groq", "gemini"):
        sess.model = body["model"]
    if "skills" in body:
        sess.skills = body["skills"]
    if "system_prompt" in body:
        sess.system_prompt = body["system_prompt"]
    await db.flush()
    await db.commit()
    return {"success": True, "session": {"id": sess.id, "name": sess.name, "model": sess.model}}

# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str
    file_ids: Optional[list[int]] = None
    model: Optional[str] = None  # override for this turn

@router.post("/sessions/{session_id}/chat")
async def chat_with_session(session_id: str, body: ChatRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    sess = await get_session(db, session_id)
    if not sess:
        raise HTTPException(404, "Session not found")
    if body.model and body.model in ("groq", "gemini"):
        sess.model = body.model
        await db.flush()
    if not body.message or not body.message.strip():
        raise HTTPException(400, "message is required")
    result = await run_turn(db, sess, body.message.strip(), file_ids=body.file_ids)
    await db.commit()
    if "error" in result and result.get("needs_key"):
        raise HTTPException(400, result["error"])
    if "error" in result:
        raise HTTPException(500, result["error"])
    # Return full updated messages
    msgs = await get_messages(db, session_id, limit=200)
    return {
        "response": result.get("response"),
        "messages": [{"id": m.id, "role": m.role, "content": m.content, "tool_calls": m.tool_calls, "created_at": m.created_at.isoformat()} for m in msgs],
    }

# Simple quick chat without tools (for status)
class QuickAskRequest(BaseModel):
    message: str
    model: Optional[str] = "groq"

@router.post("/ask")
async def quick_ask(body: QuickAskRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    # One-off ask without session persistence
    from app.services.ai_agent_service import _call_llm, BASE_SYSTEM_PROMPT, TOOLS, get_ai_keys
    keys = await get_ai_keys(db)
    provider = body.model or "groq"
    api_key = keys.get("groq_api_key") if provider == "groq" else keys.get("gemini_api_key")
    if not api_key:
        raise HTTPException(400, f"No {provider} API key configured")
    messages = [{"role": "system", "content": BASE_SYSTEM_PROMPT}, {"role": "user", "content": body.message}]
    resp = await _call_llm(provider, messages, TOOLS, api_key)
    choice = (resp.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    return {"response": text}

# ---------------------------------------------------------------------------
# Files (CSV etc.)
# ---------------------------------------------------------------------------
@router.post("/sessions/{session_id}/files")
async def upload_file_to_session(session_id: str, file: UploadFile = File(...), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    sess = await get_session(db, session_id)
    if not sess:
        raise HTTPException(404, "Session not found")
    content = (await file.read()).decode("utf-8", errors="ignore")
    if len(content) > 1_000_000:
        content = content[:1_000_000]
    f = await save_file(db, session_id=session_id, filename=file.filename or "upload.csv", content=content)
    await db.commit()
    return {"file": {"id": f.id, "filename": f.filename, "size": f.size_bytes, "preview": f.preview}}

@router.post("/files")
async def upload_file_general(file: UploadFile = File(...), session_id: Optional[str] = Form(None), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    content = (await file.read()).decode("utf-8", errors="ignore")
    if len(content) > 1_000_000:
        content = content[:1_000_000]
    f = await save_file(db, session_id=session_id, filename=file.filename or "upload.csv", content=content)
    await db.commit()
    return {"file": {"id": f.id, "filename": f.filename, "size": f.size_bytes, "preview": f.preview}}

@router.get("/files")
async def list_files(session_id: Optional[str] = Query(None), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    q = select(AIAgentFile).order_by(AIAgentFile.created_at.desc()).limit(50)
    if session_id:
        q = q.where(AIAgentFile.session_id == session_id)
    rows = list((await db.execute(q)).scalars().all())
    return {"files": [{"id": f.id, "filename": f.filename, "session_id": f.session_id, "size": f.size_bytes, "preview": f.preview, "created_at": f.created_at.isoformat()} for f in rows]}

@router.get("/files/{file_id}")
async def get_file(file_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    f = (await db.execute(select(AIAgentFile).where(AIAgentFile.id == file_id))).scalar_one_or_none()
    if not f:
        raise HTTPException(404, "File not found")
    return {"file": {"id": f.id, "filename": f.filename, "content": f.content[:10000], "preview": f.preview, "size": f.size_bytes}}

@router.delete("/files/{file_id}")
async def delete_file(file_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    f = (await db.execute(select(AIAgentFile).where(AIAgentFile.id == file_id))).scalar_one_or_none()
    if not f:
        raise HTTPException(404, "File not found")
    await db.delete(f)
    await db.commit()
    return {"success": True}

# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
@router.get("/skills")
async def list_agent_skills(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    rows = await list_skills(db)
    return {"skills": [{"id": s.id, "name": s.name, "description": s.description, "content": s.content[:500], "is_active": s.is_active, "created_at": s.created_at.isoformat()} for s in rows]}

@router.post("/skills")
async def create_agent_skill(name: str = Form(...), description: Optional[str] = Form(None), file: Optional[UploadFile] = File(None), content: Optional[str] = Form(None), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    if file:
        raw = (await file.read()).decode("utf-8", errors="ignore")
        if not content:
            content = raw
        if not name or name == "skill":
            name = file.filename or name
    if not content:
        raise HTTPException(400, "Skill content is required (upload a file or send content)")
    skill = await create_skill(db, name=name, description=description, content=content)
    await db.commit()
    return {"skill": {"id": skill.id, "name": skill.name, "description": skill.description}}

@router.post("/skills/json")
async def create_agent_skill_json(body: dict, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    name = body.get("name")
    content = body.get("content")
    description = body.get("description")
    if not name or not content:
        raise HTTPException(400, "name and content required")
    skill = await create_skill(db, name=name, description=description, content=content)
    await db.commit()
    return {"skill": {"id": skill.id, "name": skill.name}}

@router.delete("/skills/{skill_id}")
async def delete_agent_skill(skill_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await delete_skill(db, skill_id)
    await db.commit()
    return {"success": True}

@router.get("/skills/{skill_id}")
async def get_agent_skill(skill_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    s = (await db.execute(select(__import__('app.models.ai_agent', fromlist=['AIAgentSkill']).AIAgentSkill).where(__import__('app.models.ai_agent', fromlist=['AIAgentSkill']).AIAgentSkill.id == skill_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Skill not found")
    return {"skill": {"id": s.id, "name": s.name, "description": s.description, "content": s.content}}

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@router.get("/health")
async def agent_health(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    keys = await get_ai_keys(db)
    return {
        "groq_configured": keys["groq_configured"],
        "gemini_configured": keys["gemini_configured"],
        "tools": len(__import__('app.services.ai_agent_service', fromlist=['TOOLS']).TOOLS),
        "models": ["groq", "gemini"],
    }
