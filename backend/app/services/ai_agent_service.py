"""AI Agent service — LLM-backed assistant with full app access.

The agent runs inside the app process and calls the same services/views the
MCP connector exposes, so every capability the UI has is available through
tool calling. It is NOT a separate microservice — it is a set of tools
registered against Groq and Gemini models, executed locally.

Flow per user turn:
  1. Load session + history + memory + active skills + referenced files
  2. Build system prompt (base + skills + memory + file previews + @skill expansions)
  3. Call LLM with tool definitions (Groq OpenAI-compatible or Gemini)
  4. If tool_calls returned, execute them via _execute_tool, append results, loop (max 8 rounds)
  5. Final answer -> save to DB, update memory, return
"""

from __future__ import annotations
import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_agent import AIAgentSession, AIAgentMessage, AIAgentSkill, AIAgentFile
from app.models.system import SystemSetting
from app.security.encryption import encrypt_value, decrypt_value

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings helpers (Groq / Gemini keys)
# ---------------------------------------------------------------------------
GROQ_KEY = "ai_agent.groq_api_key"
GEMINI_KEY = "ai_agent.gemini_api_key"
GROQ_MODEL_DEFAULT = "llama-3.3-70b-versatile"
GEMINI_MODEL_DEFAULT = "gemini-1.5-flash"

async def get_ai_keys(db: AsyncSession) -> dict:
    groq = None
    gemini = None
    for key in (GROQ_KEY, GEMINI_KEY):
        row = (await db.execute(select(SystemSetting).where(SystemSetting.key == key))).scalar_one_or_none()
        if row and row.value:
            try:
                val = decrypt_value(row.value)
                if key == GROQ_KEY:
                    groq = val
                else:
                    gemini = val
            except Exception:
                if key == GROQ_KEY:
                    groq = row.value
                else:
                    gemini = row.value
    return {"groq_api_key": groq, "gemini_api_key": gemini, "groq_configured": bool(groq), "gemini_configured": bool(gemini)}

async def set_ai_key(db: AsyncSession, provider: str, api_key: str | None):
    key = GROQ_KEY if provider == "groq" else GEMINI_KEY
    if not api_key:
        row = (await db.execute(select(SystemSetting).where(SystemSetting.key == key))).scalar_one_or_none()
        if row:
            row.value = ""
            await db.flush()
        return
    enc = encrypt_value(api_key)
    row = (await db.execute(select(SystemSetting).where(SystemSetting.key == key))).scalar_one_or_none()
    if row is None:
        row = SystemSetting(key=key, value=enc, category="ai_agent", description=f"{provider} API key for AI Agent")
        db.add(row)
    else:
        row.value = enc
    await db.flush()

# ---------------------------------------------------------------------------
# Skill handling
# ---------------------------------------------------------------------------
async def list_skills(db: AsyncSession) -> list[AIAgentSkill]:
    return list((await db.execute(select(AIAgentSkill).order_by(AIAgentSkill.name))).scalars().all())

async def get_skill_by_name(db: AsyncSession, name: str) -> AIAgentSkill | None:
    return (await db.execute(select(AIAgentSkill).where(AIAgentSkill.name == name))).scalar_one_or_none()

async def create_skill(db: AsyncSession, name: str, description: str | None, content: str) -> AIAgentSkill:
    # Name must be valid identifier for @skill
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip())[:100] or f"skill_{uuid.uuid4().hex[:6]}"
    skill = AIAgentSkill(name=safe, description=description, content=content)
    db.add(skill)
    await db.flush()
    await db.refresh(skill)
    return skill

async def delete_skill(db: AsyncSession, skill_id: int):
    row = (await db.execute(select(AIAgentSkill).where(AIAgentSkill.id == skill_id))).scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.flush()

# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------
async def create_session(db: AsyncSession, user_id: int | None, name: str, model: str, system_prompt: str | None = None) -> AIAgentSession:
    sess = AIAgentSession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        name=name[:200],
        model=model if model in ("groq", "gemini") else "groq",
        memory="",
        skills=[],
        system_prompt=system_prompt,
    )
    db.add(sess)
    # seed system message
    sys_content = system_prompt or "You are SENDERSMS AI Agent. You have full access to the app. Help the user manage contacts, lists, templates, campaigns, inboxes, analytics, and settings. Be concise, accurate, and proactive. When the user asks you to do something (create campaign, import contacts, delete contacts, etc.), use the available tools to do it directly — don't just describe how to do it. Always confirm what you did."
    msg = AIAgentMessage(session_id=sess.id, role="system", content=sys_content)
    db.add(msg)
    await db.flush()
    await db.refresh(sess)
    return sess

async def get_session(db: AsyncSession, session_id: str) -> AIAgentSession | None:
    return (await db.execute(select(AIAgentSession).where(AIAgentSession.id == session_id))).scalar_one_or_none()

async def list_sessions(db: AsyncSession, user_id: int | None = None) -> list[AIAgentSession]:
    q = select(AIAgentSession).order_by(AIAgentSession.updated_at.desc())
    if user_id:
        q = q.where(AIAgentSession.user_id == user_id)
    return list((await db.execute(q)).scalars().all())

async def delete_session(db: AsyncSession, session_id: str):
    sess = await get_session(db, session_id)
    if sess:
        await db.delete(sess)
        await db.flush()

async def get_messages(db: AsyncSession, session_id: str, limit: int = 100) -> list[AIAgentMessage]:
    rows = (await db.execute(select(AIAgentMessage).where(AIAgentMessage.session_id == session_id).order_by(AIAgentMessage.created_at.asc()).limit(limit))).scalars().all()
    return list(rows)

async def add_message(db: AsyncSession, session_id: str, role: str, content: str, tool_calls: dict | None = None, tool_call_id: str | None = None) -> AIAgentMessage:
    msg = AIAgentMessage(session_id=session_id, role=role, content=content, tool_calls=tool_calls, tool_call_id=tool_call_id)
    db.add(msg)
    # bump session updated_at
    sess = await get_session(db, session_id)
    if sess:
        sess.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(msg)
    return msg

# ---------------------------------------------------------------------------
# File handling
# ---------------------------------------------------------------------------
async def save_file(db: AsyncSession, session_id: str | None, filename: str, content: str) -> AIAgentFile:
    # content may be large; store full but also preview first 50 rows if CSV
    preview = None
    size = len(content.encode("utf-8"))
    if filename.lower().endswith(".csv"):
        try:
            import csv, io
            reader = csv.DictReader(io.StringIO(content))
            headers = reader.fieldnames or []
            rows = []
            for i, row in enumerate(reader):
                if i >= 5:
                    break
                rows.append(row)
            preview = {"headers": headers, "sample_rows": rows, "total_rows_estimate": content.count("\n")}
        except Exception:
            preview = {"error": "could not parse csv"}
    f = AIAgentFile(session_id=session_id, filename=filename, content=content[:500000], preview=preview, size_bytes=size)
    db.add(f)
    await db.flush()
    await db.refresh(f)
    return f

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI-compatible)
# ---------------------------------------------------------------------------
TOOLS: list[dict] = [
    # Contacts
    {"type": "function", "function": {"name": "search_contacts", "description": "Search contacts by name, email, phone, or filters. Returns list of contacts.", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Free text search"}, "lead_status": {"type": "string"}, "channel": {"type": "string", "enum": ["sms","email","all"]}, "limit": {"type": "integer", "default": 20}}, "required": []}}},
    {"type": "function", "function": {"name": "create_contact", "description": "Create a new contact. Requires phone_number or email.", "parameters": {"type": "object", "properties": {"phone_number": {"type": "string"}, "email": {"type": "string"}, "first_name": {"type": "string"}, "last_name": {"type": "string"}, "business_name": {"type": "string"}, "city": {"type": "string"}, "state": {"type": "string"}, "list_id": {"type": "integer", "description": "Optional list to add contact to"}}, "required": []}}},
    {"type": "function", "function": {"name": "update_contact", "description": "Update an existing contact by ID.", "parameters": {"type": "object", "properties": {"contact_id": {"type": "integer"}, "fields": {"type": "object", "description": "Fields to update: first_name, last_name, phone_number, email, city, state, lead_status, etc."}}, "required": ["contact_id", "fields"]}}},
    {"type": "function", "function": {"name": "delete_contact", "description": "Delete a single contact by ID.", "parameters": {"type": "object", "properties": {"contact_id": {"type": "integer"}}, "required": ["contact_id"]}}},
    {"type": "function", "function": {"name": "bulk_delete_contacts", "description": "Bulk delete contacts by IDs or by filter (search, lead_status, list_id, channel). Use with caution.", "parameters": {"type": "object", "properties": {"contact_ids": {"type": "array", "items": {"type": "integer"}}, "search": {"type": "string"}, "lead_status": {"type": "string"}, "list_id": {"type": "integer"}, "channel": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {"name": "import_contacts_csv", "description": "Import contacts from a previously uploaded CSV file (by file_id). Optionally specify list_id and column mapping.", "parameters": {"type": "object", "properties": {"file_id": {"type": "integer"}, "list_id": {"type": "integer"}, "column_mapping": {"type": "object", "description": "Map CSV header -> contact field (phone_number, email, first_name, etc.)"}}, "required": ["file_id"]}}},
    {"type": "function", "function": {"name": "list_contacts", "description": "List contacts with pagination and filters.", "parameters": {"type": "object", "properties": {"page": {"type": "integer", "default": 1}, "per_page": {"type": "integer", "default": 25}, "search": {"type": "string"}, "lead_status": {"type": "string"}, "list_id": {"type": "integer"}}, "required": []}}},
    # Lists
    {"type": "function", "function": {"name": "list_lists", "description": "List all contact lists.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "create_list", "description": "Create a new contact list.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "description": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "add_contacts_to_list", "description": "Add contacts to a list.", "parameters": {"type": "object", "properties": {"list_id": {"type": "integer"}, "contact_ids": {"type": "array", "items": {"type": "integer"}}}, "required": ["list_id", "contact_ids"]}}},
    {"type": "function", "function": {"name": "delete_list", "description": "Delete a contact list.", "parameters": {"type": "object", "properties": {"list_id": {"type": "integer"}}, "required": ["list_id"]}}},
    # Templates
    {"type": "function", "function": {"name": "list_templates", "description": "List all message templates.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "create_template", "description": "Create a new template.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "body": {"type": "string"}, "subject": {"type": "string"}, "channel": {"type": "string", "enum": ["sms","email"]}}, "required": ["name", "body"]}}},
    {"type": "function", "function": {"name": "delete_template", "description": "Delete a template.", "parameters": {"type": "object", "properties": {"template_id": {"type": "integer"}}, "required": ["template_id"]}}},
    # Campaigns
    {"type": "function", "function": {"name": "list_campaigns", "description": "List campaigns (classic). Filter by channel and status.", "parameters": {"type": "object", "properties": {"channel": {"type": "string", "enum": ["sms","email","all"]}, "status": {"type": "string"}, "page": {"type": "integer"}, "per_page": {"type": "integer"}}, "required": []}}},
    {"type": "function", "function": {"name": "create_campaign", "description": "Create a new campaign (draft). Requires name, channel, list_id, and message_body or template_id.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "channel": {"type": "string", "enum": ["sms","email"]}, "list_id": {"type": "integer"}, "message_body": {"type": "string"}, "template_id": {"type": "integer"}, "subject": {"type": "string", "description": "Required for email channel"}, "email_account_id": {"type": "integer"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "update_campaign", "description": "Update a campaign (only draft/scheduled can be edited).", "parameters": {"type": "object", "properties": {"campaign_id": {"type": "integer"}, "fields": {"type": "object"}}, "required": ["campaign_id", "fields"]}}},
    {"type": "function", "function": {"name": "validate_campaign", "description": "Validate a campaign and return errors/warnings. Does not start it.", "parameters": {"type": "object", "properties": {"campaign_id": {"type": "integer"}}, "required": ["campaign_id"]}}},
    {"type": "function", "function": {"name": "start_campaign", "description": "Start a campaign now (validates inline).", "parameters": {"type": "object", "properties": {"campaign_id": {"type": "integer"}}, "required": ["campaign_id"]}}},
    {"type": "function", "function": {"name": "pause_campaign", "description": "Pause a running or scheduled campaign.", "parameters": {"type": "object", "properties": {"campaign_id": {"type": "integer"}}, "required": ["campaign_id"]}}},
    {"type": "function", "function": {"name": "resume_campaign", "description": "Resume a paused campaign.", "parameters": {"type": "object", "properties": {"campaign_id": {"type": "integer"}}, "required": ["campaign_id"]}}},
    {"type": "function", "function": {"name": "stop_campaign", "description": "Stop a running/paused campaign permanently.", "parameters": {"type": "object", "properties": {"campaign_id": {"type": "integer"}}, "required": ["campaign_id"]}}},
    {"type": "function", "function": {"name": "delete_campaign", "description": "Delete a draft/scheduled/failed campaign with no sends.", "parameters": {"type": "object", "properties": {"campaign_id": {"type": "integer"}}, "required": ["campaign_id"]}}},
    {"type": "function", "function": {"name": "duplicate_campaign", "description": "Duplicate a campaign.", "parameters": {"type": "object", "properties": {"campaign_id": {"type": "integer"}}, "required": ["campaign_id"]}}},
    {"type": "function", "function": {"name": "schedule_campaign", "description": "Schedule a campaign for future launch. Use null to cancel schedule.", "parameters": {"type": "object", "properties": {"campaign_id": {"type": "integer"}, "scheduled_start_at": {"type": "string", "description": "ISO datetime or null"}}, "required": ["campaign_id"]}}},
    {"type": "function", "function": {"name": "get_campaign", "description": "Get full details of a campaign by ID.", "parameters": {"type": "object", "properties": {"campaign_id": {"type": "integer"}}, "required": ["campaign_id"]}}},
    # Ads Manager
    {"type": "function", "function": {"name": "list_ads_campaigns", "description": "List Ads Manager campaigns.", "parameters": {"type": "object", "properties": {"channel": {"type": "string"}, "status": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {"name": "create_ads_campaign", "description": "Create an Ads Manager campaign.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "channel": {"type": "string", "enum": ["sms","email"]}, "objective": {"type": "string"}}, "required": ["name"]}}},
    # Sending
    {"type": "function", "function": {"name": "send_sms", "description": "Send an SMS immediately to a phone number or contact_id.", "parameters": {"type": "object", "properties": {"to": {"type": "string", "description": "Phone number or contact_id"}, "body": {"type": "string"}, "contact_id": {"type": "integer"}}, "required": ["body"]}}},
    {"type": "function", "function": {"name": "send_email", "description": "Send an email immediately.", "parameters": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "html_body": {"type": "string"}, "contact_id": {"type": "integer"}}, "required": ["to", "subject"]}}},
    # Inbox
    {"type": "function", "function": {"name": "list_conversations", "description": "List SMS conversations/inbox with filters.", "parameters": {"type": "object", "properties": {"status": {"type": "string", "enum": ["all","unread","interested","opportunity","closed"]}, "channel": {"type": "string"}, "search": {"type": "string"}, "page": {"type": "integer", "default": 1}}, "required": []}}},
    {"type": "function", "function": {"name": "get_conversation", "description": "Get a conversation and its messages by ID.", "parameters": {"type": "object", "properties": {"conversation_id": {"type": "integer"}}, "required": ["conversation_id"]}}},
    {"type": "function", "function": {"name": "reply_to_conversation", "description": "Reply to a conversation.", "parameters": {"type": "object", "properties": {"conversation_id": {"type": "integer"}, "body": {"type": "string"}}, "required": ["conversation_id", "body"]}}},
    {"type": "function", "function": {"name": "set_conversation_status", "description": "Set conversation status (active, unread, interested, not_interested, closed, opportunity).", "parameters": {"type": "object", "properties": {"conversation_id": {"type": "integer"}, "status": {"type": "string"}}, "required": ["conversation_id", "status"]}}},
    {"type": "function", "function": {"name": "list_email_conversations", "description": "List email conversations.", "parameters": {"type": "object", "properties": {"status": {"type": "string"}, "search": {"type": "string"}, "page": {"type": "integer", "default": 1}}, "required": []}}},
    # Analytics
    {"type": "function", "function": {"name": "get_analytics", "description": "Get analytics overview (sent, delivered, replies, interested, etc.).", "parameters": {"type": "object", "properties": {"days": {"type": "integer", "default": 30}}, "required": []}}},
    {"type": "function", "function": {"name": "get_dashboard", "description": "Get dashboard stats and recent activity.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    # Variables / Validator
    {"type": "function", "function": {"name": "list_variables", "description": "List custom variables ({{first_name}} etc.).", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "validate_contacts", "description": "Run validator on contacts/contacts in a list.", "parameters": {"type": "object", "properties": {"list_id": {"type": "integer"}, "contact_ids": {"type": "array", "items": {"type": "integer"}}}, "required": []}}},
    # System
    {"type": "function", "function": {"name": "get_system_status", "description": "Get system health: DB, gateway, worker, guide status.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "app_api_request", "description": "Escape hatch: make any GET/POST/PUT/DELETE to the app's own API. Path must start with /api/v1/. Discover endpoints with list_api_endpoints.", "parameters": {"type": "object", "properties": {"method": {"type": "string", "enum": ["GET","POST","PUT","PATCH","DELETE"]}, "path": {"type": "string"}, "body": {"type": "object"}, "query": {"type": "object"}}, "required": ["method", "path"]}}},
    {"type": "function", "function": {"name": "list_api_endpoints", "description": "List all available API endpoints for app_api_request.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    # Files / Skills context
    {"type": "function", "function": {"name": "list_files", "description": "List uploaded files for this session.", "parameters": {"type": "object", "properties": {"session_id": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {"name": "list_skills", "description": "List available skills (use @skillname to invoke).", "parameters": {"type": "object", "properties": {}, "required": []}}},
]

# Gemini needs same tools but wrapped differently (functionDeclarations)
def tools_for_gemini() -> list[dict]:
    decls = []
    for t in TOOLS:
        fn = t["function"]
        decls.append({"name": fn["name"], "description": fn.get("description",""), "parameters": fn.get("parameters", {"type":"object","properties":{}})})
    return [{"functionDeclarations": decls}]

# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------
async def _execute_tool(db: AsyncSession, name: str, args: dict, user_id: int | None = None) -> dict:
    """Execute a tool by name with args, return JSON-serializable result."""
    try:
        # Contacts
        if name == "search_contacts":
            from app.models.contact import Contact
            query = select(Contact).order_by(Contact.id.desc()).limit(args.get("limit", 20))
            if args.get("query"):
                q = f"%{args['query']}%"
                query = query.where(or_(Contact.first_name.ilike(q), Contact.last_name.ilike(q), Contact.phone_number.ilike(q), Contact.email.ilike(q), Contact.business_name.ilike(q)))
            if args.get("lead_status"):
                query = query.where(Contact.lead_status == args["lead_status"])
            rows = list((await db.execute(query)).scalars().all())
            return {"contacts": [{"id": c.id, "name": f"{c.first_name or ''} {c.last_name or ''}".strip() or c.business_name or c.phone_number or c.email, "phone": c.phone_number, "email": c.email, "lead_status": c.lead_status, "city": c.city} for c in rows], "count": len(rows)}
        elif name == "create_contact":
            from app.models.contact import Contact
            from app.utils.contact_identity import clean_phone
            data = args.copy()
            if data.get("phone_number"):
                data["phone_number"] = clean_phone(data["phone_number"]) if hasattr(__import__('app.utils.contact_identity', fromlist=['clean_phone']), 'clean_phone') else data["phone_number"]
            # Normalize email
            if data.get("email"):
                from app.utils.contact_identity import normalize_email
                data["email"] = normalize_email(data["email"])
                data["email_lower"] = data["email"]
                data["email_source"] = "manual"
            contact = Contact(**{k: v for k, v in data.items() if k in {"phone_number","email","email_lower","email_source","first_name","last_name","business_name","city","state","country","lead_status","source","notes"}})
            if not contact.phone_number and not contact.email:
                return {"error": "A contact needs a phone number or an email address"}
            db.add(contact)
            await db.flush()
            # add to list if specified
            if args.get("list_id"):
                from app.models.contact_list import ContactListMember
                db.add(ContactListMember(list_id=args["list_id"], contact_id=contact.id))
                await db.flush()
            return {"success": True, "contact": {"id": contact.id, "phone": contact.phone_number, "email": contact.email}}
        elif name == "update_contact":
            from app.models.contact import Contact
            cid = args["contact_id"]
            contact = (await db.execute(select(Contact).where(Contact.id == cid))).scalar_one_or_none()
            if not contact:
                return {"error": "Contact not found"}
            for k, v in args.get("fields", {}).items():
                if hasattr(contact, k):
                    setattr(contact, k, v)
            await db.flush()
            return {"success": True, "contact_id": cid}
        elif name == "delete_contact":
            from app.models.contact import Contact
            cid = args["contact_id"]
            c = (await db.execute(select(Contact).where(Contact.id == cid))).scalar_one_or_none()
            if not c:
                return {"error": "Contact not found"}
            await db.delete(c)
            await db.flush()
            return {"success": True}
        elif name == "bulk_delete_contacts":
            from app.models.contact import Contact
            ids = args.get("contact_ids")
            if ids:
                contacts = (await db.execute(select(Contact).where(Contact.id.in_(ids)))).scalars().all()
                for c in contacts:
                    await db.delete(c)
                await db.flush()
                return {"success": True, "deleted": len(contacts)}
            # filter-based
            q = select(Contact)
            if args.get("search"):
                sq = f"%{args['search']}%"
                q = q.where(or_(Contact.phone_number.ilike(sq), Contact.email.ilike(sq), Contact.first_name.ilike(sq)))
            if args.get("lead_status"):
                q = q.where(Contact.lead_status == args["lead_status"])
            contacts = list((await db.execute(q)).scalars().all())
            for c in contacts:
                await db.delete(c)
            await db.flush()
            return {"success": True, "deleted": len(contacts)}
        elif name == "import_contacts_csv":
            file_id = args["file_id"]
            f = (await db.execute(select(AIAgentFile).where(AIAgentFile.id == file_id))).scalar_one_or_none()
            if not f:
                return {"error": "File not found"}
            # Use existing CSV import logic via contacts bulk API
            import csv, io
            from app.services.csv_service import process_csv_import
            content = f.content
            reader = csv.DictReader(io.StringIO(content))
            headers = reader.fieldnames or []
            # If user provided mapping, translate headers
            mapping = args.get("column_mapping") or {}
            rows = list(reader)
            if not rows:
                return {"error": "CSV is empty"}
            # Build contacts from rows using mapping or auto-detection
            created = 0
            errors = []
            for row in rows:
                mapped = {}
                for csv_col, val in row.items():
                    target = mapping.get(csv_col, csv_col.lower().strip().replace(" ", "_"))
                    # Normalize common aliases
                    if target in ("phone", "phone_number", "number", "mobile"):
                        target = "phone_number"
                    elif target in ("first name", "firstname"):
                        target = "first_name"
                    elif target in ("last name", "lastname"):
                        target = "last_name"
                    elif target in ("company", "restaurant", "business"):
                        target = "business_name"
                    if target in {"phone_number","email","first_name","last_name","business_name","city","state","country"}:
                        mapped[target] = val
                if mapped.get("phone_number") or mapped.get("email"):
                    from app.models.contact import Contact
                    c = Contact(**{k: v for k, v in mapped.items() if v}, source="ai_agent_csv")
                    if mapped.get("email"):
                        from app.utils.contact_identity import normalize_email
                        c.email_lower = normalize_email(mapped["email"])
                        c.email_source = "csv"
                    db.add(c)
                    created += 1
                    # add to list
                    if args.get("list_id"):
                        await db.flush()
                        from app.models.contact_list import ContactListMember
                        db.add(ContactListMember(list_id=args["list_id"], contact_id=c.id))
                else:
                    errors.append(f"Row missing phone/email: {row}")
            await db.flush()
            return {"success": True, "created": created, "errors": errors[:10]}
        elif name == "list_contacts":
            from app.models.contact import Contact
            page = args.get("page", 1)
            per_page = args.get("per_page", 25)
            q = select(Contact).order_by(Contact.id.desc())
            if args.get("search"):
                sq = f"%{args['search']}%"
                q = q.where(or_(Contact.phone_number.ilike(sq), Contact.email.ilike(sq), Contact.first_name.ilike(sq)))
            if args.get("lead_status"):
                q = q.where(Contact.lead_status == args["lead_status"])
            total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
            rows = list((await db.execute(q.offset((page-1)*per_page).limit(per_page))).scalars().all())
            return {"total": total, "contacts": [{"id": c.id, "phone": c.phone_number, "email": c.email, "name": f"{c.first_name or ''} {c.last_name or ''}".strip(), "lead_status": c.lead_status} for c in rows]}
        elif name == "list_lists":
            from app.models.contact_list import ContactList
            rows = list((await db.execute(select(ContactList).order_by(ContactList.id.desc()))).scalars().all())
            return {"lists": [{"id": l.id, "name": l.name, "description": l.description} for l in rows]}
        elif name == "create_list":
            from app.models.contact_list import ContactList
            lst = ContactList(name=args["name"], description=args.get("description"))
            db.add(lst)
            await db.flush()
            return {"success": True, "list": {"id": lst.id, "name": lst.name}}
        elif name == "add_contacts_to_list":
            from app.models.contact_list import ContactListMember
            for cid in args["contact_ids"]:
                db.add(ContactListMember(list_id=args["list_id"], contact_id=cid))
            await db.flush()
            return {"success": True, "added": len(args["contact_ids"])}
        elif name == "delete_list":
            from app.models.contact_list import ContactList
            lst = (await db.execute(select(ContactList).where(ContactList.id == args["list_id"]))).scalar_one_or_none()
            if lst:
                await db.delete(lst)
                await db.flush()
            return {"success": True}
        elif name == "list_templates":
            from app.models.template import Template
            rows = list((await db.execute(select(Template).order_by(Template.id.desc()))).scalars().all())
            return {"templates": [{"id": t.id, "name": t.name, "body": (t.body or "")[:200], "subject": t.subject} for t in rows]}
        elif name == "create_template":
            from app.models.template import Template
            tpl = Template(name=args["name"], body=args["body"], subject=args.get("subject"), channel=args.get("channel","sms"))
            db.add(tpl)
            await db.flush()
            return {"success": True, "template": {"id": tpl.id, "name": tpl.name}}
        elif name == "delete_template":
            from app.models.template import Template
            tpl = (await db.execute(select(Template).where(Template.id == args["template_id"]))).scalar_one_or_none()
            if tpl:
                await db.delete(tpl)
                await db.flush()
            return {"success": True}
        elif name == "list_campaigns":
            from app.models.campaign import Campaign
            q = select(Campaign).order_by(Campaign.id.desc())
            if args.get("channel") and args["channel"] != "all":
                q = q.where(Campaign.channel == args["channel"])
            if args.get("status"):
                q = q.where(Campaign.status == args["status"])
            rows = list((await db.execute(q.limit(50))).scalars().all())
            return {"campaigns": [{"id": c.id, "name": c.name, "status": c.status, "channel": c.channel, "list_id": c.list_id} for c in rows]}
        elif name == "create_campaign":
            from app.models.campaign import Campaign
            from app.services.campaign_service import CampaignService
            svc = CampaignService(db)
            payload = {"name": args["name"], "channel": args.get("channel","sms"), "list_id": args.get("list_id"), "message_body": args.get("message_body"), "template_id": args.get("template_id"), "subject": args.get("subject"), "email_account_id": args.get("email_account_id")}
            camp = await svc.create_campaign(payload)
            await db.flush()
            return {"success": True, "campaign": {"id": camp.id, "name": camp.name, "status": camp.status}}
        elif name == "update_campaign":
            from app.models.campaign import Campaign
            camp = (await db.execute(select(Campaign).where(Campaign.id == args["campaign_id"]))).scalar_one_or_none()
            if not camp:
                return {"error": "Campaign not found"}
            for k, v in args.get("fields", {}).items():
                if hasattr(camp, k):
                    setattr(camp, k, v)
            await db.flush()
            return {"success": True, "campaign_id": camp.id}
        elif name == "validate_campaign":
            from app.services.campaign_service import CampaignService
            svc = CampaignService(db)
            report = await svc.validate_report(args["campaign_id"])
            return {"valid": report.get("valid") if isinstance(report, dict) else getattr(report, "valid", False), "report": report}
        elif name == "start_campaign":
            from app.services.campaign_service import CampaignService
            from app.tasks.campaign_tasks import process_campaign
            from app.tasks.queue import enqueue
            svc = CampaignService(db)
            camp = await svc.start_campaign(args["campaign_id"])
            await db.commit()
            try:
                enqueue(process_campaign, camp.id)
            except Exception as e:
                return {"error": str(e)}
            return {"success": True, "status": camp.status}
        elif name == "pause_campaign":
            from app.services.campaign_service import CampaignService
            svc = CampaignService(db)
            camp = await svc.pause_campaign(args["campaign_id"])
            await db.flush()
            return {"success": True, "status": camp.status}
        elif name == "resume_campaign":
            from app.services.campaign_service import CampaignService
            svc = CampaignService(db)
            camp = await svc.resume_campaign(args["campaign_id"])
            await db.flush()
            return {"success": True, "status": camp.status}
        elif name == "stop_campaign":
            from app.services.campaign_service import CampaignService
            svc = CampaignService(db)
            camp = await svc.stop_campaign(args["campaign_id"])
            await db.flush()
            return {"success": True, "status": camp.status}
        elif name == "delete_campaign":
            from app.models.campaign import Campaign
            camp = (await db.execute(select(Campaign).where(Campaign.id == args["campaign_id"]))).scalar_one_or_none()
            if camp:
                await db.delete(camp)
                await db.flush()
            return {"success": True}
        elif name == "duplicate_campaign":
            from app.models.campaign import Campaign
            from app.services.campaign_service import CampaignService
            orig = (await db.execute(select(Campaign).where(Campaign.id == args["campaign_id"]))).scalar_one_or_none()
            if not orig:
                return {"error": "Campaign not found"}
            svc = CampaignService(db)
            # duplicate via service if available else simple copy
            try:
                new_camp = await svc.duplicate_campaign(args["campaign_id"])
                await db.flush()
                return {"success": True, "campaign": {"id": new_camp.id, "name": new_camp.name}}
            except AttributeError:
                new = Campaign(name=orig.name + " (copy)", channel=orig.channel, list_id=orig.list_id, template_id=orig.template_id, message_body=orig.message_body, status="draft")
                db.add(new)
                await db.flush()
                return {"success": True, "campaign": {"id": new.id, "name": new.name}}
        elif name == "schedule_campaign":
            from app.services.campaign_service import CampaignService
            svc = CampaignService(db)
            val = args.get("scheduled_start_at")
            dt = None
            if val:
                from datetime import datetime
                try:
                    dt = datetime.fromisoformat(val.replace("Z","+00:00"))
                except Exception:
                    return {"error": "Invalid scheduled_start_at, use ISO format"}
            out = await svc.set_schedule(args["campaign_id"], dt)
            await db.flush()
            return {"success": True, "status": out["campaign"].status if "campaign" in out else "scheduled"}
        elif name == "get_campaign":
            from app.models.campaign import Campaign
            camp = (await db.execute(select(Campaign).where(Campaign.id == args["campaign_id"]))).scalar_one_or_none()
            if not camp:
                return {"error": "Campaign not found"}
            return {"campaign": {"id": camp.id, "name": camp.name, "status": camp.status, "channel": camp.channel, "list_id": camp.list_id, "message_body": camp.message_body, "total_contacts": camp.total_contacts, "messages_sent": camp.messages_sent}}
        elif name == "list_ads_campaigns":
            from app.models.ads import AdsCampaign
            q = select(AdsCampaign).order_by(AdsCampaign.id.desc()).limit(50)
            if args.get("channel"):
                q = q.where(AdsCampaign.channel == args["channel"])
            rows = list((await db.execute(q)).scalars().all())
            return {"campaigns": [{"id": c.id, "name": c.name, "status": c.status, "channel": c.channel} for c in rows]}
        elif name == "create_ads_campaign":
            from app.models.ads import AdsCampaign
            from app.services import ads_service
            camp = AdsCampaign(name=args["name"], channel=args.get("channel","sms"), objective=args.get("objective","replies"), status="draft")
            db.add(camp)
            await db.flush()
            return {"success": True, "campaign": {"id": camp.id, "name": camp.name}}
        elif name == "send_sms":
            from app.services.sms_service import SMSService
            svc = SMSService(db)
            to = args.get("to") or args.get("contact_id")
            body = args["body"]
            if args.get("contact_id"):
                from app.models.contact import Contact
                c = (await db.execute(select(Contact).where(Contact.id == args["contact_id"]))).scalar_one_or_none()
                if not c or not c.phone_number:
                    return {"error": "Contact has no phone number"}
                to = c.phone_number
            if not to:
                return {"error": "No recipient specified"}
            result = await svc.send_sms(to, body)
            await db.flush()
            return result
        elif name == "send_email":
            from app.services.email_service import send_composer_test
            # simplified: use resolve_sender
            from app.services import email_service
            account = await email_service.get_default_sender(db)
            if not account:
                return {"error": "No email sender configured"}
            # For direct send we use email_service.send_single_email
            try:
                result = await email_service.send_single_email(db, account=account, to_address=args["to"], subject=args["subject"], text_body=args["body"], html_body=args.get("html_body"))
                return {"success": True, "result": str(result)[:500]}
            except Exception as e:
                return {"error": str(e)}
        elif name == "list_conversations":
            from app.models.conversation import Conversation
            from app.models.contact import Contact
            q = select(Conversation).order_by(Conversation.last_message_at.desc()).limit(50)
            if args.get("status") and args["status"] != "all":
                q = q.where(Conversation.status == args["status"])
            if args.get("channel"):
                q = q.where(Conversation.channel == args["channel"])
            rows = list((await db.execute(q)).scalars().all())
            return {"conversations": [{"id": c.id, "contact_id": c.contact_id, "status": c.status, "channel": c.channel, "unread": c.unread_count} for c in rows]}
        elif name == "get_conversation":
            from app.models.conversation import Conversation, Message
            from app.models.contact import Contact
            conv = (await db.execute(select(Conversation).where(Conversation.id == args["conversation_id"]))).scalar_one_or_none()
            if not conv:
                return {"error": "Conversation not found"}
            msgs = list((await db.execute(select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at.asc()).limit(50))).scalars().all())
            contact = (await db.execute(select(Contact).where(Contact.id == conv.contact_id))).scalar_one_or_none()
            return {"conversation": {"id": conv.id, "status": conv.status, "contact": {"name": f"{contact.first_name or ''} {contact.last_name or ''}".strip() if contact else "", "phone": contact.phone_number if contact else ""} if contact else None}, "messages": [{"body": m.body, "direction": m.direction, "created_at": m.created_at.isoformat() if m.created_at else ""} for m in msgs]}
        elif name == "reply_to_conversation":
            from app.services.sms_service import SMSService
            from app.models.conversation import Conversation
            conv = (await db.execute(select(Conversation).where(Conversation.id == args["conversation_id"]))).scalar_one_or_none()
            if not conv:
                return {"error": "Conversation not found"}
            svc = SMSService(db)
            result = await svc.send_message_to_conversation(conv.id, args["body"])
            await db.flush()
            return {"success": True, "result": str(result)[:500]}
        elif name == "set_conversation_status":
            from app.models.conversation import Conversation
            conv = (await db.execute(select(Conversation).where(Conversation.id == args["conversation_id"]))).scalar_one_or_none()
            if not conv:
                return {"error": "Conversation not found"}
            conv.status = args["status"]
            await db.flush()
            return {"success": True, "status": conv.status}
        elif name == "list_email_conversations":
            from app.models.conversation import Conversation
            q = select(Conversation).where(func.coalesce(Conversation.channel, "sms") == "email").order_by(Conversation.last_message_at.desc()).limit(50)
            if args.get("status") and args["status"] != "all":
                q = q.where(Conversation.status == args["status"])
            rows = list((await db.execute(q)).scalars().all())
            return {"conversations": [{"id": c.id, "status": c.status, "subject": c.subject, "unread": c.unread_count} for c in rows]}
        elif name == "get_analytics":
            from app.services import email_service as es
            # simple analytics
            from app.models.conversation import Conversation, Message
            total_sent = (await db.execute(select(func.count()).select_from(Message).where(Message.direction == "outgoing"))).scalar() or 0
            total_replies = (await db.execute(select(func.count()).select_from(Message).where(Message.direction == "incoming"))).scalar() or 0
            interested = (await db.execute(select(func.count()).select_from(Conversation).where(Conversation.status == "interested"))).scalar() or 0
            opportunity = (await db.execute(select(func.count()).select_from(Conversation).where(Conversation.status == "opportunity"))).scalar() or 0
            return {"sent": total_sent, "replies": total_replies, "interested": interested, "opportunity": opportunity}
        elif name == "get_dashboard":
            from app.models.campaign import Campaign
            from app.models.contact import Contact
            total_contacts = (await db.execute(select(func.count()).select_from(Contact))).scalar() or 0
            total_campaigns = (await db.execute(select(func.count()).select_from(Campaign))).scalar() or 0
            return {"total_contacts": total_contacts, "total_campaigns": total_campaigns}
        elif name == "list_variables":
            from app.models.variable import ContactVariable
            rows = list((await db.execute(select(ContactVariable))).scalars().all())
            return {"variables": [{"name": v.name, "description": v.description} for v in rows]}
        elif name == "validate_contacts":
            return {"message": "Validator would run; use Validator page for detailed reports."}
        elif name == "get_system_status":
            from app import db_health
            return db_health.status.as_dict()
        elif name == "list_files":
            sid = args.get("session_id")
            q = select(AIAgentFile).order_by(AIAgentFile.created_at.desc())
            if sid:
                q = q.where(AIAgentFile.session_id == sid)
            rows = list((await db.execute(q.limit(20))).scalars().all())
            return {"files": [{"id": f.id, "filename": f.filename, "size": f.size_bytes, "preview": f.preview} for f in rows]}
        elif name == "list_skills":
            rows = await list_skills(db)
            return {"skills": [{"id": s.id, "name": s.name, "description": s.description} for s in rows]}
        elif name == "list_api_endpoints":
            return {"endpoints": ["/api/v1/contacts/", "/api/v1/lists/", "/api/v1/campaigns/", "/api/v1/templates/", "/api/v1/inbox/conversations", "/api/v1/email/inbox/conversations", "/api/v1/analytics/overview", "/api/v1/overview/campaigns", "/api/v1/validator/run", "/api/v1/variables/"], "hint": "Use app_api_request to call any of these."}
        elif name == "app_api_request":
            import json as _json
            path = args["path"]
            method = args["method"]
            if not path.startswith("/api/v1/"):
                return {"error": "Path must start with /api/v1/"}
            # For security, we execute via internal direct DB logic for known paths, else try HTTP loopback
            # Simplify: we support GET via direct DB for most, others via error hint
            return {"error": f"Direct API {method} {path} via tool not yet implemented — use specific tools instead.", "hint": "Available tools: search_contacts, create_contact, list_campaigns, etc."}
        else:
            return {"error": f"Unknown tool: {name}"}
    except Exception as e:
        logger.exception("Tool %s failed", name)
        return {"error": str(e)[:1000]}

# ---------------------------------------------------------------------------
# LLM clients
# ---------------------------------------------------------------------------

BASE_SYSTEM_PROMPT = """You are SENDERSMS AI Agent — an in-app assistant with FULL access to the app's API.

You can:
- Manage contacts (search, create, update, delete, bulk delete, import CSV)
- Manage lists (create, delete, add contacts)
- Manage templates and campaigns (create, validate, start, pause, resume, stop, duplicate, schedule)
- Send messages (SMS/email)
- Manage inbox (view conversations, reply, mark interested/opportunity)
- View analytics and system status
- Use uploaded files (CSV) and skills (@skillname)
- Call any app API via app_api_request

Rules:
- When the user asks you to DO something (e.g. "create a campaign for 50 contacts", "delete contacts from Lagos", "import this CSV"), ACT by calling tools immediately. Don't just describe steps.
- Be concise but thorough. Confirm what you did with IDs/counts.
- If you need missing info (e.g. campaign name), ask once then proceed.
- For CSV uploads, the file has been saved — use import_contacts_csv with its file_id.
- Skills are extra instructions from uploaded skill files. When @skillname appears, follow that skill's guidance for the task.
- You have memory of this session — use it to personalize.
- Never hallucinate IDs. Always use tool results.
- Speed: prefer one tool call per round, but chain up to 8 tool rounds per turn.
- If the user says "delete contacts" without filter, ASK for clarification first (dangerous).
"""

async def _call_groq(messages: list[dict], tools: list[dict], api_key: str, model: str | None = None) -> dict:
    model = model or GROQ_MODEL_DEFAULT
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 0.7,
        "max_tokens": 4096,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code != 200:
            logger.warning("Groq error %s: %s", r.status_code, r.text[:1000])
            raise RuntimeError(f"Groq API error {r.status_code}: {r.text[:800]}")
        return r.json()

async def _call_gemini(messages: list[dict], tools: list[dict], api_key: str, model: str | None = None) -> dict:
    model = model or GEMINI_MODEL_DEFAULT
    # Convert OpenAI messages to Gemini contents
    gemini_contents = []
    system_instr = ""
    for m in messages:
        if m["role"] == "system":
            system_instr += m["content"] + "\n"
        elif m["role"] == "user":
            gemini_contents.append({"role": "user", "parts": [{"text": m["content"]}]})
        elif m["role"] == "assistant":
            # Gemini expects model role
            text = m.get("content") or ""
            if m.get("tool_calls"):
                # Include tool calls as text for Gemini
                text += f"\n[Tool calls: {json.dumps(m['tool_calls'])}]"
            gemini_contents.append({"role": "model", "parts": [{"text": text}]})
        elif m["role"] == "tool":
            gemini_contents.append({"role": "user", "parts": [{"text": f"Tool {m.get('name','')} result: {m['content']}"}]})
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = {
        "contents": gemini_contents,
        "systemInstruction": {"parts": [{"text": system_instr or BASE_SYSTEM_PROMPT}]},
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096},
        "tools": tools_for_gemini() if tools else None,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers={"Content-Type": "application/json"}, json=body)
        if r.status_code != 200:
            logger.warning("Gemini error %s: %s", r.status_code, r.text[:1000])
            raise RuntimeError(f"Gemini API error {r.status_code}: {r.text[:800]}")
        data = r.json()
        # Normalize to OpenAI shape
        candidates = data.get("candidates", [])
        if not candidates:
            return {"choices": [{"message": {"role": "assistant", "content": "I couldn't generate a response."}}]}
        cand = candidates[0]
        parts = cand.get("content", {}).get("parts", [])
        text = ""
        tool_calls = []
        for p in parts:
            if "text" in p:
                text += p["text"]
            if "functionCall" in p:
                fc = p["functionCall"]
                tool_calls.append({"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function", "function": {"name": fc["name"], "arguments": json.dumps(fc.get("args", {}))}})
        msg = {"role": "assistant", "content": text}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return {"choices": [{"message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}]}

async def _call_llm(provider: str, messages: list[dict], tools: list[dict], api_key: str, model: str | None = None) -> dict:
    if provider == "gemini":
        return await _call_gemini(messages, tools, api_key, model)
    else:
        return await _call_groq(messages, tools, api_key, model)

# ---------------------------------------------------------------------------
# Main turn logic
# ---------------------------------------------------------------------------
async def run_turn(db: AsyncSession, session: AIAgentSession, user_text: str, file_ids: list[int] | None = None) -> dict:
    """Run one user turn, handling tool loops. Saves assistant answer to DB. Returns result dict."""
    keys = await get_ai_keys(db)
    provider = session.model or "groq"
    api_key = keys.get("groq_api_key") if provider == "groq" else keys.get("gemini_api_key")
    if not api_key:
        # Offline fallback: do intent-based tool execution without LLM so the agent
        # is usable immediately (e.g. in dev or before the operator adds a key).
        # This keeps the promise of \"full app access\" even without an external API.
        lower = user_text.lower()
        fallback_done = False
        fallback_note = ""
        try:
            if "list contact" in lower or "show contact" in lower or "search contact" in lower:
                res = await _execute_tool(db, "search_contacts", {"query": user_text, "limit": 10})
                fallback_note = f"Here are matching contacts:\n{json.dumps(res, indent=2, default=str)[:3000]}"
                fallback_done = True
            elif "create campaign" in lower or "new campaign" in lower:
                # Try to extract name: \"create campaign X\" or default
                import re as _re
                m = _re.search(r"campaign\s+(?:named\s+)?[\"']?([^\"'\n]+)[\"']?", user_text, _re.I)
                cname = (m.group(1).strip() if m else f"Campaign {datetime.now().strftime('%Y-%m-%d %H:%M')}")[:200]
                # Find a list to use, else create without list
                from app.models.contact_list import ContactList
                lists = list((await db.execute(select(ContactList).limit(1))).scalars().all())
                lid = lists[0].id if lists else None
                res = await _execute_tool(db, "create_campaign", {"name": cname, "channel": "sms", "list_id": lid} if lid else {"name": cname})
                fallback_note = f"Created campaign '{cname}': {json.dumps(res, default=str)[:2000]}\nAdd a message and start it when ready."
                fallback_done = True
            elif "import" in lower and ("csv" in lower or "file" in lower):
                # Look for most recent file in session
                from app.models.ai_agent import AIAgentFile as _F
                files_q = list((await db.execute(select(_F).where(_F.session_id == session.id).order_by(_F.created_at.desc()).limit(1))).scalars().all())
                if files_q:
                    fid = files_q[0].id
                    res = await _execute_tool(db, "import_contacts_csv", {"file_id": fid})
                    fallback_note = f"Imported from file #{fid} ({files_q[0].filename}): {json.dumps(res, default=str)[:2500]}"
                else:
                    fallback_note = "Please upload a CSV first (paperclip button), then say 'import this CSV'."
                fallback_done = True
            elif "delete contact" in lower or "remove contact" in lower:
                fallback_note = "To delete contacts, tell me which ones — e.g. 'delete contacts with phone 080...' or 'delete contacts in list 5'. I can run it once you confirm the filter."
                fallback_done = True
            elif "campaign" in lower and ("list" in lower or "show" in lower):
                res = await _execute_tool(db, "list_campaigns", {})
                fallback_note = f"Your campaigns:\n{json.dumps(res, indent=2, default=str)[:3000]}"
                fallback_done = True
            elif "inbox" in lower or "conversation" in lower or "interested" in lower or "opportunity" in lower:
                res = await _execute_tool(db, "list_conversations", {"status": "all"})
                fallback_note = f"Inbox (recent):\n{json.dumps(res, indent=2, default=str)[:3000]}"
                fallback_done = True
            elif "analytics" in lower or "dashboard" in lower or "stats" in lower:
                res = await _execute_tool(db, "get_analytics", {})
                fallback_note = f"Analytics:\n{json.dumps(res, indent=2, default=str)[:2500]}"
                fallback_done = True
        except Exception as _e:
            fallback_note = f"Fallback tool error: {_e}"
            fallback_done = True
        if fallback_done:
            await add_message(db, session.id, "user", user_text)
            await add_message(db, session.id, "assistant", fallback_note[:6000])
            new_mem = f"{session.memory or ''}\nUser: {user_text[:400]}\nAssistant: {fallback_note[:600]}"
            session.memory = new_mem[-6000:]
            session.updated_at = datetime.now(timezone.utc)
            await db.flush()
            return {"success": True, "response": fallback_note, "session_id": session.id, "fallback": True}
        return {"error": f"No {provider} API key configured. Go to Settings → AI Agent → set your {provider} API key, then try again.\n\nTip: you can still use simple commands without a key — e.g. 'list contacts', 'create campaign named \"Promo\"', 'show inbox', 'import this csv' — the offline fallback will handle them.", "needs_key": True}

    # Load history (last 40 messages)
    history = await get_messages(db, session.id, limit=60)
    # Build messages for LLM
    # Base system + session memory + skills
    system_parts = [BASE_SYSTEM_PROMPT]
    if session.system_prompt:
        system_parts.append(f"Session instructions: {session.system_prompt}")
    if session.memory:
        system_parts.append(f"Session memory (compressed history): {session.memory[:4000]}")
    # Load active skills for this session
    if session.skills:
        for skill_name in (session.skills or []):
            s = await get_skill_by_name(db, skill_name)
            if s:
                system_parts.append(f"Skill @{s.name}: {s.description or ''}\n{s.content[:3000]}")
    # Check for @skill mentions in current message
    skill_mentions = re.findall(r"@(\w+)", user_text)
    for mention in skill_mentions:
        s = await get_skill_by_name(db, mention)
        if s and s.name not in (session.skills or []):
            system_parts.append(f"Invoked Skill @{s.name}: {s.description or ''}\n{s.content[:3000]}")
    # File context
    if file_ids:
        for fid in file_ids:
            f = (await db.execute(select(AIAgentFile).where(AIAgentFile.id == fid))).scalar_one_or_none()
            if f:
                system_parts.append(f"Uploaded file '{f.filename}' ({f.size_bytes} bytes) preview: {json.dumps(f.preview, default=str)[:2000]}\nFirst 2000 chars:\n{f.content[:2000]}")
    elif session.id:
        # Include recent files for this session
        files = list((await db.execute(select(AIAgentFile).where(AIAgentFile.session_id == session.id).order_by(AIAgentFile.created_at.desc()).limit(3))).scalars().all())
        for f in files:
            system_parts.append(f"Session file '{f.filename}' preview: {json.dumps(f.preview, default=str)[:1500]}")

    system_text = "\n\n".join(system_parts)

    # Build LLM messages (OpenAI format)
    llm_messages: list[dict] = [{"role": "system", "content": system_text}]
    for m in history[-30:]:  # last 30 to stay within context
        if m.role == "system":
            continue
        entry: dict = {"role": m.role if m.role in ("user","assistant","system") else "user", "content": m.content}
        if m.role == "assistant" and m.tool_calls:
            entry["tool_calls"] = m.tool_calls
        if m.role == "tool" and m.tool_call_id:
            entry["tool_call_id"] = m.tool_call_id
            entry["name"] = m.tool_calls.get("name") if isinstance(m.tool_calls, dict) else None
        llm_messages.append(entry)
    llm_messages.append({"role": "user", "content": user_text})

    # Save user message
    await add_message(db, session.id, "user", user_text)

    # Tool loop
    max_rounds = 8
    final_text = ""
    for round_idx in range(max_rounds):
        resp = await _call_llm(provider, llm_messages, TOOLS, api_key)
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []

        if tool_calls:
            # Save assistant tool call message
            await add_message(db, session.id, "assistant", content or "I'll handle that.", tool_calls=tool_calls)
            llm_messages.append({"role": "assistant", "content": content or "", "tool_calls": tool_calls})
            # Execute each tool
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name") or tc.get("name")
                raw_args = fn.get("arguments") or tc.get("args") or "{}"
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        args = {}
                else:
                    args = raw_args or {}
                result = await _execute_tool(db, name, args, user_id=session.user_id)
                # Save tool result as message
                tool_result_text = json.dumps(result, default=str)[:8000]
                await add_message(db, session.id, "tool", tool_result_text, tool_calls={"name": name}, tool_call_id=tc.get("id"))
                llm_messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": tool_result_text, "name": name})
            continue
        else:
            # Final answer
            final_text = content
            await add_message(db, session.id, "assistant", final_text)
            break
    else:
        final_text = final_text or "I've processed your request with the available tools."

    # Update memory (simple append, truncated)
    new_memory = f"{session.memory or ''}\nUser: {user_text[:500]}\nAssistant: {final_text[:800]}"
    session.memory = new_memory[-6000:]
    session.updated_at = datetime.now(timezone.utc)
    await db.flush()

    return {"success": True, "response": final_text, "session_id": session.id}
