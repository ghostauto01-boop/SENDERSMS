"""The mail app's data layer: a mirror of every connected Gmail account.

WHAT THIS IS FOR
----------------
The reply pipeline (:mod:`app.services.mailbox_service`) answers "which of these
messages is a reply to something we sent?" and stores only those, as CRM
messages. That is exactly right for a CRM and exactly wrong for a mail client:
an operator who wants to "select each Gmail account and see the replies it got,
the sent, the spam, even the drafts, and the entire inbox from all the accounts"
needs *everything*, in folders, with unread counts and a reading pane.

This module keeps that copy. It is deliberately a cache over Gmail:

* **Gmail stays the source of truth.** Every row carries Gmail's own message
  id and thread id, so a flag change, a move or a delete is applied to Gmail
  first and the row only reflects what Gmail reports back. Nothing here can
  delete mail.
* **Syncs are incremental.** The first pass mirrors a week; later passes ask for
  ids newer than the folder's watermark (IMAP ``UIDNEXT``, or the Gmail
  ``historyId``) and fetch bodies with ``users.messages.batchGet`` — 100
  messages per request — so "import as fast as it can" is a property of the
  sync, not a hope.
* **Bodies are truncated.** A newsletter is not worth megabytes per row;
  ``is_truncated`` says so and the reading pane pulls the full copy from Gmail
  on demand (:func:`full_body`).

Nothing in here re-implements threading or attribution. When a mirrored message
happens to be a CRM reply, :func:`link_conversation` points the row at the
existing conversation so the two views open each other.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_inbox import EmailMailbox
from app.models.mailbox_message import MailboxMessage
from app.providers import gmail as gmail_provider
from app.security.encryption import decrypt_value

logger = logging.getLogger(__name__)

#: The five folders the mail app shows, in the order Gmail's sidebar shows them.
FOLDERS = ("INBOX", "SENT", "SPAM", "DRAFTS", "TRASH")
#: Folder keys accepted from the API. ``ALL`` is every folder at once, which is
#: what "the entire inbox with all the inbox from all the accounts" means.
FOLDER_KEYS = FOLDERS + ("ALL", "STARRED", "UNREAD")

#: How much of a body is stored. Beyond this the row says ``is_truncated`` and
#: the reading pane fetches the rest from Gmail.
TEXT_LIMIT = 60_000
HTML_LIMIT = 200_000
#: How far back the first mirror goes. A whole Gmail history in one pass would
#: take minutes and mostly fill the database with newsletters.
FIRST_MIRROR_DAYS = 14
#: Per-folder cap on a normal (incremental) mirror pass.
MIRROR_BATCH = gmail_provider.MAX_PER_SYNC


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Mirroring
# ---------------------------------------------------------------------------


def _cursor_state(mailbox: EmailMailbox) -> dict:
    try:
        state = json.loads(mailbox.cursor_state or "{}")
        return state if isinstance(state, dict) else {}
    except ValueError:
        return {}


def _json_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed]
        except ValueError:
            return [value]
    return []


async def _existing_ids(db: AsyncSession, mailbox_id: int, pids: list[str]) -> set[str]:
    """Which of these Gmail ids are already mirrored — the dedupe filter."""
    if not pids:
        return set()
    found: set[str] = set()
    for start in range(0, len(pids), 400):
        chunk = pids[start:start + 400]
        rows = (
            await db.execute(
                select(MailboxMessage.provider_message_id).where(
                    MailboxMessage.mailbox_id == mailbox_id,
                    MailboxMessage.provider_message_id.in_(chunk),
                )
            )
        ).scalars().all()
        found.update(rows)
    return found


def _upsert(row: MailboxMessage | None, mailbox: EmailMailbox, item: dict,
            *, folder: str, body_text: str, body_html: str | None) -> MailboxMessage:
    """Write one normalized message into its row, creating it when new."""
    labels = [str(x) for x in (item.get("_labels") or [])]
    to_values = [t.get("Address") if isinstance(t, dict) else str(t) for t in (item.get("To") or [])]
    to_values = [str(v).strip().lower() for v in to_values if v]
    from_header = item.get("From") or {}
    sent_at = None
    if item.get("SentAtDate"):
        try:
            sent_at = datetime.fromisoformat(str(item["SentAtDate"]))
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=timezone.utc)
        except ValueError:
            sent_at = None
    truncated = len(body_text) > TEXT_LIMIT or len(body_html or "") > HTML_LIMIT

    if row is None:
        row = MailboxMessage(
            mailbox_id=int(mailbox.id),
            provider_message_id=str(item.get("_provider_message_id") or "")[:120],
            first_seen_at=_now(),
        )
    row.thread_id = str(item.get("_thread_id") or "")[:120] or None
    row.folder = folder
    row.labels = json.dumps(labels)[:1000] if labels else None
    row.subject = str(item.get("Subject") or "")[:600] or None
    row.from_name = str(from_header.get("Name") or "")[:255] or None
    row.from_address = str(from_header.get("Address") or "")[:255] or None
    row.to_addresses = json.dumps(to_values)[:4000] if to_values else None
    row.cc_addresses = json.dumps(_json_list(item.get("_cc")))[:2000] or None
    row.date = sent_at or _now()
    row.snippet = str(item.get("_snippet") or body_text or "")[:600] or None
    row.body_text = body_text[:TEXT_LIMIT] or None
    row.body_html = (body_html or "")[:HTML_LIMIT] or None
    row.is_truncated = truncated
    row.attachment_count = len(item.get("Attachments") or [])
    headers = item.get("Headers") or {}
    row.rfc_message_id = str(headers.get("Message-ID") or item.get("MessageId") or "")[:400] or None
    row.in_reply_to = str(headers.get("In-Reply-To") or item.get("InReplyTo") or "")[:400] or None
    row.references = str(headers.get("References") or item.get("References") or "")[:2000] or None

    # Read/starred state comes from Gmail's labels over the API and from IMAP
    # flags on the other door; either way Gmail's answer wins over ours.
    if labels:
        row.is_read = "UNREAD" not in {x.upper() for x in labels}
        row.is_starred = "STARRED" in {x.upper() for x in labels}
    elif "_is_read" in item or "_is_starred" in item:
        row.is_read = bool(item.get("_is_read"))
        row.is_starred = bool(item.get("_is_starred"))
    return row


async def link_conversation(db: AsyncSession, row: MailboxMessage, item: dict) -> None:
    """Point a mirrored message at the CRM conversation it belongs to, if any.

    One lookup, and only for inbound mail: a message whose ``In-Reply-To`` or
    ``References`` names a Message-ID this app sent is that conversation's
    reply, which is what puts the "in SENDERSMS" badge on it in the mail app and
    lets the reading pane jump to the CRM thread.
    """
    headers = item.get("Headers") or {}
    ids = [
        str(headers.get("In-Reply-To") or item.get("InReplyTo") or ""),
        str(headers.get("References") or item.get("References") or ""),
    ]
    candidates = [token for chunk in ids for token in chunk.split() if "@" in token or "<" in token]
    if not candidates:
        return
    from app.services import email_service

    parent = await email_service.find_outgoing_email_by_message_ids(
        db, candidates[0] if candidates else None, " ".join(candidates) or None
    )
    if parent is None:
        return
    row.conversation_id = parent.conversation_id
    row.contact_id = parent.contact_id


async def _mirror_gmail_api(db: AsyncSession, mailbox: EmailMailbox, *,
                            force_full: bool = False) -> dict:
    """Mirror one Gmail-API mailbox using historyId, then batchGet.

    The order of operations is what makes this fast *and* correct:

    1. ask ``users.history`` for every message id touched since the watermark —
       one request covering all five folders;
    2. if there is no watermark (first mirror, or Gmail expired the history),
       fall back to listing ids per folder with a date window;
    3. download the unknown ids with ``batchGet``, 100 at a time, 3 in flight;
    4. store, and only then advance the watermark.
    """
    from app.services.mailbox_service import access_token_for

    token, error = await access_token_for(db, mailbox)
    if not token:
        return {"success": False, "error": error or "Could not refresh the Gmail API token",
                "seen": 0, "stored": 0}

    state = _cursor_state(mailbox)
    history_id = "" if force_full else str(state.get("mirror_history_id") or "")
    ids: list[str] = []
    errors: list[str] = []

    if history_id:
        changes = await gmail_provider.gmail_history_changes(token, history_id)
        if changes.get("success"):
            for entry in changes["data"].get("history") or []:
                for kind in ("messageAdded", "labelAdded", "labelRemoved"):
                    record = (entry or {}).get(kind) or {}
                    message = record.get("message") or {}
                    if message.get("id"):
                        ids.append(str(message["id"]))
            history_id = str(changes["data"].get("historyId") or history_id)
        else:
            # An expired historyId is normal after ~7 idle days. Fall through to
            # a folder listing rather than reporting a failure.
            history_id = ""

    if not history_id:
        window = FIRST_MIRROR_DAYS if not state.get("mirrored_at") else 3
        after = (_now() - timedelta(days=window)).strftime("%Y/%m/%d")
        for view in FOLDERS:
            label = gmail_provider.FOLDER_LABELS[view]
            listed = await gmail_provider.gmail_list_ids(
                token, label_ids=[label], query=f"after:{after}", max_results=MIRROR_BATCH,
            )
            if not listed.get("success"):
                errors.append(f"{view}: {listed.get('error')}")
                continue
            ids.extend(str(m.get("id")) for m in listed["data"].get("messages") or [] if m.get("id"))
        profile = await gmail_provider.gmail_profile(token)
        history_id = str((profile or {}).get("history_id") or "")

    ids = list(dict.fromkeys(ids))
    already = await _existing_ids(db, mailbox.id, ids)
    fresh = [i for i in ids if i not in already]

    stored = 0
    if fresh:
        fetched = await gmail_provider.gmail_batch_get(token, fresh, strip_quotes=False)
        errors.extend(fetched.get("errors") or [])
        for item in fetched.get("items") or []:
            pid = str(item.get("_provider_message_id") or "")
            if not pid:
                continue
            folder = str(item.get("_folder") or "OTHER").upper()
            row = MailboxMessage(
                mailbox_id=int(mailbox.id), provider_message_id=pid[:120], first_seen_at=_now()
            )
            _upsert(row, mailbox, item, folder=folder,
                    body_text=str(item.get("RawTextBody") or ""),
                    body_html=item.get("RawHtmlBody"))
            await link_conversation(db, row, item)
            db.add(row)
            stored += 1

    # Refresh the folders/labels of rows Gmail moved since we last looked: a
    # message that was in the Inbox and is now in Spam must move here too.
    if ids:
        await _refresh_known(db, mailbox, token, [i for i in ids if i in already])

    new_state = {**state, "mirror_history_id": history_id, "mirrored_at": _now().isoformat()}
    mailbox.cursor_state = json.dumps(new_state)[:8000]
    await db.flush()
    return {"success": True, "seen": len(ids), "new": len(fresh), "stored": stored,
            "errors": errors[:5]}


async def _refresh_known(db: AsyncSession, mailbox: EmailMailbox, token: str,
                         pids: list[str]) -> None:
    """Re-read the labels of messages we already have, so folders stay true."""
    if not pids:
        return
    fetched = await gmail_provider.gmail_batch_get(token, pids[:MIRROR_BATCH], strip_quotes=False)
    if not fetched.get("success"):
        return
    rows = {
        row.provider_message_id: row
        for row in (
            await db.execute(
                select(MailboxMessage).where(
                    MailboxMessage.mailbox_id == mailbox.id,
                    MailboxMessage.provider_message_id.in_(pids[:MIRROR_BATCH]),
                )
            )
        ).scalars().all()
    }
    for item in fetched.get("items") or []:
        row = rows.get(str(item.get("_provider_message_id") or ""))
        if row is None:
            continue
        labels = {str(x).upper() for x in (item.get("_labels") or [])}
        row.folder = str(item.get("_folder") or row.folder).upper()
        row.labels = json.dumps(sorted(labels))[:1000] if labels else None
        if labels:
            row.is_read = "UNREAD" not in labels
            row.is_starred = "STARRED" in labels


async def _mirror_imap(db: AsyncSession, mailbox: EmailMailbox, *,
                       force_full: bool = False) -> dict:
    """Mirror an app-password mailbox over IMAP."""
    password = decrypt_value(mailbox.credential_encrypted or "")
    if not password:
        return {"success": False, "error": "This mailbox has no stored app password",
                "seen": 0, "stored": 0}
    state = _cursor_state(mailbox)
    result = await gmail_provider.imap_mirror_folders(
        mailbox.email_address, password,
        cursors={} if force_full else (state.get("mirror") or {}),
        first_sync=force_full or not state.get("mirrored_at"),
    )
    items = result.get("items") or []
    stored = 0
    already = await _existing_ids(
        db, mailbox.id, [str(i.get("_provider_message_id") or "") for i in items]
    )
    for item in items:
        pid = str(item.get("_provider_message_id") or "")
        if not pid:
            continue
        folder = str(item.get("_folder") or "INBOX").upper()
        if pid in already:
            row = (
                await db.execute(
                    select(MailboxMessage).where(
                        MailboxMessage.mailbox_id == mailbox.id,
                        MailboxMessage.provider_message_id == pid,
                    )
                )
            ).scalar_one_or_none()
        else:
            row = None
        row = _upsert(row, mailbox, item, folder=folder,
                      body_text=str(item.get("_raw_text") or item.get("RawTextBody") or ""),
                      body_html=item.get("RawHtmlBody"))
        await link_conversation(db, row, item)
        db.add(row)
        stored += 1
    new_state = {**state, "mirror": result.get("cursors") or {}, "mirrored_at": _now().isoformat()}
    mailbox.cursor_state = json.dumps(new_state)[:8000]
    await db.flush()
    return {"success": True, "seen": len(items), "new": len(items), "stored": stored,
            "errors": (result.get("errors") or [])[:5]}


async def mirror_mailbox(db: AsyncSession, mailbox: EmailMailbox, *,
                         force_full: bool = False) -> dict:
    """Bring one mailbox's mirror up to date. Never raises."""
    try:
        if mailbox.provider == "gmail_api":
            result = await _mirror_gmail_api(db, mailbox, force_full=force_full)
        else:
            result = await _mirror_imap(db, mailbox, force_full=force_full)
    except Exception as exc:  # noqa: BLE001 — a mirror failure must not break a sync
        logger.warning("MAIL %s mirror failed: %s", mailbox.email_address, exc)
        return {"success": False, "error": str(exc)[:300], "seen": 0, "stored": 0}
    result["mailbox_id"] = mailbox.id
    result["email"] = mailbox.email_address
    return result


async def mirror_all(db: AsyncSession) -> list[dict]:
    from app.services.mailbox_service import list_mailboxes

    return [await mirror_mailbox(db, m) for m in await list_mailboxes(db)]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def serialize(row: MailboxMessage, mailbox: EmailMailbox | None = None, *,
              body: bool = False) -> dict:
    """What the mail app receives. Bodies only when the reading pane asks."""
    out: dict[str, Any] = {
        "id": row.id,
        "mailbox_id": row.mailbox_id,
        "mailbox_email": mailbox.email_address if mailbox else None,
        "mailbox_name": mailbox.name if mailbox else None,
        "provider_message_id": row.provider_message_id,
        "thread_id": row.thread_id,
        "folder": row.folder,
        "labels": _json_list(row.labels),
        "subject": row.subject or "(no subject)",
        "from_name": row.from_name,
        "from_address": row.from_address,
        "to": _json_list(row.to_addresses),
        "cc": _json_list(row.cc_addresses),
        "date": row.date.isoformat() if row.date else None,
        "snippet": row.snippet,
        "is_read": bool(row.is_read),
        "is_starred": bool(row.is_starred),
        "attachments": int(row.attachment_count or 0),
        "conversation_id": row.conversation_id,
        "contact_id": row.contact_id,
        "is_truncated": bool(row.is_truncated),
    }
    if body:
        out["body_text"] = row.body_text
        out["body_html"] = row.body_html
    return out


def _base_query(db: AsyncSession, *, mailbox_id: int | None) -> Select:
    query = select(MailboxMessage)
    if mailbox_id:
        query = query.where(MailboxMessage.mailbox_id == mailbox_id)
    return query


def _apply_folder(query: Select, folder: str) -> Select:
    folder = (folder or "INBOX").upper()
    if folder == "ALL":
        return query.where(MailboxMessage.folder != "TRASH")
    if folder == "UNREAD":
        return query.where(MailboxMessage.is_read.is_(False),
                           MailboxMessage.folder != "TRASH")
    if folder == "STARRED":
        return query.where(MailboxMessage.is_starred.is_(True),
                           MailboxMessage.folder != "TRASH")
    return query.where(MailboxMessage.folder == folder)


async def list_messages(
    db: AsyncSession,
    *,
    mailbox_id: int | None = None,
    folder: str = "INBOX",
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
    contact_id: int | None = None,
    conversation_id: int | None = None,
) -> dict:
    """A page of messages, newest first — the middle column of the mail app."""
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    stmt = _apply_folder(_base_query(db, mailbox_id=mailbox_id), folder)
    if contact_id:
        stmt = stmt.where(MailboxMessage.contact_id == int(contact_id))
    if conversation_id:
        stmt = stmt.where(MailboxMessage.conversation_id == int(conversation_id))
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                MailboxMessage.subject.ilike(like),
                MailboxMessage.from_address.ilike(like),
                MailboxMessage.from_name.ilike(like),
                MailboxMessage.body_text.ilike(like),
                MailboxMessage.snippet.ilike(like),
            )
        )
    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(
                MailboxMessage.date.desc().nullslast(), MailboxMessage.id.desc()
            ).limit(limit).offset(offset)
        )
    ).scalars().all()
    mailboxes = {m.id: m for m in await _mailboxes(db)}
    return {
        "items": [serialize(row, mailboxes.get(row.mailbox_id)) for row in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "folder": (folder or "INBOX").upper(),
    }


async def _mailboxes(db: AsyncSession) -> list[EmailMailbox]:
    from app.services.mailbox_service import list_mailboxes

    return await list_mailboxes(db, include_inactive=True)


async def thread_of(db: AsyncSession, mailbox_id: int, thread_id: str) -> list[MailboxMessage]:
    rows = (
        await db.execute(
            select(MailboxMessage)
            .where(MailboxMessage.mailbox_id == int(mailbox_id),
                   MailboxMessage.thread_id == str(thread_id))
            .order_by(MailboxMessage.date.asc().nullsfirst(), MailboxMessage.id.asc())
        )
    ).scalars().all()
    return list(rows)


async def read_message(db: AsyncSession, mailbox_id: int, provider_message_id: str,
                       *, with_body: bool = True) -> dict | None:
    """One message, its thread, and — when asked — the full body from Gmail."""
    row = (
        await db.execute(
            select(MailboxMessage).where(
                MailboxMessage.mailbox_id == int(mailbox_id),
                MailboxMessage.provider_message_id == str(provider_message_id),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    mailbox = next((m for m in await _mailboxes(db) if m.id == row.mailbox_id), None)
    if with_body and (row.is_truncated or not row.body_text):
        await full_body(db, mailbox, row)
    siblings = await thread_of(db, mailbox_id, row.thread_id or "") if row.thread_id else []
    payload = serialize(row, mailbox, body=True)
    payload["thread"] = [serialize(s, mailbox, body=(s.id == row.id)) for s in siblings]
    return payload


async def full_body(db: AsyncSession, mailbox: EmailMailbox | None, row: MailboxMessage) -> None:
    """Pull the untruncated body straight from Gmail and update the row."""
    if mailbox is None or mailbox.provider != "gmail_api":
        return
    from app.services.mailbox_service import access_token_for

    token, error = await access_token_for(db, mailbox)
    if not token:
        logger.info("MAIL %s: full body unavailable (%s)", mailbox.email_address, error)
        return
    fetched = await gmail_provider.gmail_get_message(
        token, row.provider_message_id, strip_quotes=False
    )
    if not fetched.get("success"):
        return
    item = fetched["item"]
    row.body_text = str(item.get("RawTextBody") or "")[:TEXT_LIMIT] or None
    row.body_html = str(item.get("RawHtmlBody") or "")[:HTML_LIMIT] or None
    row.is_truncated = (
        len(str(item.get("RawTextBody") or "")) > TEXT_LIMIT
        or len(str(item.get("RawHtmlBody") or "")) > HTML_LIMIT
    )
    await db.flush()


# ---------------------------------------------------------------------------
# Acting on a message — Gmail first, then the row
# ---------------------------------------------------------------------------


async def _mailbox_or_none(db: AsyncSession, mailbox_id: int) -> EmailMailbox | None:
    return next((m for m in await _mailboxes(db) if m.id == int(mailbox_id)), None)


async def _row_or_none(db: AsyncSession, mailbox_id: int,
                       provider_message_id: str) -> MailboxMessage | None:
    return (
        await db.execute(
            select(MailboxMessage).where(
                MailboxMessage.mailbox_id == int(mailbox_id),
                MailboxMessage.provider_message_id == str(provider_message_id),
            )
        )
    ).scalar_one_or_none()


async def set_flags(db: AsyncSession, mailbox_id: int, provider_message_id: str, *,
                    read: bool | None = None, starred: bool | None = None) -> dict:
    """Mark read/unread/starred in Gmail, then in the mirror."""
    row = await _row_or_none(db, mailbox_id, provider_message_id)
    if row is None:
        return {"success": False, "error": "Message not found"}
    mailbox = await _mailbox_or_none(db, mailbox_id)
    add: list[str] = []
    remove: list[str] = []
    if read is True:
        remove.append("UNREAD")
    elif read is False:
        add.append("UNREAD")
    if starred is True:
        add.append("STARRED")
    elif starred is False:
        remove.append("STARRED")
    if mailbox is not None and mailbox.provider == "gmail_api":
        from app.services.mailbox_service import access_token_for

        token, _ = await access_token_for(db, mailbox)
        if token:
            await gmail_provider.gmail_modify(
                token, row.provider_message_id, add=add or None, remove=remove or None
            )
    if read is not None:
        row.is_read = bool(read)
    if starred is not None:
        row.is_starred = bool(starred)
    await db.flush()
    return {"success": True, "is_read": row.is_read, "is_starred": row.is_starred}


async def move(db: AsyncSession, mailbox_id: int, provider_message_id: str,
               folder: str) -> dict:
    """Move a message between folders — labels in Gmail, ``folder`` here."""
    target = (folder or "").upper()
    if target not in FOLDERS:
        return {"success": False, "error": f"Unknown folder '{folder}'"}
    row = await _row_or_none(db, mailbox_id, provider_message_id)
    if row is None:
        return {"success": False, "error": "Message not found"}
    mailbox = await _mailbox_or_none(db, mailbox_id)
    label = gmail_provider.FOLDER_LABELS[target]
    if mailbox is not None and mailbox.provider == "gmail_api":
        from app.services.mailbox_service import access_token_for

        token, _ = await access_token_for(db, mailbox)
        if token:
            remove = [gmail_provider.FOLDER_LABELS[f] for f in FOLDERS if f != target]
            if target == "TRASH":
                await gmail_provider.gmail_trash(token, row.provider_message_id)
            else:
                await gmail_provider.gmail_modify(
                    token, row.provider_message_id, add=[label], remove=remove or None
                )
    row.folder = target
    await db.flush()
    return {"success": True, "folder": target}


async def delete(db: AsyncSession, mailbox_id: int, provider_message_id: str) -> dict:
    """Trash in Gmail (recoverable), remove from the mirror.

    Gmail's own Delete is a move to Trash, not an erase, and this keeps that
    behaviour: nothing is destroyed by pressing the button in the app.
    """
    row = await _row_or_none(db, mailbox_id, provider_message_id)
    if row is None:
        return {"success": False, "error": "Message not found"}
    mailbox = await _mailbox_or_none(db, mailbox_id)
    if mailbox is not None and mailbox.provider == "gmail_api":
        from app.services.mailbox_service import access_token_for

        token, _ = await access_token_for(db, mailbox)
        if token:
            await gmail_provider.gmail_trash(token, row.provider_message_id)
    row.folder = "TRASH"
    await db.flush()
    return {"success": True, "folder": "TRASH"}


async def send_reply(
    db: AsyncSession,
    mailbox_id: int,
    provider_message_id: str,
    *,
    body: str,
    html: str | None = None,
    to_all: bool = False,
) -> dict:
    """Reply to a mirrored message **from the account that received it**.

    This is the rule, enforced in one place: the reply leaves from the Gmail
    address the original landed in — never from a Brevo sender, and never from
    "whichever Gmail happens to be connected". ``In-Reply-To``/``References``
    keep it inside the same thread in both mail clients.
    """
    from app.services import mailbox_service

    row = await _row_or_none(db, mailbox_id, provider_message_id)
    if row is None:
        return {"success": False, "error": "Message not found"}
    mailbox = await _mailbox_or_none(db, mailbox_id)
    if mailbox is None:
        return {"success": False, "error": "That mailbox is no longer connected"}

    recipient = (row.from_address or "").strip()
    if not recipient:
        return {"success": False, "error": "That message has no sender address to reply to"}
    cc = [a for a in _json_list(row.to_addresses)
          if a.lower() != (mailbox.email_address or "").lower()] if to_all else []
    subject = row.subject or "(no subject)"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    result = await mailbox_service.send_via_mailbox(
        db, mailbox,
        to_address=recipient,
        to_name=row.from_name,
        subject=subject[:500],
        text=body,
        html=html,
        in_reply_to=row.rfc_message_id,
        references=" ".join(
            [t for t in ((row.references or "").split() + ([row.rfc_message_id] if row.rfc_message_id else []))
             if t]
        )[:2000] or None,
        from_name=mailbox.name,
        cc=cc or None,
        headers={"Reply-To": await _reply_to_for(db, mailbox)},
    )
    if not result.get("success"):
        return result
    row.is_read = True
    await db.flush()
    return {"success": True, "from": mailbox.email_address, "to": recipient,
            "provider_message_id": result.get("provider_message_id")}


async def _reply_to_for(db: AsyncSession, mailbox: EmailMailbox) -> str:
    """The mailbox that sent it, with the Brevo sender attached behind it."""
    from app.services.email_service import mailbox_reply_to

    return await mailbox_reply_to(db, None, mailbox)


# ---------------------------------------------------------------------------
# Counts and the account rail
# ---------------------------------------------------------------------------


async def folder_counts(db: AsyncSession, *, mailbox_id: int | None = None) -> dict:
    """``{folder: {"total": n, "unread": n}}`` for one account or all of them."""
    rows = (
        await db.execute(
            select(
                MailboxMessage.folder,
                MailboxMessage.is_read,
                func.count(),
            )
            .where(
                *(
                    [MailboxMessage.mailbox_id == int(mailbox_id)] if mailbox_id else []
                )
            )
            .group_by(MailboxMessage.folder, MailboxMessage.is_read)
        )
    ).all()
    counts: dict[str, dict[str, int]] = {
        folder: {"total": 0, "unread": 0} for folder in FOLDERS
    }
    for folder, is_read, total in rows:
        key = (folder or "OTHER").upper()
        if key not in counts:
            counts[key] = {"total": 0, "unread": 0}
        counts[key]["total"] += int(total or 0)
        if not is_read:
            counts[key]["unread"] += int(total or 0)
    counts["ALL"] = {
        "total": sum(v["total"] for k, v in counts.items() if k in FOLDERS and k != "TRASH"),
        "unread": sum(v["unread"] for k, v in counts.items() if k in FOLDERS and k != "TRASH"),
    }
    return counts


async def accounts_overview(db: AsyncSession) -> dict:
    """The left rail: every account, its folders, and its sync health."""
    from app.services.mailbox_service import list_mailboxes, serialize_mailbox

    mailboxes = await list_mailboxes(db, include_inactive=True)
    ids = [int(m.id) for m in mailboxes]
    rows = (
        await db.execute(
            select(MailboxMessage.mailbox_id, MailboxMessage.folder, MailboxMessage.is_read,
                   func.count())
            .where(MailboxMessage.mailbox_id.in_(ids) if ids else False)
            .group_by(MailboxMessage.mailbox_id, MailboxMessage.folder, MailboxMessage.is_read)
        )
    ).all() if ids else []

    per_account: dict[int, dict[str, dict[str, int]]] = {}
    for mailbox_id, folder, is_read, total in rows:
        bucket = per_account.setdefault(
            int(mailbox_id), {f: {"total": 0, "unread": 0} for f in FOLDERS}
        )
        key = (folder or "OTHER").upper()
        bucket.setdefault(key, {"total": 0, "unread": 0})
        bucket[key]["total"] += int(total or 0)
        if not is_read:
            bucket[key]["unread"] += int(total or 0)

    accounts = []
    for mailbox in mailboxes:
        counts = per_account.get(int(mailbox.id), {f: {"total": 0, "unread": 0} for f in FOLDERS})
        counts["ALL"] = {
            "total": sum(v["total"] for k, v in counts.items() if k != "TRASH"),
            "unread": sum(v["unread"] for k, v in counts.items() if k != "TRASH"),
        }
        accounts.append({
            **serialize_mailbox(mailbox),
            "counts": counts,
            "needs_reconnect": bool(getattr(mailbox, "last_error", None)) and
                                "invalid_grant" in (mailbox.last_error or "").lower(),
        })
    total_unread = sum(a["counts"]["ALL"]["unread"] for a in accounts if a["is_active"])
    return {
        "accounts": accounts,
        "all": await folder_counts(db),
        "total_unread": total_unread,
        "folder_keys": list(FOLDER_KEYS),
    }
