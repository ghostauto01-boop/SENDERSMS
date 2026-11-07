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
* **Syncs are incremental.** The first pass mirrors a recent window; later
  passes ask Gmail for changes since ``historyId`` (or use IMAP ``UIDNEXT``).
  Gmail messages are fetched through the documented ``users.messages.get``
  method with bounded concurrency; there is no ``messages.batchGet`` endpoint.
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

from sqlalchemy import Select, delete, func, or_, select
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
#: How far back a fresh mirror or expired-history recovery goes. A whole Gmail
#: history in one pass would take minutes and mostly fill the database with newsletters.
FIRST_MIRROR_DAYS = 14


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
    """Mirror Gmail with paginated history and the documented messages.get API.

    A fresh profile watermark is captured before a full list, so mail arriving
    during the list is included by the next history request. Failed pages or
    message fetches never advance the watermark; successful rows remain safe to
    revisit because the Gmail message id is the mirror's unique key.
    """
    from app.services.mailbox_service import access_token_for

    token, error = await access_token_for(db, mailbox)
    if not token:
        return {"success": False, "error": error or "Could not refresh the Gmail API token",
                "seen": 0, "stored": 0, "errors": [error or "Gmail token unavailable"]}

    state = _cursor_state(mailbox)
    history_id = "" if force_full else str(state.get("mirror_history_id") or "")
    next_history = history_id
    needs_full_list = not bool(history_id)
    ids: list[str] = []
    deleted_ids: list[str] = []
    errors: list[str] = []

    if history_id:
        changes = await gmail_provider.gmail_history_changes(token, history_id)
        if changes.get("success"):
            data = changes.get("data") or {}
            for entry in data.get("history") or []:
                for field in ("messagesAdded", "labelsAdded", "labelsRemoved"):
                    for record in (entry or {}).get(field) or []:
                        message = (record or {}).get("message") or {}
                        if message.get("id"):
                            ids.append(str(message["id"]))
                for record in (entry or {}).get("messagesDeleted") or []:
                    message = (record or {}).get("message") or {}
                    if message.get("id"):
                        deleted_ids.append(str(message["id"]))
            next_history = str(data.get("historyId") or history_id)
        elif int(changes.get("status_code") or 0) == 404:
            # Gmail says an invalid/expired startHistoryId requires a full sync.
            needs_full_list = True
        else:
            detail = str(changes.get("error") or "Gmail history request failed")[:200]
            return {"success": False, "error": detail, "errors": [detail],
                    "seen": 0, "stored": 0}

    if needs_full_list:
        profile = await gmail_provider.gmail_profile(token)
        if profile.get("success") and profile.get("history_id"):
            next_history = str(profile["history_id"])
        else:
            errors.append(
                f"profile: {str(profile.get('error') or 'Gmail profile returned no historyId')[:200]}"
            )

        # Re-list the whole recovery window on an expired history cursor. The
        # natural-key upsert makes this safe and catches mail missed while idle.
        after = (_now() - timedelta(days=FIRST_MIRROR_DAYS)).strftime("%Y/%m/%d")
        for view in FOLDERS:
            label = gmail_provider.FOLDER_LABELS[view]
            listed = await gmail_provider.gmail_list_all_ids(
                token, label_ids=[label], query=f"after:{after}", page_size=500,
            )
            if not listed.get("success"):
                errors.append(f"{view}: {str(listed.get('error') or 'Gmail list failed')[:200]}")
            ids.extend(
                str(row.get("id")) for row in (listed.get("data") or {}).get("messages") or []
                if isinstance(row, dict) and row.get("id")
            )

    ids = list(dict.fromkeys(ids))
    deleted_ids = list(dict.fromkeys(deleted_ids))
    already = await _existing_ids(db, mailbox.id, ids)
    fresh = [message_id for message_id in ids if message_id not in already]

    stored = 0
    if fresh:
        fetched = await gmail_provider.gmail_batch_get(
            token, fresh, strip_quotes=False, include_attachments=False,
        )
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

    # Refresh every known message Gmail says changed, not only the first page.
    existing_changed = [message_id for message_id in ids if message_id in already]
    refresh_errors, missing_ids = await _refresh_known(
        db, mailbox, token, existing_changed,
    )
    errors.extend(refresh_errors)
    deleted_ids.extend(missing_ids)

    deleted_ids = list(dict.fromkeys(deleted_ids))
    if deleted_ids:
        for start in range(0, len(deleted_ids), 400):
            chunk = deleted_ids[start:start + 400]
            await db.execute(
                delete(MailboxMessage).where(
                    MailboxMessage.mailbox_id == mailbox.id,
                    MailboxMessage.provider_message_id.in_(chunk),
                )
            )

    if not errors and next_history:
        new_state = {
            **state,
            "mirror_history_id": next_history,
            "mirrored_at": _now().isoformat(),
        }
        mailbox.cursor_state = json.dumps(new_state)[:8000]
    await db.flush()
    return {"success": not errors, "seen": len(ids), "new": len(fresh),
            "stored": stored, "errors": errors[:10]}


async def _refresh_known(db: AsyncSession, mailbox: EmailMailbox, token: str,
                         pids: list[str]) -> tuple[list[str], list[str]]:
    """Refresh folders/flags for all changed rows and remove deleted messages."""
    if not pids:
        return [], []
    pids = list(dict.fromkeys(str(pid) for pid in pids if pid))
    fetched = await gmail_provider.gmail_batch_get(
        token, pids, strip_quotes=False, include_attachments=False,
    )
    rows = {
        row.provider_message_id: row
        for row in (
            await db.execute(
                select(MailboxMessage).where(
                    MailboxMessage.mailbox_id == mailbox.id,
                    MailboxMessage.provider_message_id.in_(pids),
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
    missing_ids = list(fetched.get("missing_ids") or [])
    if missing_ids:
        await db.execute(
            delete(MailboxMessage).where(
                MailboxMessage.mailbox_id == mailbox.id,
                MailboxMessage.provider_message_id.in_(missing_ids),
            )
        )
    return list(fetched.get("errors") or []), missing_ids


async def _mirror_imap(db: AsyncSession, mailbox: EmailMailbox, *,
                       force_full: bool = False) -> dict:
    """Mirror an app-password mailbox over IMAP without skipping failed mail."""
    password = decrypt_value(mailbox.credential_encrypted or "")
    if not password:
        return {"success": False, "error": "This mailbox has no stored app password",
                "seen": 0, "stored": 0, "errors": ["This mailbox has no stored app password"]}
    state = _cursor_state(mailbox)
    saved_cursors = state.get("mirror") if isinstance(state.get("mirror"), dict) else {}
    request_cursors = {} if force_full else dict(saved_cursors)
    result = await gmail_provider.imap_mirror_folders(
        mailbox.email_address, password,
        cursors=request_cursors,
        first_sync=force_full or not state.get("mirrored_at"),
    )
    items = result.get("items") or []
    errors = [str(error)[:300] for error in (result.get("errors") or []) if error]
    if result.get("error"):
        errors.append(str(result["error"])[:300])
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

    # The provider returns updates only for folders whose messages were fully
    # fetched. On a partial pass, ignore its aggregate cursor map and merge only
    # those safe per-folder updates over the saved state. Older providers/mocks
    # without cursor_updates are trusted only when the whole pass succeeded.
    cursor_updates = result.get("cursor_updates")
    if cursor_updates is None:
        cursor_updates = result.get("cursors") or {} if not errors else {}
    cursor_updates = dict(cursor_updates or {})
    completed = result.get("success") is not False and not errors
    if completed and force_full:
        cursors = dict(result.get("cursors") or cursor_updates)
    elif completed:
        cursors = {**saved_cursors, **dict(result.get("cursors") or cursor_updates)}
    else:
        cursors = {**saved_cursors, **cursor_updates}
    new_state = {**state, "mirror": cursors}
    if completed:
        new_state["mirrored_at"] = _now().isoformat()
    mailbox.cursor_state = json.dumps(new_state)[:8000]
    await db.flush()
    return {"success": completed, "seen": len(items), "new": len(items),
            "stored": stored, "errors": errors[:10]}


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
