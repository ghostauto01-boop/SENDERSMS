"""The AI Agent's hands: every capability the app has, as a tool.

DESIGN
------
Each tool is a :class:`Tool` record with

  * an OpenAI-compatible JSON schema (Gemini gets the same schema, sanitised),
  * a required *area* (contacts, lists, campaigns, inbox, …) so the UI can group
    them and the prompt can tell the model what exists,
  * a ``danger`` flag for irreversible or high-impact work. A danger tool is
    **never executed inline**: it is stored as a pending
    :class:`~app.models.ai_agent.AIAgentAction` and the chat returns a
    confirmation card (audience size, channel, message preview, schedule) that
    the operator approves or cancels. ``auto_approve`` (off by default) skips
    the card.

Every execution is written to ``ai_agent_actions`` with parameters, result,
status and duration — that log is the Agent activity page and the undo source.

CONSENT IS NOT OPTIONAL
-----------------------
``start_campaign`` and ``send_sms``/``send_email`` check the contact's opt-out
state before touching the network and refuse with a plain sentence. An operator
cannot talk the agent into messaging somebody who said STOP, and
``tools_consent_guard`` below asserts it in the test suite.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_providers import sanitise_schema

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    name: str
    area: str
    description: str
    parameters: dict
    handler: Callable[..., Awaitable[Any]]
    #: Irreversible or high-impact: goes through the confirmation card.
    danger: bool = False
    #: A plain-English one-liner used by the confirmation card while the exact
    #: figures are still being computed.
    danger_label: str = ""

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


REGISTRY: dict[str, Tool] = {}


def tool(
    name: str,
    area: str,
    description: str,
    parameters: Optional[dict] = None,
    *,
    danger: bool = False,
    danger_label: str = "",
):
    """Register a tool. Order of registration is the order the model sees."""

    def decorator(fn: Callable[..., Awaitable[Any]]):
        REGISTRY[name] = Tool(
            name=name,
            area=area,
            description=description,
            parameters=parameters or {"type": "object", "properties": {}, "required": []},
            handler=fn,
            danger=danger,
            danger_label=danger_label,
        )
        return fn

    return decorator


def _obj(**properties: Any) -> dict:
    required = [k for k, v in properties.items() if isinstance(v, dict) and v.pop("_required", False)]
    cleaned = {}
    for key, value in properties.items():
        if isinstance(value, dict):
            value = dict(value)
            value.pop("_required", None)
        cleaned[key] = value
    return {"type": "object", "properties": cleaned, "required": required}


def S(description: str, **extra) -> dict:  # noqa: N802 - tiny schema helper
    return {"type": "string", "description": description, **extra}


def I(description: str, **extra) -> dict:  # noqa: N802
    return {"type": "integer", "description": description, **extra}


def B(description: str, **extra) -> dict:  # noqa: N802
    return {"type": "boolean", "description": description, **extra}


def A(description: str, item_type: str = "integer", **extra) -> dict:  # noqa: N802
    return {"type": "array", "description": description, "items": {"type": item_type}, **extra}


# ---------------------------------------------------------------------------
# small shared helpers
# ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _contact_brief(contact) -> dict:
    return {
        "id": contact.id,
        "name": f"{contact.first_name or ''} {contact.last_name or ''}".strip()
        or contact.business_name
        or contact.phone_number
        or contact.email
        or f"Contact {contact.id}",
        "phone": contact.phone_number,
        "email": contact.email,
        "city": contact.city,
        "lead_status": contact.lead_status,
        "opted_out": bool(contact.is_opted_out),
        "email_opted_out": bool(getattr(contact, "is_email_opted_out", False)),
    }


async def _find_contact(db: AsyncSession, contact_id: Any = None, phone: Any = None, email: Any = None):
    from app.models.contact import Contact

    if contact_id:
        return (await db.execute(select(Contact).where(Contact.id == int(contact_id)))).scalar_one_or_none()
    if phone:
        from app.utils.contact_identity import clean_phone, normalize_email

        number = clean_phone(str(phone)) or str(phone).strip()
        found = (await db.execute(select(Contact).where(Contact.phone_number == number))).scalar_one_or_none()
        if found:
            return found
        if "@" in str(phone):
            return await _find_contact(db, email=phone)
        return None
    if email:
        from app.utils.contact_identity import normalize_email

        address = normalize_email(str(email)) or str(email).strip().lower()
        return (
            await db.execute(select(Contact).where(func.lower(Contact.email) == address))
        ).scalar_one_or_none()
    return None


def _is_opted_out(contact, channel: str) -> Optional[str]:
    """Return a refusal sentence if this contact may not be messaged."""
    if channel == "email":
        if getattr(contact, "is_email_opted_out", False):
            return "This contact has unsubscribed from email."
        if getattr(contact, "is_email_undeliverable", False):
            return "This contact's email address is marked undeliverable."
        return None
    if contact.is_opted_out:
        return "This contact has opted out of SMS (they replied STOP)."
    if getattr(contact, "is_undeliverable", False):
        return "This contact's number is marked undeliverable."
    return None


# ===========================================================================
# CONTACTS
# ===========================================================================
@tool(
    "search_contacts",
    "contacts",
    "Find contacts by free text (name, phone, email, business), status, city or list. Returns ids you can pass to other tools.",
    _obj(
        query=S("Free text to match against name, phone, email, business name, city"),
        lead_status=S("new | contacted | replied | interested | not_interested | converted"),
        city=S("Filter by city"),
        list_id=I("Only contacts in this list"),
        limit=I("Max results (default 20, max 100)"),
    ),
)
async def search_contacts(db, args, ctx):
    from app.models.contact import Contact
    from app.models.contact_list import ContactListMember

    limit = max(1, min(int(args.get("limit") or 20), 100))
    stmt = select(Contact).order_by(Contact.id.desc()).limit(limit)
    if args.get("query"):
        needle = f"%{args['query']}%"
        stmt = stmt.where(
            or_(
                Contact.first_name.ilike(needle),
                Contact.last_name.ilike(needle),
                Contact.phone_number.ilike(needle),
                Contact.email.ilike(needle),
                Contact.business_name.ilike(needle),
                Contact.city.ilike(needle),
            )
        )
    if args.get("lead_status"):
        stmt = stmt.where(Contact.lead_status == args["lead_status"])
    if args.get("city"):
        stmt = stmt.where(Contact.city.ilike(f"%{args['city']}%"))
    if args.get("list_id"):
        stmt = stmt.join(ContactListMember, Contact.id == ContactListMember.contact_id).where(
            ContactListMember.list_id == int(args["list_id"])
        )
    rows = list((await db.execute(stmt)).scalars().all())
    return {"count": len(rows), "contacts": [await _contact_brief(c) for c in rows]}


@tool(
    "get_contact",
    "contacts",
    "Full detail for one contact: fields, consent state, lists, tags, recent messages.",
    _obj(contact_id=I("Contact id", _required=True)),
)
async def get_contact(db, args, ctx):
    from app.models.contact_list import ContactList, ContactListMember
    from app.models.conversation import Message

    contact = await _find_contact(db, contact_id=args.get("contact_id"))
    if not contact:
        return {"error": "Contact not found"}
    lists = list(
        (
            await db.execute(
                select(ContactList.name)
                .join(ContactListMember, ContactList.id == ContactListMember.list_id)
                .where(ContactListMember.contact_id == contact.id)
            )
        ).scalars().all()
    )
    recent = list(
        (
            await db.execute(
                select(Message).where(Message.contact_id == contact.id)
                .order_by(Message.created_at.desc()).limit(10)
            )
        ).scalars().all()
    )
    return {
        "contact": {
            **(await _contact_brief(contact)),
            "business_name": contact.business_name,
            "state": contact.state,
            "country": contact.country,
            "notes": contact.notes,
            "consent_status": contact.consent_status,
            "has_consented": contact.has_consented,
            "source": contact.source,
            "messages_sent": contact.messages_sent,
            "messages_received": contact.messages_received,
        },
        "lists": lists,
        "recent_messages": [
            {"direction": m.direction, "body": (m.body or "")[:300], "at": m.created_at.isoformat()}
            for m in recent
        ],
    }


@tool(
    "create_contact",
    "contacts",
    "Create one contact. Needs a Nigerian phone number or an email address. Numbers are normalised to +234.",
    _obj(
        phone_number=S("Phone, any Nigerian format (0803…, 0803 123 4567, +234…)"),
        email=S("Email address"),
        first_name=S("First name"),
        last_name=S("Last name"),
        business_name=S("Business or company name"),
        city=S("City"),
        state=S("State"),
        list_id=I("Also add the new contact to this list"),
        notes=S("Free-text note"),
    ),
)
async def create_contact(db, args, ctx):
    from app.models.contact import Contact
    from app.models.contact_list import ContactListMember
    from app.utils.contact_identity import clean_phone, normalize_email

    phone = clean_phone(args.get("phone_number")) if args.get("phone_number") else None
    email = normalize_email(args["email"]) if args.get("email") else None
    if args.get("phone_number") and not phone:
        return {"error": f"“{args['phone_number']}” is not a usable phone number."}
    if not phone and not email:
        return {"error": "A contact needs a phone number or an email address."}
    if phone:
        existing = (
            await db.execute(select(Contact).where(Contact.phone_number == phone))
        ).scalar_one_or_none()
        if existing:
            return {"error": f"That number is already contact #{existing.id}.", "contact_id": existing.id}
    contact = Contact(
        phone_number=phone,
        email=email,
        email_lower=email.lower() if email else None,
        email_source="manual" if email else None,
        first_name=args.get("first_name"),
        last_name=args.get("last_name"),
        business_name=args.get("business_name"),
        city=args.get("city"),
        state=args.get("state"),
        notes=args.get("notes"),
        country="Nigeria",
        source="ai_agent",
    )
    db.add(contact)
    await db.flush()
    if args.get("list_id"):
        db.add(ContactListMember(list_id=int(args["list_id"]), contact_id=contact.id))
        await db.flush()
    return {"ok": True, "contact": await _contact_brief(contact)}


@tool(
    "update_contact",
    "contacts",
    "Update fields on one contact.",
    _obj(
        contact_id=I("Contact id", _required=True),
        fields={
            "type": "object",
            "description": "Field -> new value. Allowed: first_name, last_name, business_name, phone_number, email, city, state, notes, lead_status",
        },
    ),
)
async def update_contact(db, args, ctx):
    allowed = {
        "first_name", "last_name", "business_name", "phone_number", "email",
        "city", "state", "country", "notes", "lead_status", "industry", "website",
    }
    contact = await _find_contact(db, contact_id=args.get("contact_id"))
    if not contact:
        return {"error": "Contact not found"}
    changed = {}
    for key, value in (args.get("fields") or {}).items():
        if key not in allowed:
            continue
        if key == "phone_number" and value:
            from app.utils.contact_identity import clean_phone

            value = clean_phone(str(value))
            if not value:
                return {"error": "That phone number is not usable."}
        if key == "email" and value:
            from app.utils.contact_identity import normalize_email

            value = normalize_email(str(value))
            contact.email_lower = value.lower() if value else None
        setattr(contact, key, value)
        changed[key] = value
    await db.flush()
    return {"ok": True, "contact_id": contact.id, "changed": changed}


@tool(
    "delete_contact",
    "contacts",
    "Delete ONE contact. Irreversible.",
    _obj(contact_id=I("Contact id", _required=True)),
    danger=True,
    danger_label="Delete a contact",
)
async def delete_contact(db, args, ctx):
    from app.api.v1.contacts import _delete_contacts_bulk

    contact = await _find_contact(db, contact_id=args.get("contact_id"))
    if not contact:
        return {"error": "Contact not found"}
    brief = await _contact_brief(contact)
    await _delete_contacts_bulk(db, [contact.id])
    # Undo payload: the contact row is gone, so keep the fields that matter.
    return {"ok": True, "deleted": brief, "undo": {"kind": "restore_contact", "contact": brief}}


@tool(
    "bulk_delete_contacts",
    "contacts",
    "Delete many contacts matching a filter (or an explicit id list). Irreversible — always shows a confirmation card first.",
    _obj(
        contact_ids=A("Explicit list of contact ids"),
        search=S("Delete contacts matching this text"),
        lead_status=S("Delete contacts with this status"),
        list_id=I("Delete contacts in this list"),
        tag=S("Delete contacts carrying this tag"),
        reason=S("Why (stored in the activity log)"),
    ),
    danger=True,
    danger_label="Bulk delete contacts",
)
async def bulk_delete_contacts(db, args, ctx):
    from app.api.v1.contacts import _apply_contact_filters, _delete_contacts_bulk
    from app.models.contact import Contact

    ids: list[int] = []
    if args.get("contact_ids"):
        ids = [int(i) for i in args["contact_ids"]]
    else:
        stmt = _apply_contact_filters(
            select(Contact.id), args.get("search"), args.get("lead_status"), args.get("tag")
        )
        if args.get("list_id"):
            from app.models.contact_list import ContactListMember

            stmt = stmt.join(ContactListMember, Contact.id == ContactListMember.contact_id).where(
                ContactListMember.list_id == int(args["list_id"])
            )
        ids = list((await db.execute(stmt)).scalars().all())
    if not ids:
        return {"error": "No contacts matched, nothing was deleted."}
    snapshot = list(
        (
            await db.execute(
                select(Contact.id, Contact.phone_number, Contact.email, Contact.first_name,
                       Contact.last_name, Contact.business_name)
                .where(Contact.id.in_(ids[:2000]))
            )
        ).all()
    )
    await _delete_contacts_bulk(db, ids)
    return {
        "ok": True,
        "deleted": len(ids),
        "undo": {
            "kind": "restore_contacts",
            "contacts": [
                {
                    "id": row[0], "phone": row[1], "email": row[2],
                    "first_name": row[3], "last_name": row[4], "business_name": row[5],
                }
                for row in snapshot
            ],
        },
    }


@tool(
    "bulk_contact_action",
    "contacts",
    "Apply one action to many contacts: set lead status, add or remove a tag, opt in/out of SMS or email.",
    _obj(
        action=S("status | tag | untag | sms_opt_out | sms_opt_in | email_opt_out | email_opt_in", _required=True),
        contact_ids=A("Contact ids to act on"),
        value=S("Status value or tag name (for action=status/tag/untag)"),
        list_id=I("Act on every contact in this list instead of explicit ids"),
        search=S("Act on contacts matching this text"),
    ),
    danger=True,
    danger_label="Change many contacts at once",
)
async def bulk_contact_action(db, args, ctx):
    from app.models.contact import Contact
    from app.models.contact_list import ContactListMember
    from app.services.suppression_service import add_suppression

    action = args.get("action")
    ids = [int(i) for i in (args.get("contact_ids") or [])]
    if not ids:
        stmt = select(Contact.id)
        if args.get("list_id"):
            stmt = stmt.join(ContactListMember, Contact.id == ContactListMember.contact_id).where(
                ContactListMember.list_id == int(args["list_id"])
            )
        if args.get("search"):
            needle = f"%{args['search']}%"
            stmt = stmt.where(or_(Contact.first_name.ilike(needle), Contact.phone_number.ilike(needle),
                                  Contact.email.ilike(needle), Contact.business_name.ilike(needle)))
        ids = list((await db.execute(stmt)).scalars().all())
    if not ids:
        return {"error": "No contacts matched."}
    contacts = list((await db.execute(select(Contact).where(Contact.id.in_(ids)))).scalars().all())

    if action == "status":
        value = args.get("value") or "new"
        for contact in contacts:
            contact.lead_status = value
        return {"ok": True, "affected": len(contacts), "lead_status": value}

    if action in ("tag", "untag"):
        from app.models.contact import ContactTag, Tag

        name = (args.get("value") or "").strip()
        if not name:
            return {"error": "A tag name is required."}
        tag = (await db.execute(select(Tag).where(func.lower(Tag.name) == name.lower()))).scalar_one_or_none()
        if tag is None and action == "tag":
            tag = Tag(name=name)
            db.add(tag)
            await db.flush()
        if tag is None:
            return {"ok": True, "affected": 0, "note": "That tag does not exist."}
        affected = 0
        for contact in contacts:
            existing = (
                await db.execute(
                    select(ContactTag).where(ContactTag.contact_id == contact.id, ContactTag.tag_id == tag.id)
                )
            ).scalar_one_or_none()
            if action == "tag" and existing is None:
                db.add(ContactTag(contact_id=contact.id, tag_id=tag.id))
                affected += 1
            elif action == "untag" and existing is not None:
                await db.delete(existing)
                affected += 1
        await db.flush()
        return {"ok": True, "affected": affected, "tag": name}

    if action in ("sms_opt_out", "sms_opt_in", "email_opt_out", "email_opt_in"):
        affected = 0
        for contact in contacts:
            if action == "sms_opt_out":
                contact.is_opted_out = True
                contact.opted_out_at = _now()
                contact.opt_out_reason = "Set by AI Agent"
                contact.consent_status = "opted_out"
                contact.has_consented = False
                if contact.phone_number:
                    await add_suppression(db, contact.phone_number, contact_id=contact.id,
                                          reason="Set by AI Agent", source="manual")
            elif action == "sms_opt_in":
                contact.is_opted_out = False
                contact.opted_out_at = None
                contact.consent_status = "opted_in"
                contact.has_consented = True
            elif action == "email_opt_out":
                contact.is_email_opted_out = True
                contact.email_opted_out_at = _now()
                contact.email_opt_out_reason = "Set by AI Agent"
            else:
                contact.is_email_opted_out = False
                contact.email_opted_out_at = None
            affected += 1
        await db.flush()
        return {"ok": True, "affected": affected, "action": action}

    return {"error": f"Unknown action {action}."}


@tool(
    "get_contact_tags",
    "contacts",
    "List every tag with how many contacts carry it.",
)
async def get_contact_tags(db, args, ctx):
    from app.models.contact import ContactTag, Tag

    rows = (
        await db.execute(
            select(Tag.name, func.count(ContactTag.contact_id))
            .outerjoin(ContactTag, Tag.id == ContactTag.tag_id)
            .group_by(Tag.name)
            .order_by(func.count(ContactTag.contact_id).desc())
        )
    ).all()
    return {"tags": [{"name": name, "contacts": count} for name, count in rows]}


@tool(
    "count_contacts",
    "contacts",
    "Count contacts overall, by channel (SMS-capable, email-capable) and by status. Use this before promising an audience size.",
    _obj(list_id=I("Count only this list"), channel=S("sms | email | all")),
)
async def count_contacts(db, args, ctx):
    from app.models.contact import Contact
    from app.models.contact_list import ContactListMember

    base = select(func.count(Contact.id))
    if args.get("list_id"):
        base = base.join(ContactListMember, Contact.id == ContactListMember.contact_id).where(
            ContactListMember.list_id == int(args["list_id"])
        )
    total = (await db.execute(base)).scalar() or 0
    sms = (
        await db.execute(
            select(func.count(Contact.id)).where(
                Contact.phone_number.isnot(None), Contact.phone_number != "",
                Contact.is_opted_out.is_(False),
            )
        )
    ).scalar() or 0
    email = (
        await db.execute(
            select(func.count(Contact.id)).where(
                Contact.email.isnot(None), Contact.email != "",
                Contact.is_email_opted_out.is_(False),
            )
        )
    ).scalar() or 0
    opted_out = (
        await db.execute(select(func.count(Contact.id)).where(Contact.is_opted_out.is_(True)))
    ).scalar() or 0
    by_status = (
        await db.execute(select(Contact.lead_status, func.count(Contact.id)).group_by(Contact.lead_status))
    ).all()
    return {
        "total": total,
        "reachable_by_sms": sms,
        "reachable_by_email": email,
        "opted_out": opted_out,
        "by_status": {status or "unknown": count for status, count in by_status},
    }


# ===========================================================================
# LISTS
# ===========================================================================
@tool("list_lists", "lists", "Every contact list with its member count.")
async def list_lists(db, args, ctx):
    from app.models.contact_list import ContactList, ContactListMember

    rows = (
        await db.execute(
            select(ContactList.id, ContactList.name, ContactList.description, func.count(ContactListMember.contact_id))
            .outerjoin(ContactListMember, ContactList.id == ContactListMember.list_id)
            .group_by(ContactList.id, ContactList.name, ContactList.description)
            .order_by(ContactList.name)
        )
    ).all()
    return {"lists": [{"id": r[0], "name": r[1], "description": r[2], "contacts": r[3]} for r in rows]}


@tool(
    "create_list",
    "lists",
    "Create a contact list.",
    _obj(name=S("List name", _required=True), description=S("What this list is for")),
)
async def create_list(db, args, ctx):
    from app.models.contact_list import ContactList

    name = (args.get("name") or "").strip()
    if not name:
        return {"error": "A list needs a name."}
    existing = (
        await db.execute(select(ContactList).where(func.lower(ContactList.name) == name.lower()))
    ).scalar_one_or_none()
    if existing:
        return {"ok": True, "existed": True, "list": {"id": existing.id, "name": existing.name}}
    lst = ContactList(name=name[:200], description=args.get("description"))
    db.add(lst)
    await db.flush()
    return {"ok": True, "list": {"id": lst.id, "name": lst.name}}


@tool(
    "add_contacts_to_list",
    "lists",
    "Add contacts to a list (idempotent — contacts already in the list are skipped).",
    _obj(
        list_id=I("List id", _required=True),
        contact_ids=A("Contact ids"),
        search=S("Instead of ids: add every contact matching this text"),
    ),
)
async def add_contacts_to_list(db, args, ctx):
    from app.models.contact import Contact
    from app.models.contact_list import ContactList, ContactListMember

    lst = (await db.execute(select(ContactList).where(ContactList.id == int(args["list_id"])))).scalar_one_or_none()
    if not lst:
        return {"error": "List not found"}
    ids = [int(i) for i in (args.get("contact_ids") or [])]
    if not ids and args.get("search"):
        needle = f"%{args['search']}%"
        ids = list(
            (
                await db.execute(
                    select(Contact.id).where(
                        or_(Contact.first_name.ilike(needle), Contact.phone_number.ilike(needle),
                            Contact.email.ilike(needle), Contact.business_name.ilike(needle))
                    )
                )
            ).scalars().all()
        )
    if not ids:
        return {"error": "No contacts to add."}
    existing = set(
        (
            await db.execute(
                select(ContactListMember.contact_id).where(
                    ContactListMember.list_id == lst.id,
                    ContactListMember.contact_id.in_(ids),
                )
            )
        ).scalars().all()
    )
    added = 0
    for cid in ids:
        if cid in existing:
            continue
        db.add(ContactListMember(list_id=lst.id, contact_id=cid))
        added += 1
    await db.flush()
    return {"ok": True, "list": lst.name, "added": added, "already_present": len(ids) - added}


@tool(
    "remove_contacts_from_list",
    "lists",
    "Remove contacts from a list. The contacts themselves are not deleted.",
    _obj(list_id=I("List id", _required=True), contact_ids=A("Contact ids", _required=True)),
)
async def remove_contacts_from_list(db, args, ctx):
    from app.models.contact_list import ContactListMember

    ids = [int(i) for i in args["contact_ids"]]
    rows = list(
        (
            await db.execute(
                select(ContactListMember).where(
                    ContactListMember.list_id == int(args["list_id"]),
                    ContactListMember.contact_id.in_(ids),
                )
            )
        ).scalars().all()
    )
    for row in rows:
        await db.delete(row)
    await db.flush()
    return {"ok": True, "removed": len(rows)}


@tool("list_stats", "lists", "Size, channel split and opt-out count for one list.", _obj(list_id=I("List id", _required=True)))
async def list_stats(db, args, ctx):
    from app.models.contact import Contact
    from app.models.contact_list import ContactList, ContactListMember

    list_id = int(args["list_id"])
    lst = (await db.execute(select(ContactList).where(ContactList.id == list_id))).scalar_one_or_none()
    if not lst:
        return {"error": "List not found"}
    joined = select(func.count(ContactListMember.contact_id)).where(ContactListMember.list_id == list_id)
    total = (await db.execute(joined)).scalar() or 0
    sms = (
        await db.execute(
            select(func.count(Contact.id))
            .join(ContactListMember, Contact.id == ContactListMember.contact_id)
            .where(ContactListMember.list_id == list_id, Contact.phone_number.isnot(None),
                   Contact.is_opted_out.is_(False))
        )
    ).scalar() or 0
    email = (
        await db.execute(
            select(func.count(Contact.id))
            .join(ContactListMember, Contact.id == ContactListMember.contact_id)
            .where(ContactListMember.list_id == list_id, Contact.email.isnot(None),
                   Contact.is_email_opted_out.is_(False))
        )
    ).scalar() or 0
    return {"list": lst.name, "contacts": total, "sms_ready": sms, "email_ready": email}


@tool(
    "delete_list",
    "lists",
    "Delete a list. Contacts stay in the database.",
    _obj(list_id=I("List id", _required=True)),
    danger=True,
    danger_label="Delete a list",
)
async def delete_list(db, args, ctx):
    from app.models.contact_list import ContactList, ContactListMember

    list_id = int(args["list_id"])
    lst = (await db.execute(select(ContactList).where(ContactList.id == list_id))).scalar_one_or_none()
    if not lst:
        return {"error": "List not found"}
    name = lst.name
    rows = list(
        (await db.execute(select(ContactListMember).where(ContactListMember.list_id == list_id))).scalars().all()
    )
    for row in rows:
        await db.delete(row)
    await db.delete(lst)
    await db.flush()
    return {"ok": True, "deleted": name, "members_removed": len(rows)}


# ===========================================================================
# FILES + IMPORT
# ===========================================================================
@tool(
    "read_file_rows",
    "files",
    "Read a window of rows from an uploaded file. Use this instead of trying to see the whole file.",
    _obj(
        file_id=I("File id", _required=True),
        offset=I("Start row (0-based)"),
        limit=I("How many rows (default 20, max 200)"),
    ),
)
async def read_file_rows(db, args, ctx):
    from app.models.ai_agent import AIAgentFile
    from app.services import agent_files

    record = (
        await db.execute(select(AIAgentFile).where(AIAgentFile.id == int(args["file_id"])))
    ).scalar_one_or_none()
    if not record:
        return {"error": "File not found"}
    try:
        parsed = agent_files.parse_stored(record.filename, record.content)
    except agent_files.FileParseError as error:
        return {"error": str(error)}
    rows = parsed.rows
    window = agent_files.slice_rows(rows, int(args.get("offset") or 0), int(args.get("limit") or 20))
    return {"file": record.filename, "total_rows": len(rows), "rows": window}


@tool(
    "query_file",
    "files",
    "Search an uploaded file: rows where any column contains the text. Answers 'which rows have 0803 numbers?' and similar.",
    _obj(
        file_id=I("File id", _required=True),
        query=S("Text to look for (empty returns the first rows)"),
        limit=I("Max matching rows (default 20)"),
    ),
)
async def query_file(db, args, ctx):
    from app.models.ai_agent import AIAgentFile
    from app.services import agent_files

    record = (
        await db.execute(select(AIAgentFile).where(AIAgentFile.id == int(args["file_id"])))
    ).scalar_one_or_none()
    if not record:
        return {"error": "File not found"}
    parsed = agent_files.parse_stored(record.filename, record.content)
    matches = agent_files.query_rows(parsed.rows, args.get("query") or "", int(args.get("limit") or 20))
    return {
        "file": record.filename,
        "total_rows": parsed.total_rows,
        "matched_returned": len(matches),
        "rows": matches,
    }


@tool(
    "inspect_file",
    "files",
    "Summary of an uploaded file: columns, detected types, row count, suggested column mapping and the first rows.",
    _obj(file_id=I("File id", _required=True)),
)
async def inspect_file(db, args, ctx):
    from app.models.ai_agent import AIAgentFile

    record = (
        await db.execute(select(AIAgentFile).where(AIAgentFile.id == int(args["file_id"])))
    ).scalar_one_or_none()
    if not record:
        return {"error": "File not found"}
    return {"file": record.filename, "summary": record.preview, "size_bytes": record.size_bytes}


_ONBOARDING_FIELDS = (
    "phone_number", "email", "first_name", "last_name", "full_name", "business_name",
    "city", "state", "country", "notes", "tags",
)


async def run_import(
    db: AsyncSession,
    rows: list[dict],
    mapping: dict[str, str],
    *,
    list_id: Optional[int] = None,
    progress: Optional[Callable[[int, int], Awaitable[None]]] = None,
) -> dict:
    """Upsert rows into contacts, chunked, with per-row error reporting.

    Shared by the ``import_csv`` tool and the file-import endpoint so an import
    started from the chat and one started from the UI produce identical data.
    """
    from app.models.contact import Contact
    from app.models.contact_list import ContactListMember
    from app.utils.contact_identity import clean_phone, normalize_email

    created = updated = skipped = 0
    errors: list[dict] = []
    seen_phones: set[str] = set()
    seen_emails: set[str] = set()
    total = len(rows)

    for index, row in enumerate(rows):
        values: dict[str, Any] = {}
        for header, field in mapping.items():
            if field not in _ONBOARDING_FIELDS:
                continue
            raw = str(row.get(header, "") or "").strip()
            if not raw:
                continue
            if field == "full_name":
                parts = raw.split()
                values.setdefault("first_name", parts[0])
                if len(parts) > 1:
                    values.setdefault("last_name", " ".join(parts[1:]))
                continue
            values[field] = raw

        phone = clean_phone(values.get("phone_number")) if values.get("phone_number") else None
        email = normalize_email(values["email"]) if values.get("email") else None
        if not phone and not email:
            skipped += 1
            if len(errors) < 50:
                errors.append({"row": index + 2, "reason": "no usable phone or email"})
            continue
        if phone and phone in seen_phones:
            skipped += 1
            continue
        if email and email in seen_emails:
            skipped += 1
            continue
        if phone:
            seen_phones.add(phone)
        if email:
            seen_emails.add(email)

        existing = None
        if phone:
            existing = (
                await db.execute(select(Contact).where(Contact.phone_number == phone))
            ).scalar_one_or_none()
        if existing is None and email:
            existing = (
                await db.execute(select(Contact).where(func.lower(Contact.email) == email))
            ).scalar_one_or_none()

        if existing is None:
            contact = Contact(
                phone_number=phone,
                email=email,
                email_lower=email.lower() if email else None,
                email_source="import" if email else None,
                country=values.get("country") or "Nigeria",
                source="ai_agent_import",
                first_name=values.get("first_name"),
                last_name=values.get("last_name"),
                business_name=values.get("business_name"),
                city=values.get("city"),
                state=values.get("state"),
                notes=values.get("notes"),
            )
            db.add(contact)
            await db.flush()
            created += 1
            contact_id = contact.id
        else:
            contact_id = existing.id
            touched = False
            for key in ("first_name", "last_name", "business_name", "city", "state", "notes"):
                if values.get(key) and not getattr(existing, key):
                    setattr(existing, key, values[key])
                    touched = True
            if email and not existing.email:
                existing.email = email
                existing.email_lower = email.lower()
                touched = True
            if touched:
                updated += 1

        if values.get("tags"):
            from app.models.contact import ContactTag, Tag

            for raw_tag in re.split(r"[;,|]", values["tags"]):
                name = raw_tag.strip()
                if not name:
                    continue
                tag = (
                    await db.execute(select(Tag).where(func.lower(Tag.name) == name.lower()))
                ).scalar_one_or_none()
                if tag is None:
                    tag = Tag(name=name[:100])
                    db.add(tag)
                    await db.flush()
                present = (
                    await db.execute(
                        select(ContactTag).where(
                            ContactTag.contact_id == contact_id, ContactTag.tag_id == tag.id
                        )
                    )
                ).scalar_one_or_none()
                if present is None:
                    db.add(ContactTag(contact_id=contact_id, tag_id=tag.id))

        if list_id:
            present = (
                await db.execute(
                    select(ContactListMember.id).where(
                        ContactListMember.list_id == int(list_id),
                        ContactListMember.contact_id == contact_id,
                    )
                )
            ).scalar_one_or_none()
            if present is None:
                db.add(ContactListMember(list_id=int(list_id), contact_id=contact_id))

        if progress and (index % 200 == 0 or index == total - 1):
            await progress(index + 1, total)

    await db.flush()
    return {
        "ok": True,
        "imported": created,
        "updated": updated,
        "skipped": skipped,
        "total_rows": total,
        "list_id": list_id,
        "errors": errors,
    }


@tool(
    "import_csv",
    "files",
    "Import an uploaded file into contacts. Pass the mapping shown by inspect_file (or omit it to use the suggested mapping). Handles thousands of rows.",
    _obj(
        file_id=I("File id", _required=True),
        list_id=I("List to add every imported contact to (created by create_list if needed)"),
        mapping={
            "type": "object",
            "description": "File column -> contact field (phone_number, email, first_name, last_name, full_name, business_name, city, state, tags, notes)",
        },
    ),
    danger=True,
    danger_label="Import contacts from a file",
)
async def import_csv(db, args, ctx):
    from app.models.ai_agent import AIAgentFile
    from app.services import agent_files

    record = (
        await db.execute(select(AIAgentFile).where(AIAgentFile.id == int(args["file_id"])))
    ).scalar_one_or_none()
    if not record:
        return {"error": "File not found"}
    parsed = agent_files.parse_stored(record.filename, record.content)
    if not parsed.is_tabular:
        return {"error": f"{record.filename} is not a spreadsheet — nothing to import from it."}
    mapping = args.get("mapping") or (record.preview or {}).get("suggested_mapping") or {}
    if not mapping:
        mapping = agent_files.suggest_mapping(parsed.headers, parsed.rows)
    if not any(v in ("phone_number", "email") for v in mapping.values()):
        return {
            "error": (
                "The mapping has no phone_number or email column, so no contact could be "
                "reached. Map one of the file's columns to phone_number or email and retry."
            ),
            "columns": parsed.headers,
            "suggested": mapping,
        }
    return await run_import(db, parsed.rows, mapping, list_id=args.get("list_id"))


# ===========================================================================
# TEMPLATES + VARIABLES
# ===========================================================================
@tool("list_templates", "templates", "Every message template with its channel.")
async def list_templates(db, args, ctx):
    from app.models.template import Template

    rows = list((await db.execute(select(Template).order_by(Template.name))).scalars().all())
    return {
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "channel": getattr(t, "channel", "sms"),
                "subject": getattr(t, "subject", None),
                "body_preview": (t.body or "")[:200],
            }
            for t in rows
        ]
    }


@tool(
    "create_template",
    "templates",
    "Create a message template. Body may contain {{first_name}} style variables.",
    _obj(
        name=S("Template name", _required=True),
        body=S("Message body", _required=True),
        subject=S("Email subject (email templates only)"),
        channel=S("sms | email"),
    ),
)
async def create_template(db, args, ctx):
    from app.models.template import Template

    body = (args.get("body") or "").strip()
    if not body:
        return {"error": "A template needs a body."}
    tpl = Template(
        name=(args.get("name") or "Untitled")[:200],
        body=body,
        subject=args.get("subject"),
        channel=args.get("channel") or "sms",
    )
    db.add(tpl)
    await db.flush()
    return {"ok": True, "template": {"id": tpl.id, "name": tpl.name}}


@tool(
    "preview_template",
    "templates",
    "Render a template for one contact so you can show the operator the exact message.",
    _obj(
        template_id=I("Template id"),
        body=S("Instead of a template id: render this text"),
        contact_id=I("Contact to fill variables from"),
        subject=S("Subject to render too"),
    ),
)
async def preview_template(db, args, ctx):
    from app.models.template import Template

    body = args.get("body")
    subject = args.get("subject")
    if args.get("template_id"):
        tpl = (await db.execute(select(Template).where(Template.id == int(args["template_id"])))).scalar_one_or_none()
        if not tpl:
            return {"error": "Template not found"}
        body = body or tpl.body
        subject = subject or getattr(tpl, "subject", None)
    if not body:
        return {"error": "Provide a template_id or a body."}
    contact = None
    if args.get("contact_id"):
        contact = await _find_contact(db, contact_id=args["contact_id"])

    def render(text: str) -> str:
        if not text:
            return text or ""
        values = {
            "first_name": (contact.first_name if contact else "") or "there",
            "last_name": (contact.last_name if contact else "") or "",
            "business_name": (contact.business_name if contact else "") or "",
            "city": (contact.city if contact else "") or "",
            "phone_number": (contact.phone_number if contact else "") or "",
            "email": (contact.email if contact else "") or "",
        }
        out = text
        for key, value in values.items():
            out = re.sub(r"\{\{\s*" + key + r"\s*\}\}", str(value), out, flags=re.I)
        return out

    rendered = render(body)
    return {
        "body": rendered,
        "subject": render(subject) if subject else None,
        "characters": len(rendered),
        "sms_segments": max(1, -(-len(rendered) // 160)),
    }


@tool("list_variables", "templates", "Custom variables available in templates.")
async def list_variables(db, args, ctx):
    from app.models.variable import ContactVariable

    rows = list((await db.execute(select(ContactVariable))).scalars().all())
    return {"variables": [{"name": v.name, "description": v.description} for v in rows]}


# ===========================================================================
# CAMPAIGNS (classic)
# ===========================================================================
@tool(
    "list_campaigns",
    "campaigns",
    "List campaigns with status and counts.",
    _obj(channel=S("sms | email | all"), status=S("draft | scheduled | running | paused | completed | failed")),
)
async def list_campaigns(db, args, ctx):
    from app.models.campaign import Campaign

    stmt = select(Campaign).order_by(Campaign.id.desc()).limit(50)
    if args.get("channel") and args["channel"] != "all":
        stmt = stmt.where(Campaign.channel == args["channel"])
    if args.get("status"):
        stmt = stmt.where(Campaign.status == args["status"])
    rows = list((await db.execute(stmt)).scalars().all())
    return {
        "campaigns": [
            {
                "id": c.id,
                "name": c.name,
                "channel": c.channel,
                "status": c.status,
                "list_id": c.list_id,
                "total_contacts": c.total_contacts,
                "sent": c.messages_sent,
                "replies": c.replies_count,
                "scheduled_for": c.scheduled_start_at.isoformat() if c.scheduled_start_at else None,
            }
            for c in rows
        ]
    }


@tool("get_campaign", "campaigns", "Full detail for one campaign, including its message body and validation state.", _obj(campaign_id=I("Campaign id", _required=True)))
async def get_campaign(db, args, ctx):
    from app.models.campaign import Campaign

    campaign = (await db.execute(select(Campaign).where(Campaign.id == int(args["campaign_id"])))).scalar_one_or_none()
    if not campaign:
        return {"error": "Campaign not found"}
    return {
        "campaign": {
            "id": campaign.id,
            "name": campaign.name,
            "channel": campaign.channel,
            "status": campaign.status,
            "list_id": campaign.list_id,
            "subject": getattr(campaign, "subject", None),
            "message_body": campaign.message_body,
            "total_contacts": campaign.total_contacts,
            "sent": campaign.messages_sent,
            "replies": campaign.replies_count,
            "scheduled_for": campaign.scheduled_start_at.isoformat() if campaign.scheduled_start_at else None,
        }
    }


@tool(
    "create_campaign",
    "campaigns",
    "Create a DRAFT campaign. Nothing is sent until start_campaign (which asks for approval).",
    _obj(
        name=S("Campaign name", _required=True),
        channel=S("sms | email"),
        list_id=I("Audience list"),
        message_body=S("The message text"),
        template_id=I("Use this template instead of a body"),
        subject=S("Email subject (required for email)"),
        scheduled_start_at=S("ISO datetime to schedule; omit to leave it as a draft"),
    ),
)
async def create_campaign(db, args, ctx):
    from app.models.campaign import Campaign
    from app.models.contact_list import ContactList, ContactListMember
    from app.models.template import Template

    name = (args.get("name") or "").strip()
    if not name:
        return {"error": "A campaign needs a name."}
    if len(name) > 255:
        return {"error": "That campaign name is longer than 255 characters. Shorten it."}
    channel = args.get("channel") or "sms"
    body = args.get("message_body")
    subject = args.get("subject")
    if args.get("template_id"):
        tpl = (await db.execute(select(Template).where(Template.id == int(args["template_id"])))).scalar_one_or_none()
        if not tpl:
            return {"error": "Template not found"}
        body = body or tpl.body
        subject = subject or getattr(tpl, "subject", None)

    total_contacts = 0
    if args.get("list_id"):
        lst = (await db.execute(select(ContactList).where(ContactList.id == int(args["list_id"])))).scalar_one_or_none()
        if not lst:
            return {"error": "List not found"}
        total_contacts = (
            await db.execute(
                select(func.count(ContactListMember.contact_id)).where(
                    ContactListMember.list_id == lst.id
                )
            )
        ).scalar() or 0

    scheduled = None
    if args.get("scheduled_start_at"):
        from dateutil import parser as date_parser

        try:
            scheduled = date_parser.parse(str(args["scheduled_start_at"]))
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
        except Exception:
            return {"error": "I could not read that date. Use an ISO date like 2026-02-03T09:00:00+01:00."}

    campaign = Campaign(
        name=name[:255],
        channel=channel,
        list_id=args.get("list_id"),
        message_body=body,
        subject=subject,
        template_id=args.get("template_id"),
        status="scheduled" if scheduled else "draft",
        scheduled_start_at=scheduled,
        total_contacts=total_contacts,
    )
    db.add(campaign)
    await db.flush()
    return {
        "ok": True,
        "campaign": {
            "id": campaign.id,
            "name": campaign.name,
            "channel": campaign.channel,
            "status": campaign.status,
            "audience": total_contacts,
            "scheduled_for": scheduled.isoformat() if scheduled else None,
        },
        "next": "Validate it with validate_campaign, then start_campaign to launch.",
    }


@tool(
    "update_campaign",
    "campaigns",
    "Update a draft or scheduled campaign.",
    _obj(campaign_id=I("Campaign id", _required=True), fields={"type": "object", "description": "field -> value"}),
)
async def update_campaign(db, args, ctx):
    from app.models.campaign import Campaign

    campaign = (await db.execute(select(Campaign).where(Campaign.id == int(args["campaign_id"])))).scalar_one_or_none()
    if not campaign:
        return {"error": "Campaign not found"}
    if campaign.status not in ("draft", "scheduled"):
        return {"error": f"This campaign is {campaign.status}; only drafts and scheduled campaigns can be edited."}
    allowed = {"name", "message_body", "subject", "list_id", "scheduled_start_at", "template_id"}
    changed = {}
    for key, value in (args.get("fields") or {}).items():
        if key not in allowed:
            continue
        if key == "name" and value and len(str(value)) > 255:
            return {"error": "That name is longer than 255 characters."}
        setattr(campaign, key, value)
        changed[key] = value
    await db.flush()
    return {"ok": True, "changed": changed}


@tool("validate_campaign", "campaigns", "Check a campaign before it starts: audience, message, sender, consent. Returns errors and warnings.", _obj(campaign_id=I("Campaign id", _required=True)))
async def validate_campaign(db, args, ctx):
    from app.models.campaign import Campaign
    from app.services.campaign_service import CampaignService, CampaignStateError

    campaign = (await db.execute(select(Campaign).where(Campaign.id == int(args["campaign_id"])))).scalar_one_or_none()
    if not campaign:
        return {"error": "Campaign not found"}
    try:
        # validate_report() *checks only*: it never moves the campaign's state,
        # so asking "is this ok?" can never send anything.
        result = await CampaignService(db).validate_report(campaign.id)
    except CampaignStateError as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": []}
    except Exception as exc:
        logger.exception("agent: validate_campaign failed")
        return {"ok": False, "errors": [f"Could not validate: {exc}"], "warnings": []}
    return {
        "ok": bool(result.get("valid")),
        "errors": result.get("errors") or [],
        "warnings": result.get("warnings") or [],
        "audience": result.get("audience"),
        "campaign": campaign.name,
    }


@tool(
    "start_campaign",
    "campaigns",
    "Launch a campaign now. Validates first and ALWAYS asks the operator to approve (unless auto-approve is on).",
    _obj(campaign_id=I("Campaign id", _required=True)),
    danger=True,
    danger_label="Start a campaign (messages will be sent)",
)
async def start_campaign(db, args, ctx):
    from app.models.campaign import Campaign
    from app.services.campaign_service import CampaignService

    campaign = (await db.execute(select(Campaign).where(Campaign.id == int(args["campaign_id"])))).scalar_one_or_none()
    if not campaign:
        return {"error": "Campaign not found"}
    validation = await validate_campaign(db, {"campaign_id": campaign.id}, ctx)
    if not validation.get("ok"):
        return {
            "error": "This campaign failed validation, so it was not started.",
            "errors": validation.get("errors"),
        }
    try:
        await CampaignService(db).start_campaign(campaign.id)
    except Exception as exc:
        return {"error": f"Could not start the campaign: {exc}"}
    await db.flush()
    return {"ok": True, "campaign": campaign.name, "status": campaign.status,
            "audience": campaign.total_contacts}


@tool(
    "pause_campaign",
    "campaigns",
    "Pause a running or scheduled campaign.",
    _obj(campaign_id=I("Campaign id", _required=True)),
    danger=True,
    danger_label="Pause a campaign",
)
async def pause_campaign(db, args, ctx):
    from app.models.campaign import Campaign
    from app.services.campaign_service import CampaignService

    campaign = (await db.execute(select(Campaign).where(Campaign.id == int(args["campaign_id"])))).scalar_one_or_none()
    if not campaign:
        return {"error": "Campaign not found"}
    try:
        await CampaignService(db).pause_campaign(campaign.id)
    except Exception as exc:
        return {"error": f"Could not pause: {exc}"}
    await db.flush()
    return {"ok": True, "campaign": campaign.name, "status": campaign.status}


@tool("resume_campaign", "campaigns", "Resume a paused campaign.", _obj(campaign_id=I("Campaign id", _required=True)), danger=True, danger_label="Resume a campaign")
async def resume_campaign(db, args, ctx):
    from app.models.campaign import Campaign
    from app.services.campaign_service import CampaignService

    campaign = (await db.execute(select(Campaign).where(Campaign.id == int(args["campaign_id"])))).scalar_one_or_none()
    if not campaign:
        return {"error": "Campaign not found"}
    try:
        await CampaignService(db).resume_campaign(campaign.id)
    except Exception as exc:
        return {"error": f"Could not resume: {exc}"}
    await db.flush()
    return {"ok": True, "campaign": campaign.name, "status": campaign.status}


@tool(
    "schedule_campaign",
    "campaigns",
    "Schedule a campaign for a future date/time. Shows the operator the exact plan before it is saved.",
    _obj(
        campaign_id=I("Campaign id", _required=True),
        scheduled_start_at=S("ISO datetime, e.g. 2026-02-03T09:00:00+01:00", _required=True),
    ),
    danger=True,
    danger_label="Schedule a campaign",
)
async def schedule_campaign(db, args, ctx):
    from dateutil import parser as date_parser

    from app.models.campaign import Campaign
    from app.services.campaign_service import CampaignService

    campaign = (await db.execute(select(Campaign).where(Campaign.id == int(args["campaign_id"])))).scalar_one_or_none()
    if not campaign:
        return {"error": "Campaign not found"}
    try:
        when = date_parser.parse(str(args["scheduled_start_at"]))
    except Exception:
        return {"error": "I could not read that date."}
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    if when <= _now():
        return {"error": "That time is in the past. Pick a future date."}
    try:
        result = await CampaignService(db).schedule_campaign(campaign.id, when)
    except Exception as exc:
        return {"error": f"Could not schedule: {exc}"}
    await db.flush()
    return {
        "ok": True,
        "campaign": campaign.name,
        "scheduled_for": when.isoformat(),
        "status": campaign.status,
        "message": (result or {}).get("message") if isinstance(result, dict) else None,
    }


# ===========================================================================
# ADS MANAGER
# ===========================================================================
@tool("list_ads_campaigns", "campaigns", "List Ads Manager campaigns (sets, creatives and A/B experiments).", _obj(channel=S("sms | email"), status=S("draft | active | paused | completed")))
async def list_ads_campaigns(db, args, ctx):
    from app.models.ads import AdsCampaign

    stmt = select(AdsCampaign).order_by(AdsCampaign.id.desc()).limit(50)
    if args.get("channel"):
        stmt = stmt.where(AdsCampaign.channel == args["channel"])
    if args.get("status"):
        stmt = stmt.where(AdsCampaign.status == args["status"])
    rows = list((await db.execute(stmt)).scalars().all())
    return {
        "campaigns": [
            {
                "id": c.id,
                "name": c.name,
                "channel": c.channel,
                "status": c.status,
                "objective": c.objective,
                "launched_at": c.launched_at.isoformat() if getattr(c, "launched_at", None) else None,
            }
            for c in rows
        ]
    }


@tool(
    "create_ads_campaign",
    "campaigns",
    "Create an Ads Manager campaign draft.",
    _obj(name=S("Campaign name", _required=True), channel=S("sms | email"), objective=S("replies | leads | bookings | sales")),
)
async def create_ads_campaign(db, args, ctx):
    from app.models.ads import AdsCampaign

    name = (args.get("name") or "").strip()
    if not name:
        return {"error": "A campaign needs a name."}
    if len(name) > 255:
        return {"error": "That campaign name is longer than 255 characters. Shorten it."}
    campaign = AdsCampaign(
        name=name,
        channel=args.get("channel") or "sms",
        objective=args.get("objective") or "replies",
        status="draft",
    )
    db.add(campaign)
    await db.flush()
    return {"ok": True, "campaign": {"id": campaign.id, "name": campaign.name, "status": campaign.status}}


@tool(
    "launch_ads_campaign",
    "campaigns",
    "Launch an Ads Manager campaign (assigns the audience and starts sending). Asks for approval first.",
    _obj(campaign_id=I("Campaign id", _required=True)),
    danger=True,
    danger_label="Launch an Ads campaign",
)
async def launch_ads_campaign(db, args, ctx):
    from app.models.ads import AdsCampaign
    from app.services import ads_service

    campaign = (
        await db.execute(select(AdsCampaign).where(AdsCampaign.id == int(args["campaign_id"])))
    ).scalar_one_or_none()
    if not campaign:
        return {"error": "Campaign not found"}
    result = await ads_service.launch(db, campaign, actor="ai_agent")
    await db.flush()
    return result


@tool("ads_campaign_analytics", "campaigns", "Performance for one Ads campaign: sent, delivered, replies, interested, per creative.", _obj(campaign_id=I("Campaign id", _required=True)))
async def ads_campaign_analytics(db, args, ctx):
    from app.models.ads import AdsAssignment, AdsCampaign

    campaign_id = int(args["campaign_id"])
    campaign = (await db.execute(select(AdsCampaign).where(AdsCampaign.id == campaign_id))).scalar_one_or_none()
    if not campaign:
        return {"error": "Campaign not found"}
    totals = (
        await db.execute(
            select(AdsAssignment.send_status, func.count(AdsAssignment.id))
            .where(AdsAssignment.campaign_id == campaign_id)
            .group_by(AdsAssignment.send_status)
        )
    ).all()
    replies = (
        await db.execute(
            select(func.count(AdsAssignment.id)).where(
                AdsAssignment.campaign_id == campaign_id,
                AdsAssignment.reply_status.isnot(None),
            )
        )
    ).scalar() or 0
    return {
        "campaign": campaign.name,
        "status": campaign.status,
        "by_send_status": {status or "unknown": count for status, count in totals},
        "replied": replies,
    }


# ===========================================================================
# SENDING
# ===========================================================================
@tool(
    "send_sms",
    "sending",
    "Send ONE SMS now to a contact or number. Refuses opted-out or suppressed numbers. Asks for approval.",
    _obj(
        body=S("The message text", _required=True),
        contact_id=I("Contact to message"),
        to=S("Or a raw phone number"),
    ),
    danger=True,
    danger_label="Send an SMS now",
)
async def send_sms(db, args, ctx):
    from app.services.sms_service import SMSService
    from app.services.suppression_service import is_suppressed

    body = (args.get("body") or "").strip()
    if not body:
        return {"error": "The message body is empty."}
    contact = await _find_contact(db, contact_id=args.get("contact_id"), phone=args.get("to"))
    if not contact:
        return {"error": "I could not find that contact. Create them first, or give me a valid number."}
    refusal = _is_opted_out(contact, "sms")
    if refusal:
        return {"refused": True, "error": f"I did not send that. {refusal}", "contact_id": contact.id}
    if contact.phone_number and await is_suppressed(db, contact.phone_number):
        return {
            "refused": True,
            "error": "I did not send that. This number is on the suppression list.",
            "contact_id": contact.id,
        }
    try:
        message = await SMSService(db).send_message(contact.id, body)
    except Exception as exc:
        return {"error": f"The gateway refused the message: {exc}"}
    await db.flush()
    return {
        "ok": message is not None,
        "to": contact.phone_number,
        "status": getattr(message, "status", None),
        "message_id": getattr(message, "id", None),
    }


@tool(
    "send_email",
    "sending",
    "Send ONE email now. Refuses unsubscribed addresses. Asks for approval.",
    _obj(
        to=S("Email address"),
        subject=S("Subject", _required=True),
        body=S("Plain-text body", _required=True),
        html_body=S("Optional HTML body"),
        contact_id=I("Contact to email"),
    ),
    danger=True,
    danger_label="Send an email now",
)
async def send_email(db, args, ctx):
    from app.services import email_service

    contact = await _find_contact(db, contact_id=args.get("contact_id"), email=args.get("to"))
    if contact is None:
        return {"error": "I could not find that contact. Create them first, or use a saved contact."}
    refusal = _is_opted_out(contact, "email")
    if refusal:
        return {"refused": True, "error": f"I did not send that. {refusal}", "contact_id": contact.id}
    if not contact.email:
        return {"error": "That contact has no email address."}
    sender = await email_service.get_default_sender(db)
    if sender is None:
        return {"error": "No email sender is configured. Add one in Email Manager → Senders."}
    try:
        message, result = await email_service.send_now(
            db, contact,
            subject=args.get("subject") or "(no subject)",
            text_body=args.get("body") or "",
            html_body=args.get("html_body"),
            account=sender,
        )
    except Exception as exc:
        return {"error": f"The email provider refused the message: {exc}"}
    await db.flush()
    if message is None:
        return {"error": "The email was not queued.", "detail": result}
    return {"ok": True, "to": contact.email, "message_id": message.id}


@tool(
    "send_to_list",
    "sending",
    "Send one message to every reachable contact in a list right now. Always asks for approval with the audience size.",
    _obj(
        list_id=I("List id", _required=True),
        body=S("Message text", _required=True),
        channel=S("sms | email"),
        subject=S("Email subject"),
    ),
    danger=True,
    danger_label="Send to an entire list",
)
async def send_to_list(db, args, ctx):
    from app.models.contact import Contact
    from app.models.contact_list import ContactList, ContactListMember
    from app.services.sms_service import SMSService

    lst = (await db.execute(select(ContactList).where(ContactList.id == int(args["list_id"])))).scalar_one_or_none()
    if not lst:
        return {"error": "List not found"}
    channel = args.get("channel") or "sms"
    rows = list(
        (
            await db.execute(
                select(Contact)
                .join(ContactListMember, Contact.id == ContactListMember.contact_id)
                .where(ContactListMember.list_id == lst.id)
            )
        ).scalars().all()
    )
    reachable = [
        c for c in rows
        if _is_opted_out(c, channel) is None
        and (c.phone_number if channel == "sms" else c.email)
    ]
    if not reachable:
        return {"error": f"No contact in “{lst.name}” can be messaged on {channel}.", "checked": len(rows)}
    service = SMSService(db)
    sent = failed = 0
    for contact in reachable:
        if channel == "email":
            # Email goes through a campaign, which tracks per-sender daily limits
            # and warm-up; a raw loop would blow through both.
            return {
                "error": (
                    "For email, create a campaign on this list instead — that way the "
                    "daily sending limit and warm-up are respected."
                )
            }
        try:
            await service.send_message(contact.id, args["body"])
            sent += 1
        except Exception as exc:
            failed += 1
            logger.warning("agent send_to_list: %s failed for %s: %s", lst.name, contact.id, exc)
    await db.flush()
    return {"ok": True, "list": lst.name, "attempted": len(reachable), "sent": sent,
            "failed": failed, "skipped_opted_out": len(rows) - len(reachable)}


@tool("message_history", "sending", "Recent outbound messages, optionally for one contact.", _obj(contact_id=I("Contact id"), limit=I("How many (default 20)")))
async def message_history(db, args, ctx):
    from app.models.conversation import Message

    stmt = select(Message).where(Message.direction == "outgoing").order_by(Message.created_at.desc()).limit(int(args.get("limit") or 20))
    if args.get("contact_id"):
        stmt = stmt.where(Message.contact_id == int(args["contact_id"]))
    rows = list((await db.execute(stmt)).scalars().all())
    return {
        "messages": [
            {
                "id": m.id,
                "to_contact": m.contact_id,
                "channel": getattr(m, "channel", "sms"),
                "status": m.status,
                "body": (m.body or "")[:160],
                "at": m.created_at.isoformat(),
            }
            for m in rows
        ]
    }


# ===========================================================================
# INBOX
# ===========================================================================
@tool(
    "list_conversations",
    "inbox",
    "Read the inbox. status=unread today is usually what 'today's replies' means.",
    _obj(
        status=S("all | unread | interested | not_interested | opportunity | closed"),
        channel=S("sms | email"),
        search=S("Match contact name, phone or message text"),
        days=I("Only conversations active in the last N days"),
        limit=I("Max threads (default 20)"),
    ),
)
async def list_conversations(db, args, ctx):
    from app.models.contact import Contact
    from app.models.conversation import Conversation

    limit = max(1, min(int(args.get("limit") or 20), 100))
    stmt = (
        select(Conversation, Contact)
        .outerjoin(Contact, Conversation.contact_id == Contact.id)
        .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.id.desc())
        .limit(limit)
    )
    status = args.get("status")
    if status and status != "all":
        stmt = stmt.where(Conversation.status == status)
    if args.get("channel"):
        stmt = stmt.where(func.coalesce(Conversation.channel, "sms") == args["channel"])
    if args.get("days"):
        since = _now() - timedelta(days=int(args["days"]))
        stmt = stmt.where(Conversation.last_message_at >= since)
    if args.get("search"):
        needle = f"%{args['search']}%"
        stmt = stmt.where(
            or_(Contact.first_name.ilike(needle), Contact.last_name.ilike(needle),
                Contact.phone_number.ilike(needle), Conversation.last_message_preview.ilike(needle))
        )
    rows = (await db.execute(stmt)).all()
    return {
        "count": len(rows),
        "conversations": [
            {
                "id": conv.id,
                "contact_id": contact.id if contact else None,
                "contact": (
                    f"{contact.first_name or ''} {contact.last_name or ''}".strip()
                    if contact else "Unknown"
                ) or (contact.phone_number if contact else "Unknown"),
                "phone": contact.phone_number if contact else None,
                "channel": getattr(conv, "channel", "sms"),
                "status": conv.status,
                "unread": conv.unread_count,
                "last_message": (conv.last_message_preview or "")[:200],
                "at": conv.last_message_at.isoformat() if conv.last_message_at else None,
            }
            for conv, contact in rows
        ],
    }


@tool("get_conversation", "inbox", "A full conversation with its messages.", _obj(conversation_id=I("Conversation id", _required=True)))
async def get_conversation(db, args, ctx):
    from app.models.contact import Contact
    from app.models.conversation import Conversation, Message

    conversation = (
        await db.execute(select(Conversation).where(Conversation.id == int(args["conversation_id"])))
    ).scalar_one_or_none()
    if not conversation:
        return {"error": "Conversation not found"}
    contact = (
        await db.execute(select(Contact).where(Contact.id == conversation.contact_id))
    ).scalar_one_or_none()
    messages = list(
        (
            await db.execute(
                select(Message).where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at.asc()).limit(100)
            )
        ).scalars().all()
    )
    return {
        "conversation": {
            "id": conversation.id,
            "status": conversation.status,
            "contact": f"{contact.first_name or ''} {contact.last_name or ''}".strip() if contact else None,
            "phone": contact.phone_number if contact else None,
            "opted_out": bool(contact.is_opted_out) if contact else False,
        },
        "messages": [
            {
                "direction": m.direction,
                "body": m.body,
                "at": m.created_at.isoformat(),
                "intent": m.ai_intent,
                "sentiment": m.ai_sentiment,
            }
            for m in messages
        ],
    }


@tool(
    "reply_to_conversation",
    "inbox",
    "Reply in a conversation. Refuses if the contact has opted out.",
    _obj(conversation_id=I("Conversation id", _required=True), body=S("Reply text", _required=True)),
    danger=True,
    danger_label="Reply to a customer",
)
async def reply_to_conversation(db, args, ctx):
    from app.models.contact import Contact
    from app.models.conversation import Conversation
    from app.services.sms_service import SMSService
    from app.services.suppression_service import is_suppressed

    conversation = (
        await db.execute(select(Conversation).where(Conversation.id == int(args["conversation_id"])))
    ).scalar_one_or_none()
    if not conversation:
        return {"error": "Conversation not found"}
    contact = (await db.execute(select(Contact).where(Contact.id == conversation.contact_id))).scalar_one_or_none()
    if not contact:
        return {"error": "This conversation has no contact attached."}
    channel = getattr(conversation, "channel", "sms") or "sms"
    refusal = _is_opted_out(contact, channel)
    if refusal:
        return {"refused": True, "error": f"I did not send that. {refusal}"}
    if channel == "sms":
        if await is_suppressed(db, contact.phone_number):
            return {"refused": True, "error": "I did not send that. This number is suppressed."}
        if not contact.phone_number:
            return {"error": "That contact has no phone number."}
        await SMSService(db).send_message(contact.id, args["body"], conversation_id=conversation.id)
    else:
        from app.services import email_service

        sender = await email_service.get_default_sender(db)
        if sender is None:
            return {"error": "No email sender is configured."}
        if not contact.email:
            return {"error": "That contact has no email address."}
        await email_service.send_now(
            db, contact,
            subject=f"Re: {conversation.last_message_preview or 'your message'}"[:120],
            text_body=args["body"],
            account=sender,
            as_reply=True,
        )
    await db.flush()
    return {"ok": True, "conversation_id": conversation.id, "to": contact.phone_number or contact.email}


@tool(
    "set_conversation_status",
    "inbox",
    "Mark a conversation interested / not_interested / opportunity / closed / unread.",
    _obj(
        conversation_id=I("Conversation id", _required=True),
        status=S("interested | not_interested | opportunity | closed | unread | active", _required=True),
    ),
)
async def set_conversation_status(db, args, ctx):
    from app.models.conversation import Conversation

    conversation = (
        await db.execute(select(Conversation).where(Conversation.id == int(args["conversation_id"])))
    ).scalar_one_or_none()
    if not conversation:
        return {"error": "Conversation not found"}
    allowed = {"interested", "not_interested", "opportunity", "closed", "unread", "active"}
    status = (args.get("status") or "").lower()
    if status not in allowed:
        return {"error": f"status must be one of {sorted(allowed)}"}
    conversation.status = status
    if status == "unread":
        conversation.unread_count = max(1, conversation.unread_count or 0)
    elif status != "unread":
        conversation.unread_count = 0
    await db.flush()
    return {"ok": True, "conversation_id": conversation.id, "status": conversation.status}


@tool(
    "pause_followups_for_contact",
    "inbox",
    "Pause every automated follow-up for one contact (sequence steps, ads follow-ups).",
    _obj(contact_id=I("Contact id", _required=True), reason=S("Why")),
)
async def pause_followups_for_contact(db, args, ctx):
    from app.models.conversation import Conversation
    from app.services.sms_service import SMSService

    contact = await _find_contact(db, contact_id=args.get("contact_id"))
    if not contact:
        return {"error": "Contact not found"}
    await SMSService(db)._stop_seq(contact.id)
    conversations = list(
        (await db.execute(select(Conversation).where(Conversation.contact_id == contact.id))).scalars().all()
    )
    for conversation in conversations:
        conversation.sequence_paused = True
    await db.flush()
    return {"ok": True, "contact_id": contact.id, "paused_threads": len(conversations)}


# ===========================================================================
# CALENDAR
# ===========================================================================
@tool(
    "create_meeting",
    "calendar",
    "Book a meeting with a contact.",
    _obj(
        contact_id=I("Contact id"),
        title=S("Meeting title", _required=True),
        scheduled_at=S("ISO datetime", _required=True),
        duration_minutes=I("Length in minutes (default 30)"),
        location=S("Where / link"),
        notes=S("Agenda"),
    ),
)
async def create_meeting(db, args, ctx):
    from app.models.meeting import Meeting
    from dateutil import parser as date_parser

    try:
        when = date_parser.parse(str(args["scheduled_at"]))
    except Exception:
        return {"error": "I could not read that date."}
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    minutes = int(args.get("duration_minutes") or 30)
    meeting = Meeting(
        contact_id=args.get("contact_id"),
        title=(args.get("title") or "Meeting")[:255],
        description=args.get("notes"),
        starts_at=when,
        ends_at=when + timedelta(minutes=minutes),
        location=args.get("location"),
        status="scheduled",
    )
    db.add(meeting)
    await db.flush()
    return {
        "ok": True,
        "meeting": {
            "id": meeting.id, "title": meeting.title,
            "at": meeting.starts_at.isoformat(),
            "ends_at": meeting.ends_at.isoformat(),
        },
    }


@tool("list_meetings", "calendar", "Upcoming meetings.", _obj(days=I("How many days ahead (default 14)")))
async def list_meetings(db, args, ctx):
    from app.models.meeting import Meeting

    until = _now() + timedelta(days=int(args.get("days") or 14))
    rows = list(
        (
            await db.execute(
                select(Meeting).where(Meeting.starts_at <= until).order_by(Meeting.starts_at).limit(50)
            )
        ).scalars().all()
    )
    return {
        "meetings": [
            {"id": m.id, "title": m.title, "at": m.starts_at.isoformat(), "status": m.status}
            for m in rows
        ]
    }


# ===========================================================================
# INVOICES
# ===========================================================================
@tool("list_invoices", "invoices", "Recent invoices with amount, status and customer.", _obj(limit=I("How many (default 20)"), status=S("draft | sent | paid | overdue | cancelled")))
async def list_invoices(db, args, ctx):
    from app.models.invoice import Invoice

    stmt = select(Invoice).order_by(Invoice.id.desc()).limit(int(args.get("limit") or 20))
    if args.get("status"):
        stmt = stmt.where(Invoice.status == args["status"])
    rows = list((await db.execute(stmt)).scalars().all())
    return {
        "invoices": [
            {
                "id": i.id,
                "number": getattr(i, "number", None),
                "customer": getattr(i, "customer_name", None),
                "total_minor": getattr(i, "total_minor", None),
                "currency": getattr(i, "currency", "NGN"),
                "status": i.status,
                "public_link": f"/i/{i.public_token}" if getattr(i, "public_token", None) else None,
            }
            for i in rows
        ]
    }


@tool(
    "create_invoice",
    "invoices",
    "Create a draft invoice for a customer.",
    _obj(
        customer_name=S("Customer name", _required=True),
        customer_email=S("Customer email"),
        items=A("Line items as objects: {description, quantity, unit_price} — unit_price in major units (naira)"),
        notes=S("Notes shown on the invoice"),
        due_date=S("ISO date"),
    ),
    danger=True,
    danger_label="Create an invoice",
)
async def create_invoice(db, args, ctx):
    from app.models.invoice import Invoice, InvoiceItem
    from app.services.invoice_service import next_invoice_number

    items = args.get("items") or []
    if not isinstance(items, list) or not items:
        return {"error": "An invoice needs at least one line item: [{description, quantity, unit_price}]."}
    invoice = Invoice(
        number=await next_invoice_number(db),
        customer_name=args.get("customer_name"),
        customer_email=args.get("customer_email"),
        notes=args.get("notes"),
        status="draft",
        currency="NGN",
    )
    db.add(invoice)
    await db.flush()
    total = 0
    for item in items:
        quantity = float(item.get("quantity") or 1)
        unit = int(round(float(item.get("unit_price") or 0) * 100))
        line_total = int(round(quantity * unit))
        total += line_total
        db.add(
            InvoiceItem(
                invoice_id=invoice.id,
                description=str(item.get("description") or "Item")[:500],
                quantity=quantity,
                unit_price_minor=unit,
                amount_minor=line_total,
            )
        )
    invoice.subtotal_minor = total
    invoice.total_minor = total
    await db.flush()
    return {"ok": True, "invoice": {"id": invoice.id, "number": invoice.number, "total_minor": total}}


# ===========================================================================
# AUTOMATION
# ===========================================================================
@tool("list_automations", "automation", "Automation rules and auto-reply rules, with whether each is enabled.")
async def list_automations(db, args, ctx):
    from app.models.automation import Automation
    from app.models.autoreply import AutoReplyRule

    automations = list((await db.execute(select(Automation))).scalars().all())
    rules = list((await db.execute(select(AutoReplyRule))).scalars().all())
    return {
        "automations": [
            {"id": a.id, "name": a.name, "trigger": a.trigger_type,
             "enabled": bool(a.is_enabled)}
            for a in automations
        ],
        "auto_reply_rules": [
            {"id": r.id, "name": r.name, "keywords": r.keywords, "enabled": bool(r.is_enabled)}
            for r in rules
        ],
    }


# ===========================================================================
# ANALYTICS / OVERVIEW / SYSTEM
# ===========================================================================
@tool(
    "get_overview_metrics",
    "analytics",
    "The Overview numbers: sent, delivered, replies, unread, contacts, opt-outs and the daily series.",
    _obj(days=I("Window in days (default 30)"), channel=S("sms | email | all")),
)
async def get_overview_metrics(db, args, ctx):
    from fastapi import Request

    from app.api.v1.overview import unified_metrics

    return await unified_metrics(
        days=int(args.get("days") or 30),
        channel=args.get("channel"),
        db=db,
        current_user=ctx.user,
    )


@tool("get_analytics", "analytics", "Analytics overview for a period.", _obj(days=I("Window in days (default 30)")))
async def get_analytics(db, args, ctx):
    from app.api.v1.analytics import get_analytics_overview

    return await get_analytics_overview(
        days=int(args.get("days") or 30), channel=args.get("channel") or "all",
        campaign_id=None, db=db, current_user=ctx.user,
    )


@tool("get_dashboard", "analytics", "Dashboard totals and recent activity.")
async def get_dashboard(db, args, ctx):
    from app.api.v1.dashboard import get_dashboard_stats

    return await get_dashboard_stats(channel=None, days=30, db=db, current_user=ctx.user)


@tool(
    "list_system_errors",
    "system",
    "Errors the app recorded. Use for 'what went wrong today?'.",
    _obj(since_hours=I("Look back this many hours (default 24)"), limit=I("Max 50")),
)
async def list_system_errors(db, args, ctx):
    from app.models.system_error import SystemErrorRecord

    since = _now() - timedelta(hours=int(args.get("since_hours") or 24))
    rows = list(
        (
            await db.execute(
                select(SystemErrorRecord)
                .where(SystemErrorRecord.created_at >= since)
                .order_by(SystemErrorRecord.created_at.desc())
                .limit(min(int(args.get("limit") or 20), 50))
            )
        ).scalars().all()
    )
    return {
        "count": len(rows),
        "errors": [
            {
                "request_id": row.request_id,
                "code": row.code,
                "type": row.exception_type,
                "message": (row.message or "")[:300],
                "path": row.path,
                "method": row.method,
                "at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


@tool("get_system_status", "system", "Database, gateway, worker and scheduler health.")
async def get_system_status(db, args, ctx):
    from app import db_health

    return db_health.status.as_dict()


@tool("list_notifications", "system", "Recent in-app notifications.", _obj(limit=I("How many (default 10)")))
async def list_notifications(db, args, ctx):
    from app.models.notification import Notification

    rows = list(
        (
            await db.execute(
                select(Notification).order_by(Notification.created_at.desc()).limit(int(args.get("limit") or 10))
            )
        ).scalars().all()
    )
    return {
        "notifications": [
            {"id": n.id, "title": getattr(n, "title", None), "read": bool(getattr(n, "is_read", False)),
             "at": n.created_at.isoformat()}
            for n in rows
        ]
    }


@tool("list_suppressions", "system", "The suppression list (numbers that must never be messaged).", _obj(limit=I("How many (default 50)")))
async def list_suppressions(db, args, ctx):
    from app.models.suppression import SuppressionEntry

    rows = list(
        (
            await db.execute(
                select(SuppressionEntry).order_by(SuppressionEntry.id.desc()).limit(int(args.get("limit") or 50))
            )
        ).scalars().all()
    )
    return {
        "count": len(rows),
        "entries": [
            {"phone": s.phone_number, "reason": s.reason, "source": s.source,
             "at": s.suppressed_at.isoformat() if s.suppressed_at else None}
            for s in rows
        ],
    }


@tool("list_skills", "system", "Instruction skills the operator uploaded (invoked with @name).")
async def list_skills(db, args, ctx):
    from app.models.ai_agent import AIAgentSkill

    rows = list((await db.execute(select(AIAgentSkill).order_by(AIAgentSkill.name))).scalars().all())
    return {"skills": [{"name": s.name, "description": s.description} for s in rows]}


# ===========================================================================
# ACCESS TO EVERYTHING ELSE
# ===========================================================================
@tool(
    "app_api_request",
    "system",
    "Escape hatch: call any app API endpoint with the operator's own session. Use list_api_endpoints to discover paths. Path must start with /api/v1/.",
    _obj(
        method=S("GET | POST | PUT | PATCH | DELETE", _required=True),
        path=S("e.g. /api/v1/contacts/?limit=5", _required=True),
        body={"type": "object", "description": "JSON body for write requests"},
    ),
    danger=True,
    danger_label="Call an API endpoint directly",
)
async def app_api_request(db, args, ctx):
    """Call the app's own API in-process, authenticated as the operator.

    Implemented against the ASGI app rather than over loopback HTTP so it works
    identically in tests, on Render (where the port is not guessed) and behind a
    proxy — and so no extra network round trip or auth token is needed.
    """
    import httpx

    from app.main import app as fastapi_app
    from app.security.auth import create_access_token

    method = (args.get("method") or "GET").upper()
    path = args.get("path") or ""
    if not path.startswith("/api/v1/"):
        return {"error": "Path must start with /api/v1/"}
    token = create_access_token({"sub": str(ctx.user.id), "username": ctx.user.username})
    headers = {"Authorization": f"Bearer {token}", "Cookie": f"sendsms_session={token}"}
    try:
        transport = httpx.ASGITransport(app=fastapi_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent.local",
                                     headers=headers, timeout=60.0) as client:
            response = await client.request(method, path, json=args.get("body"))
        content_type = response.headers.get("content-type", "")
        payload: Any = response.json() if "json" in content_type else response.text[:4000]
        if isinstance(payload, dict) and len(json.dumps(payload, default=str)) > 12000:
            payload = {"truncated": True, "keys": list(payload.keys())}
        return {"status": response.status_code, "body": payload}
    except Exception as exc:
        return {"error": f"Request failed: {exc}"}


@tool("list_api_endpoints", "system", "Every API path the app exposes, grouped by area, for app_api_request.")
async def list_api_endpoints(db, args, ctx):
    from app.main import app as fastapi_app

    paths: dict[str, list[str]] = {}
    for route in fastapi_app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1/"):
            continue
        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            area = path.split("/")[3] if len(path.split("/")) > 3 else "root"
            paths.setdefault(area, [])
            entry = f"{method} {path}"
            if entry not in paths[area]:
                paths[area].append(entry)
    return {"areas": sorted(paths.keys()), "endpoints": paths}


# ---------------------------------------------------------------------------
# public helpers
# ---------------------------------------------------------------------------
def all_tools() -> list[Tool]:
    return list(REGISTRY.values())


def openai_schemas() -> list[dict]:
    return [t.schema() for t in REGISTRY.values()]


def gemini_schemas() -> list[dict]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "parameters": sanitise_schema(t.parameters),
        }
        for t in REGISTRY.values()
    ]


def areas() -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in REGISTRY.values():
        counts[t.area] = counts.get(t.area, 0) + 1
    return counts


def get(name: str) -> Optional[Tool]:
    return REGISTRY.get(name)


def danger_tools() -> set[str]:
    return {t.name for t in REGISTRY.values() if t.danger}
