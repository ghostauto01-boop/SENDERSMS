"""
Gmail, by two doors: the Gmail REST API (OAuth) and IMAP/SMTP (app password).

WHY BOTH
--------
The API is the better door — it can move a message out of Spam with a single
label change, *create* a Gmail filter whose action is "never send it to Spam",
and push notifications instead of polling. But it costs the operator a Google
Cloud project and an OAuth consent screen. An IMAP app password costs one
toggle in Google account settings, works in five minutes, and can do everything
the reply workflow actually needs, including moving a message out of Spam (copy
to INBOX, then delete from Spam). So the app supports both and uses whichever
the operator connected.

WHAT BOTH PRODUCE
-----------------
One normalized message dict per reply, in the same shape Brevo's inbound parser
posts (``From``/``To``/``Subject``/``MessageId``/``InReplyTo``/``RawTextBody``/
``RawHtmlBody``/``Attachments``/``SentAtDate``/``Headers``). That matters: there
is exactly one ingestion pipeline in the app
(:func:`app.services.email_service.process_inbound_email`), and a reply that
arrives through Brevo, through Gmail's API or through IMAP threads, attributes,
auto-replies and notifies identically. Two extra keys ride along for the
mailbox-specific decisions: ``_folder`` (so a Spam message can be rescued) and
``_provider_message_id`` (Gmail's own id, for label changes).

No new dependencies: IMAP/SMTP use the standard library, run off the event loop
with ``asyncio.to_thread`` because ``imaplib`` blocks.
"""

from __future__ import annotations

import asyncio
import base64
import email
import email.header
import email.policy  # `email.policy.default` is not pulled in by `import email`
import imaplib
import json
import logging
import re
import smtplib
import socket
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid, parseaddr, parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

#: Full-mailbox access. Narrower scopes exist (gmail.modify, gmail.readonly,
#: gmail.send) but the reply workflow needs all three at once — read INBOX and
#: Spam, change labels, and send — so one scope is simpler to reason about and
#: to approve.
GMAIL_SCOPE = "https://mail.google.com/"

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
#: Every socket gets a timeout. ``imaplib`` defaults to *no timeout*, so a
#: deployment behind a firewall that drops outbound 993 would hang the request
#: (and the Celery worker thread) indefinitely instead of reporting an error.
IMAP_TIMEOUT = 30
SMTP_TIMEOUT = 45

SPAM_FOLDERS = ("[Gmail]/Spam", "[Google Mail]/Spam")
#: Page size used for Gmail message-list requests. The Gmail REST API has no
#: messages.batchGet method, so message bodies are fetched with messages.get.
MAX_PER_SYNC = 250
#: Work in bounded groups to limit transient task/memory growth; each id is
#: still fetched with its own documented users.messages.get request.
BATCH_GET_LIMIT = 100
#: At most this many messages.get requests run concurrently per mailbox sync.
BATCH_CONCURRENCY = 3
#: Guard against a broken/repeating nextPageToken while listing or reading history.
MAX_LIST_PAGES = 1000
#: Keep Gmail reply attachment downloads inside the email-service safety limits.
MAX_IMPORT_ATTACHMENTS = 10
MAX_IMPORT_ATTACHMENT_BYTES = 4 * 1024 * 1024
MAX_IMPORT_ATTACHMENTS_TOTAL_BYTES = 8 * 1024 * 1024
MAX_GMAIL_TEXT_PART_BYTES = 8 * 1024 * 1024
#: On the very first sync, only look back this far. Nobody wants last year's
#: newsletters imported into their CRM inbox.
FIRST_SYNC_DAYS = 7

#: The Gmail label behind each folder the mail app shows. Gmail has no "folders"
#: — it has labels — and the system ones are uppercase and reserved.
FOLDER_LABELS: dict[str, str] = {
    "INBOX": "INBOX",
    "SENT": "SENT",
    "SPAM": "SPAM",
    "DRAFTS": "DRAFT",
    "TRASH": "TRASH",
    "STARRED": "STARRED",
    "IMPORTANT": "IMPORTANT",
}
#: The IMAP folder name behind the same view, for the app-password door.
FOLDER_IMAP: dict[str, tuple[str, ...]] = {
    "INBOX": ("INBOX",),
    "SENT": ("[Gmail]/Sent Mail", "[Google Mail]/Sent Mail"),
    "SPAM": ("[Gmail]/Spam", "[Google Mail]/Spam"),
    "DRAFTS": ("[Gmail]/Drafts", "[Google Mail]/Drafts"),
    "TRASH": ("[Gmail]/Trash", "[Google Mail]/Trash"),
}


class MailboxError(Exception):
    """A mailbox operation failed, with a message an operator can act on."""


# ---------------------------------------------------------------------------
# Normalization — one shape, whichever door the reply came through
# ---------------------------------------------------------------------------


def _decode(value: str | bytes | None, encoding: str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode(encoding or "utf-8", errors="replace")
        except LookupError:
            return value.decode("utf-8", errors="replace")
    return str(value)


def _b64url_decode(data: str) -> bytes:
    padded = (data or "").replace("-", "+").replace("_", "/")
    padded += "=" * (-len(padded) % 4)
    try:
        return base64.b64decode(padded)
    except Exception:  # noqa: BLE001
        return b""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def normalize_message(
    *,
    from_address: str,
    from_name: str | None,
    to_addresses: list[str],
    subject: str | None,
    text: str,
    html: str | None,
    message_id: str | None,
    in_reply_to: str | None,
    references: str | None,
    date: datetime | None,
    attachments: list[dict] | None = None,
    headers: dict | None = None,
    folder: str = "INBOX",
    provider_message_id: str | None = None,
    provider: str = "gmail",
) -> dict:
    """Build the Brevo-shaped inbound item the single ingestion pipeline reads."""
    item: dict[str, Any] = {
        "From": {"Address": (from_address or "").strip().lower(), "Name": from_name or ""},
        "To": [{"Address": a.strip().lower()} for a in to_addresses if a],
        "Subject": (subject or "")[:500],
        "MessageId": message_id or provider_message_id or "",
        "RawTextBody": text or "",
        "SentAtDate": (date or datetime.now(timezone.utc)).isoformat(),
        "Headers": {
            k: v for k, v in {
                "In-Reply-To": in_reply_to or "",
                "References": references or "",
                "Message-ID": message_id or "",
            }.items() if v
        },
        "Attachments": attachments or [],
        "_folder": folder,
        "_provider": provider,
    }
    if in_reply_to:
        # Brevo puts threading at the top level of the item; the parser reads
        # either spelling, so both are supplied.
        item["InReplyTo"] = in_reply_to
    if references:
        item["References"] = references
    if html:
        item["RawHtmlBody"] = html
    if provider_message_id:
        item["_provider_message_id"] = provider_message_id
    if headers:
        item["Headers"].update({k: v for k, v in headers.items() if v})
    return item


def split_addresses(header: str) -> list[str]:
    """Split a To/Cc header on commas that are not inside a display name."""
    return [chunk for chunk in re.split(r",(?![^<]*>)", header or "") if chunk.strip()]


def strip_quoted_reply(text: str) -> str:
    """Drop the quoted history a reply carries, so the inbox shows the reply.

    Gmail's own inbound parser does this with machine learning; a few reliable
    markers get most of the way there and never eat real content.
    """
    if not text:
        return ""
    markers = (
        r"^\s*On .{0,120}?wrote:\s*$",
        r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$",
        r"^\s*_{5,}\s*$",
        r"^\s*From:\s*.+\s*$",
        r"^\s*>+\s?.*$",
        r"^\s*Le .{0,120}?a écrit\s*:\s*$",
    )
    lines = text.replace("\r\n", "\n").split("\n")
    kept: list[str] = []
    for line in lines:
        if any(re.match(marker, line) for marker in markers):
            break
        kept.append(line)
    body = "\n".join(kept).strip()
    return body or text.strip()


# ---------------------------------------------------------------------------
# MIME → normalized item (used by IMAP, and by anything that has raw RFC822)
# ---------------------------------------------------------------------------


def parse_mime(raw: bytes | str, *, folder: str = "INBOX",
               provider_message_id: str | None = None) -> dict:
    """Parse a raw RFC822 message into the normalized inbound shape.

    IMAP hands back bytes and the Gmail API hands back base64url-decoded bytes,
    but :func:`build_rfc822` produces ``str`` — accepting both keeps a message
    that we built ourselves round-trippable through the same parser.
    """
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "replace")
    message = email.message_from_bytes(raw, policy=email.policy.default)
    from_name, from_address = parseaddr(str(message.get("From") or ""))
    to_addresses = [
        parsed.strip().lower()
        for parsed in (
            parseaddr(chunk)[1]
            for chunk in split_addresses(str(message.get("To") or ""))
        )
        if parsed
    ]
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict] = []

    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = str(part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        content_type = part.get_content_type()
        if disposition == "attachment" or (filename and content_type != "text/plain"):
            payload = part.get_payload(decode=True) or b""
            if filename:
                attachments.append({
                    "name": str(filename)[:255],
                    "contentType": content_type or "application/octet-stream",
                    "content": base64.b64encode(payload).decode(),
                    "size": len(payload),
                })
            continue
        if content_type == "text/plain":
            text_parts.append(_decode(part.get_payload(decode=True), part.get_content_charset()))
        elif content_type == "text/html":
            html_parts.append(_decode(part.get_payload(decode=True), part.get_content_charset()))

    text = strip_quoted_reply("\n".join(p for p in text_parts if p).strip())
    html = "\n".join(p for p in html_parts if p).strip() or None
    try:
        date = parsedate_to_datetime(str(message.get("Date") or ""))
    except (TypeError, ValueError):
        date = None
    if date is not None and date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)

    # Every header, not just the threading three: the reply recogniser needs
    # List-Unsubscribe / Precedence / Auto-Submitted to tell a real answer from
    # a newsletter a known contact's address also happens to send.
    all_headers = {str(k): str(v) for k, v in message.items()}
    return normalize_message(
        from_address=from_address,
        from_name=from_name or None,
        to_addresses=to_addresses,
        subject=str(message.get("Subject") or "") or None,
        text=text,
        html=html,
        message_id=str(message.get("Message-ID") or "") or None,
        in_reply_to=str(message.get("In-Reply-To") or "") or None,
        references=str(message.get("References") or "") or None,
        date=date,
        attachments=attachments[:10],
        headers=all_headers,
        folder=folder,
        provider_message_id=provider_message_id,
    )


#: Headers that mark a message as bulk/marketing rather than a person writing
#: back. Each one is set by the sender's mailer on purpose, so their presence is
#: strong evidence — and their absence is what lets an unthreaded answer from a
#: known contact be imported instead of dropped.
BULK_MAIL_MARKERS = (
    "list-unsubscribe",
    "list-unsubscribe-post",
    "list-id",
    "list-subscribe",
    "x-mc-user",
    "x-campaignid",
    "x-brevo-campaign",
    "x-sg-eid",
    "x-mailchimp-id",
    "feedback-id",
)
#: Header *values* that mean the same thing: ``{header: allowed value prefixes}``.
#: An out-of-office (``Auto-Submitted: auto-replied``) is deliberately NOT in
#: here — that is a person's mailbox answering, and the operator wants to see it.
BULK_MAIL_VALUES = {
    "precedence": ("bulk", "list", "junk"),
    "auto-submitted": ("auto-generated",),
}


def looks_like_bulk_mail(item: dict) -> bool:
    """Is this message marketing mail rather than a person replying?

    Used for exactly one decision: when a *known contact* writes an unthreaded
    message to a connected mailbox, importing it is right (people reply from
    phones, change the subject, forward through rules) — unless the message is
    plainly a campaign blast, which is what this catches.
    """
    headers = item.get("Headers") or {}
    lowered = {str(k).lower(): str(v).strip().lower() for k, v in headers.items()}
    if any(lowered.get(marker) for marker in BULK_MAIL_MARKERS):
        return True
    return any(
        lowered.get(header, "").startswith(prefix)
        for header, prefixes in BULK_MAIL_VALUES.items()
        for prefix in prefixes
    )


def build_rfc822(
    *,
    from_address: str,
    from_name: str | None,
    to_address: str,
    to_name: str | None,
    subject: str,
    text: str,
    html: str | None,
    in_reply_to: str | None = None,
    references: str | None = None,
    message_id: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    attachments: list[dict] | None = None,
    headers: dict | None = None,
) -> tuple[str, list[str]]:
    """Render an outbound message as ``(rfc822, recipients)``.

    ``In-Reply-To``/``References`` are the whole point of sending through the
    mailbox the thread started in: the reply lands inside the existing
    conversation in the prospect's client instead of starting a new one, and
    Gmail on both sides sees one coherent thread.
    """
    message = EmailMessage()
    display = f"{from_name} <{from_address}>" if from_name else from_address
    message["From"] = display
    message["To"] = f"{to_name} <{to_address}>" if to_name else to_address
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject or "(no subject)"
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = message_id or make_msgid(domain=(from_address.split("@")[-1] or None))
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    message["X-Mailer"] = "SENDERSMS"
    for header_name, header_value in (headers or {}).items():
        # Threading and envelope headers above are authoritative; the extra
        # application headers are for list-unsubscribe and future extensions.
        if not header_value or header_name.lower() in {
            "from", "to", "cc", "bcc", "subject", "date", "message-id",
            "in-reply-to", "references",
        }:
            continue
        try:
            message[header_name] = str(header_value)
        except (KeyError, ValueError):
            logger.warning("GMAIL: ignored invalid outbound header %r", header_name)

    if html:
        message.set_content(text or re.sub(r"<[^>]+>", " ", html).strip() or " ")
        message.add_alternative(html, subtype="html")
    else:
        message.set_content(text or " ")

    for entry in (attachments or [])[:10]:
        name = str(entry.get("name") or "attachment")
        content = entry.get("content") or entry.get("content_base64")
        if not content:
            continue
        try:
            payload = base64.b64decode(content)
        except Exception:  # noqa: BLE001
            continue
        maintype, _, subtype = str(
            entry.get("content_type") or "application/octet-stream"
        ).partition("/")
        try:
            message.add_attachment(
                payload, maintype=maintype or "application", subtype=subtype or "octet-stream",
                filename=name,
            )
        except Exception as exc:  # noqa: BLE001 — one bad attachment must not lose the reply
            logger.warning("GMAIL: attachment %s skipped (%s)", name, exc)

    recipients = [to_address] + list(cc or []) + list(bcc or [])
    return message.as_string(), recipients  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# IMAP / SMTP (app password)
# ---------------------------------------------------------------------------


def _imap_login(address: str, password: str) -> imaplib.IMAP4_SSL:
    connection = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT)
    connection.login(address, password)
    return connection


def _imap_status(connection: imaplib.IMAP4_SSL, folder: str) -> dict:
    """UIDNEXT and EXISTS for one folder, or {} if it does not exist."""
    try:
        status = connection.status(f'"{folder}"', "(UIDNEXT EXISTS UNSEEN)")
    except imaplib.IMAP4.error as exc:
        logger.info("IMAP status failed for %s: %s", folder, exc)
        return {}
    if status[0] != "OK" or not status[1]:
        return {}
    raw = status[1][0].decode("utf-8", errors="replace") if isinstance(status[1][0], bytes) \
        else str(status[1][0])
    numbers = dict(re.findall(r"(UIDNEXT|EXISTS|UNSEEN)\s+(\d+)", raw))
    return {k: int(v) for k, v in numbers.items()}


def _imap_select(connection: imaplib.IMAP4_SSL, folder: str) -> bool:
    try:
        result = connection.select(f'"{folder}"', readonly=False)
    except imaplib.IMAP4.error as exc:
        logger.info("IMAP select failed for %s: %s", folder, exc)
        return False
    return result[0] == "OK"


def _imap_sync_blocking(
    address: str, password: str, folders: list[str], cursors: dict, *, import_all: bool,
    lookback_days: int,
) -> dict:
    """One blocking IMAP pass; cursors stop at the first unprocessed UID."""
    connection = _imap_login(address, password)
    items: list[dict] = []
    new_cursors: dict[str, int] = dict(cursors or {})
    cursor_updates: dict[str, int] = {}
    errors: list[str] = []
    try:
        for folder in folders:
            if not _imap_select(connection, folder):
                errors.append(f"Cannot open folder {folder}")
                continue
            status = _imap_status(connection, folder)
            uid_next = status.get("UIDNEXT")
            since_uid = int((cursors or {}).get(folder) or 0)
            if since_uid:
                criteria = f"UID {since_uid}:*"
            else:
                since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)
                         ).strftime("%d-%b-%Y")
                criteria = f'(SINCE "{since}")'
            try:
                search = connection.uid("SEARCH", None, criteria)
            except imaplib.IMAP4.error as exc:
                errors.append(f"{folder}: search failed ({exc})")
                continue
            if not search or search[0] != "OK":
                status_text = search[0] if search else "no response"
                errors.append(f"{folder}: search failed ({status_text})")
                continue

            search_data = search[1][0] if len(search) > 1 and search[1] else b""
            found_uids = sorted(
                int(uid) for uid in (search_data or b"").split() if uid.isdigit()
            )
            # UID n:* can include the last message even when there is nothing
            # new; the cursor is stored as the next UID to process.
            found_uids = [uid for uid in found_uids if uid >= since_uid]
            uids = found_uids[:MAX_PER_SYNC]
            has_more = len(found_uids) > len(uids)
            last_processed_uid: int | None = None
            folder_failed = False

            for uid in uids:
                try:
                    fetched = connection.uid("FETCH", str(uid), "(RFC822)")
                except Exception as exc:  # noqa: BLE001 — retain this UID for retry
                    errors.append(f"{folder}/{uid}: fetch failed ({type(exc).__name__})")
                    folder_failed = True
                    break
                if not fetched or fetched[0] != "OK" or len(fetched) < 2 or not fetched[1]:
                    status_text = fetched[0] if fetched else "no response"
                    errors.append(f"{folder}/{uid}: fetch failed ({status_text})")
                    folder_failed = True
                    break
                raw = None
                for chunk in fetched[1]:
                    if isinstance(chunk, tuple) and len(chunk) > 1:
                        raw = chunk[1]
                        break
                if not raw:
                    errors.append(f"{folder}/{uid}: fetch returned no message body")
                    folder_failed = True
                    break
                try:
                    item = parse_mime(
                        raw, folder=folder, provider_message_id=f"imap-{folder}-{uid}",
                    )
                except Exception as exc:  # noqa: BLE001 — don't skip an unparsed message
                    errors.append(f"{folder}/{uid}: parse failed ({exc})")
                    folder_failed = True
                    break
                item["_uid"] = uid
                items.append(item)
                last_processed_uid = uid

            if folder_failed:
                if last_processed_uid is not None:
                    next_uid = last_processed_uid + 1
                    cursor_updates[folder] = next_uid
                    new_cursors[folder] = next_uid
                continue

            if has_more:
                if last_processed_uid is not None:
                    next_uid = last_processed_uid + 1
                    cursor_updates[folder] = next_uid
                    new_cursors[folder] = next_uid
            elif uid_next:
                cursor_updates[folder] = uid_next
                new_cursors[folder] = uid_next
            elif last_processed_uid is not None:
                next_uid = last_processed_uid + 1
                cursor_updates[folder] = next_uid
                new_cursors[folder] = next_uid
    finally:
        try:
            connection.logout()
        except Exception:  # noqa: BLE001
            pass
    return {
        "success": not errors,
        "items": items,
        "cursors": new_cursors,
        "cursor_updates": cursor_updates,
        "errors": errors,
        "profile_email": address.lower(),
    }


async def imap_sync(address: str, password: str, folders: list[str], cursors: dict | None,
                    *, import_all: bool = False, first_sync: bool = False) -> dict:
    """Fetch new messages with a bounded, resumable backlog off the event loop."""
    return await asyncio.to_thread(
        _imap_sync_blocking, address, password, folders or ["INBOX"], cursors or {},
        import_all=import_all,
        lookback_days=FIRST_SYNC_DAYS if first_sync else 1,
    )


def _imap_flags(raw_flags) -> set[str]:
    """Gmail's IMAP flags, uppercased: \\Seen, \\Flagged, \\Draft …"""
    text = raw_flags.decode("utf-8", "replace") if isinstance(raw_flags, bytes) else str(raw_flags or "")
    return {token.strip().upper().lstrip("\\") for token in re.findall(r"\\\w+", text)}


def _imap_mirror_blocking(
    address: str, password: str, views: dict[str, tuple[str, ...]], *,
    cursors: dict, lookback_days: int, max_per_folder: int,
) -> dict:
    """Read every folder the mail app shows, with read/starred state.

    The reply sync only needs INBOX and Spam; the mail app needs Sent, Drafts
    and Trash too. One connection, one pass per folder, ``BODY.PEEK[]`` so
    reading here never marks anything unread in Gmail. Per-folder cursors move
    only past messages we have fetched successfully; a capped pass leaves the
    older backlog for the next run.
    """
    connection = _imap_login(address, password)
    items: list[dict] = []
    new_cursors: dict[str, int] = dict(cursors or {})
    cursor_updates: dict[str, int] = {}
    errors: list[str] = []
    max_per_folder = max(1, int(max_per_folder or 1))
    try:
        for view, names in views.items():
            folder_name = next((n for n in names if _imap_select(connection, n)), None)
            if folder_name is None:
                errors.append(f"Cannot open a folder for {view} (tried {', '.join(names)})")
                continue
            status = _imap_status(connection, folder_name)
            cursor_key = f"mirror:{view}"
            uid_next = status.get("UIDNEXT")
            since_uid = int((cursors or {}).get(cursor_key) or 0)
            if since_uid:
                criteria = f"UID {since_uid}:*"
            else:
                since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)
                         ).strftime("%d-%b-%Y")
                criteria = f'(SINCE "{since}")'
            try:
                search = connection.uid("SEARCH", None, criteria)
            except imaplib.IMAP4.error as exc:
                errors.append(f"{view}: search failed ({exc})")
                continue
            if not search or search[0] != "OK":
                status_text = search[0] if search else "no response"
                errors.append(f"{view}: search failed ({status_text})")
                continue

            found_uids = [
                int(uid) for uid in ((search[1][0] or b"") if len(search) > 1 and search[1] else b"").split()
                if uid.isdigit()
            ]
            found_uids = [uid for uid in found_uids if uid >= since_uid]
            uids = found_uids[:max_per_folder]
            has_more = len(found_uids) > len(uids)
            last_processed_uid: int | None = None
            folder_failed = False

            for uid in uids:
                try:
                    fetched = connection.uid("FETCH", str(uid), "(FLAGS BODY.PEEK[])")
                except imaplib.IMAP4.error as exc:
                    errors.append(f"{view}/{uid}: fetch failed ({exc})")
                    folder_failed = True
                    break
                if not fetched or fetched[0] != "OK" or len(fetched) < 2 or not fetched[1]:
                    status_text = fetched[0] if fetched else "no response"
                    errors.append(f"{view}/{uid}: fetch failed ({status_text})")
                    folder_failed = True
                    break
                raw, flags = None, set()
                for chunk in fetched[1]:
                    if isinstance(chunk, tuple) and len(chunk) > 1:
                        flags = _imap_flags(chunk[0])
                        raw = chunk[1]
                        break
                if not raw:
                    errors.append(f"{view}/{uid}: fetch returned no message body")
                    folder_failed = True
                    break
                try:
                    item = parse_mime(raw, folder=view,
                                      provider_message_id=f"imap-{view}-{uid}")
                except Exception as exc:  # noqa: BLE001 — don't advance past an unparsed message
                    errors.append(f"{view}/{uid}: parse failed ({exc})")
                    folder_failed = True
                    break
                item["_uid"] = uid
                item["_folder"] = view
                item["_is_read"] = "SEEN" in flags
                item["_is_starred"] = "FLAGGED" in flags
                item["_raw_text"] = item.get("RawTextBody") or ""
                item["_provider"] = "imap"
                items.append(item)
                last_processed_uid = uid

            # A failed fetch/parse can still move a cursor past earlier messages
            # that completed in this pass, but never past the failed UID.
            if folder_failed:
                if last_processed_uid is not None:
                    next_uid = last_processed_uid + 1
                    cursor_updates[cursor_key] = next_uid
                    new_cursors[cursor_key] = next_uid
                continue

            if has_more:
                # Consume the oldest part of a backlog first so a per-pass cap
                # cannot skip older mail by jumping straight to UIDNEXT.
                if last_processed_uid is not None:
                    next_uid = last_processed_uid + 1
                    cursor_updates[cursor_key] = next_uid
                    new_cursors[cursor_key] = next_uid
            elif uid_next:
                cursor_updates[cursor_key] = uid_next
                new_cursors[cursor_key] = uid_next
            elif last_processed_uid is not None:
                next_uid = last_processed_uid + 1
                cursor_updates[cursor_key] = next_uid
                new_cursors[cursor_key] = next_uid
    finally:
        try:
            connection.logout()
        except Exception:  # noqa: BLE001
            pass
    return {
        "success": not errors,
        "items": items,
        "cursors": new_cursors,
        "cursor_updates": cursor_updates,
        "errors": errors,
        "profile_email": address.lower(),
    }


async def imap_mirror_folders(
    address: str, password: str, *, cursors: dict | None = None,
    first_sync: bool = False, max_per_folder: int = MAX_PER_SYNC,
) -> dict:
    """Mirror every mail-app folder for an app-password mailbox."""
    return await asyncio.to_thread(
        _imap_mirror_blocking, address, password, FOLDER_IMAP,
        cursors=cursors or {},
        lookback_days=FIRST_SYNC_DAYS if first_sync else 3,
        max_per_folder=max_per_folder,
    )


def _imap_rescue_blocking(address: str, password: str, folder: str, uid: int) -> dict:
    """Move one message out of Spam: copy to INBOX, then remove it from Spam.

    IMAP has no "un-spam" verb — the label is Gmail's own — but copying to INBOX
    and deleting the Spam copy is exactly what the Gmail UI does when you press
    "Not spam", and it clears the Spam label as a side effect.
    """
    connection = _imap_login(address, password)
    try:
        if not _imap_select(connection, folder):
            return {"success": False, "error": f"Cannot open {folder}"}
        copied = connection.uid("COPY", str(uid), '"INBOX"')
        if copied[0] != "OK":
            # Gmail needs MOVE enabled; fall back to it when COPY is refused.
            moved = connection.uid("MOVE", str(uid), "INBOX")
            if moved[0] != "OK":
                return {"success": False, "error": f"Copy/Move refused: {copied[0]}"}
            return {"success": True, "moved": True}
        connection.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
        connection.expunge()
        return {"success": True, "moved": False}
    except imaplib.IMAP4.error as exc:
        return {"success": False, "error": str(exc)[:300]}
    finally:
        try:
            connection.logout()
        except Exception:  # noqa: BLE001
            pass


async def imap_rescue_from_spam(address: str, password: str, uid: int,
                                folder: str = "[Gmail]/Spam") -> dict:
    return await asyncio.to_thread(_imap_rescue_blocking, address, password, folder, uid)


def _imap_check_blocking(address: str, password: str) -> dict:
    try:
        connection = _imap_login(address, password)
    except imaplib.IMAP4.error as exc:
        text = str(exc)
        if "Invalid credentials" in text or "AUTHENTICATIONFAILED" in text:
            return {"success": False,
                    "error": "Gmail refused the sign-in. You need an *app password* (Google "
                             "Account → Security → 2-Step Verification → App passwords), not your "
                             "normal password."}
        return {"success": False, "error": text[:300]}
    except (TimeoutError, socket.timeout):
        return {"success": False,
                "error": f"imap.gmail.com:{IMAP_PORT} did not answer within {IMAP_TIMEOUT}s. "
                         "Outbound IMAP is usually blocked by a firewall or the hosting "
                         "provider — that is a network problem, not a wrong password."}
    except OSError as exc:
        return {"success": False, "error": f"Cannot reach imap.gmail.com: {exc}"}
    try:
        listed = connection.list()
        folders = []
        for line in listed[1] or []:
            decoded = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line)
            match = re.search(r'"([^"]+)"$', decoded)
            if match:
                folders.append(match.group(1))
        status = _imap_status(connection, "INBOX")
        return {"success": True, "folders": folders, "inbox": status,
                "has_spam_folder": any(f in folders for f in SPAM_FOLDERS)}
    finally:
        try:
            connection.logout()
        except Exception:  # noqa: BLE001
            pass


async def imap_check(address: str, password: str) -> dict:
    """Validate credentials and report what the mailbox looks like."""
    return await asyncio.to_thread(_imap_check_blocking, address, password)


def _smtp_send_blocking(address: str, password: str, raw: str, recipients: list[str]) -> dict:
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as server:
            server.login(address, password)
            server.sendmail(address, recipients, raw)
        return {"success": True, "provider_message_id": None}
    except smtplib.SMTPAuthenticationError as exc:
        return {"success": False, "status_code": 401,
                "error": f"Gmail refused the sign-in ({exc.smtp_code}). Use an app password."}
    except smtplib.SMTPRecipientsRefused as exc:
        return {"success": False, "status_code": 400, "error": str(exc.recipients)[:300]}
    except (smtplib.SMTPException, OSError) as exc:
        return {"success": False, "error": str(exc)[:300]}


async def smtp_send(address: str, password: str, raw: str, recipients: list[str]) -> dict:
    """Send through Gmail's own SMTP — the reply leaves from the real mailbox.

    This is the deliverability fix, not a convenience: a message sent by Brevo
    with ``From: you@gmail.com`` fails DMARC for gmail.com, and Gmail treats the
    whole thread as spoofed — which is why the prospect's reply then lands in
    Spam. Sent from the mailbox itself, Google signs it, DMARC aligns, and the
    reply threads normally.
    """
    return await asyncio.to_thread(_smtp_send_blocking, address, password, raw, recipients)


# ---------------------------------------------------------------------------
# Gmail REST API (OAuth)
# ---------------------------------------------------------------------------


def google_authorize_url(client_id: str, redirect_uri: str, state: str,
                         *, prompt: str = "consent", login_hint: str | None = None) -> str:
    """The Google consent URL the operator is sent to when connecting a mailbox.

    ``prompt`` carries two values on purpose. ``consent`` is what makes Google
    hand back a *refresh token* to an account that has already authorized this
    client once (without it the second mailbox of the same Google account comes
    back with no refresh token and can never be polled). ``select_account``
    forces the account chooser, so connecting a second, third or tenth Gmail
    inbox is the same click as the first instead of silently reusing whichever
    account the browser happens to be signed in to.
    """
    from urllib.parse import urlencode

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GMAIL_SCOPE,
        "access_type": "offline",   # required to get a refresh token
        "prompt": prompt,           # required for a refresh token on a re-consent
        "state": state,
        "include_granted_scopes": "true",
    }
    if login_hint and login_hint.strip():
        # Pre-fills the account the operator meant; the chooser still appears.
        params["login_hint"] = login_hint.strip()
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def google_exchange_code(client_id: str, client_secret: str, code: str,
                               redirect_uri: str) -> dict:
    """Trade an authorization code for tokens. Returns refresh_token when granted."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
    try:
        payload = response.json()
    except ValueError:
        payload = {"error": response.text[:200]}
    if response.status_code >= 400:
        return {"success": False, "status_code": response.status_code,
                "error": str(payload.get("error_description") or payload.get("error")
                             or response.text[:200])}
    if not payload.get("refresh_token"):
        return {"success": False, "status_code": response.status_code,
                "error": "Google did not return a refresh_token. This happens when the account "
                         "has already authorized this app: revoke it at "
                         "myaccount.google.com/permissions and connect again, or re-run the "
                         "consent with prompt=consent.",
                "raw": payload}
    return {"success": True, **payload}


async def google_refresh_access_token(client_id: str, client_secret: str,
                                      refresh_token: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(GOOGLE_TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
    try:
        payload = response.json()
    except ValueError:
        payload = {"error": response.text[:200]}
    if response.status_code >= 400 or not payload.get("access_token"):
        hint = ""
        if str(payload.get("error")) == "invalid_grant":
            hint = (" The refresh token was revoked or expired — reconnect the mailbox in the app.")
        return {"success": False, "status_code": response.status_code,
                "error": str(payload.get("error_description") or payload.get("error")
                             or "token refresh failed") + hint}
    return {"success": True, **payload}


async def _api(access_token: str, method: str, path: str, *,
               params: dict | None = None, json_body: Any = None,
               _client: httpx.AsyncClient | None = None) -> dict:
    """One Gmail API call, with bounded retries and a safe, readable error.

    ``_client`` is shared only inside one bounded message-fetch operation, which
    avoids opening hundreds of TLS connections during a mailbox sync. The
    bearer token is never included in a log or returned error.
    """
    url = path if path.startswith("http") else f"{GMAIL_API}{path}"
    headers = {"authorization": f"Bearer {access_token}", "accept": "application/json"}
    # GET is safe to retry. Never retry Gmail sends, label modifications, filter
    # creation, or other side-effecting requests: a timeout can happen after the
    # server has accepted the first request.
    attempts = 3 if method.upper() in {"GET", "HEAD"} else 1

    async def request(client: httpx.AsyncClient):
        return await client.request(
            method.upper(), url, params=params or None, json=json_body, headers=headers,
        )

    response = None
    for attempt in range(attempts):
        try:
            if _client is not None:
                response = await request(_client)
            else:
                async with httpx.AsyncClient(timeout=45.0) as client:
                    response = await request(client)
        except httpx.RequestError as exc:
            if attempt + 1 >= attempts:
                return {"success": False, "error": f"Gmail request failed: {type(exc).__name__}"}
            await asyncio.sleep(0.2 * (2 ** attempt))
            continue

        if response.status_code not in {429, 500, 502, 503, 504} or attempt + 1 >= attempts:
            break
        retry_after = (response.headers.get("retry-after") or "").strip()
        try:
            delay = min(max(float(retry_after), 0.0), 2.0) if retry_after else 0.2 * (2 ** attempt)
        except ValueError:
            delay = 0.2 * (2 ** attempt)
        await asyncio.sleep(delay)

    if response is None:
        return {"success": False, "error": "Gmail request failed"}
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text[:400]}
    if response.status_code >= 400:
        error = payload.get("error") if isinstance(payload, dict) else None
        message = (error or {}).get("message") if isinstance(error, dict) else str(error or "")
        return {"success": False, "status_code": response.status_code,
                "error": (message or response.text[:300])[:400], "raw": payload}
    if isinstance(payload, dict):
        payload.setdefault("success", True)
    return {"success": True, "data": payload}


async def gmail_profile(access_token: str) -> dict:
    """``users.getProfile`` — the cheapest way to prove a token works."""
    result = await _api(access_token, "GET", "/users/me/profile")
    if not result.get("success"):
        return result
    profile = result["data"]
    return {"success": True, "email": str(profile.get("emailAddress") or "").lower(),
            "messages_total": profile.get("messagesTotal"),
            "threads_total": profile.get("threadsTotal"),
            "history_id": str(profile.get("historyId") or "")}


def _decode_mime_header(value: str) -> str:
    """Decode RFC 2047 encoded words without losing malformed header text."""
    try:
        return str(email.header.make_header(email.header.decode_header(value or "")))
    except (LookupError, TypeError, ValueError):
        return value or ""


def _estimated_decoded_bytes(encoded: str | None) -> int:
    """Upper bound decoded base64 size without allocating the decoded bytes."""
    compact = str(encoded or "").rstrip("=")
    return (len(compact) * 3 + 3) // 4


def _walk_payload(
    payload: dict, *, include_file_attachments: bool = True,
) -> tuple[str, str | None, list[dict], dict, list[dict], list[str]]:
    """Flatten Gmail MIME parts while enforcing body and attachment size caps."""
    canonical_names = {
        "from": "From", "to": "To", "cc": "Cc", "bcc": "Bcc",
        "reply-to": "Reply-To", "subject": "Subject", "date": "Date",
        "message-id": "Message-ID", "in-reply-to": "In-Reply-To",
        "references": "References", "delivered-to": "Delivered-To",
        "list-unsubscribe": "List-Unsubscribe",
    }
    headers: dict[str, str] = {}
    for header in payload.get("headers") or []:
        if not isinstance(header, dict):
            continue
        original_name = str(header.get("name") or "").strip()
        if not original_name:
            continue
        name = canonical_names.get(original_name.casefold(), original_name)
        value = _decode_mime_header(str(header.get("value") or ""))
        headers[name] = f"{headers[name]}, {value}" if headers.get(name) else value

    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict] = []
    body_attachment_refs: list[dict] = []
    body_errors: list[str] = []
    inline_attachment_bytes = 0

    def part_size(body: dict, data: str | None) -> int:
        try:
            declared = int(body.get("size") or 0)
        except (TypeError, ValueError):
            declared = 0
        return max(declared, _estimated_decoded_bytes(data) if data else 0)

    def visit(part: dict) -> None:
        nonlocal inline_attachment_bytes
        if not isinstance(part, dict):
            return
        mime = str(part.get("mimeType") or "")
        filename = part.get("filename")
        body = part.get("body") or {}
        data = str(body.get("data") or "")
        size = part_size(body, data)
        if filename:
            # Metadata beyond the import count may be large too; keep only the
            # first supported records and never decode content that will be dropped.
            if len(attachments) < MAX_IMPORT_ATTACHMENTS:
                record = {
                    "name": str(filename)[:255],
                    "contentType": mime or "application/octet-stream",
                    "size": size or None,
                }
                if include_file_attachments and data:
                    estimated = _estimated_decoded_bytes(data)
                    if (
                        size <= MAX_IMPORT_ATTACHMENT_BYTES
                        and estimated <= MAX_IMPORT_ATTACHMENT_BYTES
                        and inline_attachment_bytes + estimated <= MAX_IMPORT_ATTACHMENTS_TOTAL_BYTES
                    ):
                        raw_attachment = _b64url_decode(data)
                        if (
                            len(raw_attachment) <= MAX_IMPORT_ATTACHMENT_BYTES
                            and inline_attachment_bytes + len(raw_attachment)
                            <= MAX_IMPORT_ATTACHMENTS_TOTAL_BYTES
                        ):
                            inline_attachment_bytes += len(raw_attachment)
                            record["content"] = base64.b64encode(raw_attachment).decode()
                elif include_file_attachments and body.get("attachmentId"):
                    # The caller decides whether to fetch this separately; oversized
                    # files are retained as metadata only and never downloaded.
                    if (
                        size <= MAX_IMPORT_ATTACHMENT_BYTES
                        and inline_attachment_bytes + size <= MAX_IMPORT_ATTACHMENTS_TOTAL_BYTES
                    ):
                        record["_attachment_id"] = str(body["attachmentId"])
                attachments.append(record)
        elif mime in ("text/plain", "text/html"):
            if data:
                if size > MAX_GMAIL_TEXT_PART_BYTES:
                    body_errors.append("Gmail text body part exceeds the import size limit")
                else:
                    decoded = _b64url_decode(data).decode("utf-8", errors="replace")
                    (text_parts if mime == "text/plain" else html_parts).append(decoded)
            elif body.get("attachmentId"):
                body_attachment_refs.append({"id": str(body["attachmentId"]), "mime": mime,
                                            "size": size or None})
        for child in part.get("parts") or []:
            visit(child)

    visit(payload)
    return ("\n".join(t for t in text_parts if t).strip(),
            "\n".join(h for h in html_parts if h).strip() or None,
            attachments[:MAX_IMPORT_ATTACHMENTS], headers,
            body_attachment_refs[:4], body_errors[:4])


def folder_from_labels(labels: list[str]) -> str:
    """Which mail-app folder a set of Gmail labels means.

    Order matters: a message can carry INBOX *and* IMPORTANT and UNREAD at once,
    and the folders the operator recognises (Inbox, Sent, Spam, Drafts, Trash)
    win over those decorations.
    """
    have = {str(label).upper() for label in labels}
    if "TRASH" in have:
        return "TRASH"
    if "SPAM" in have:
        return "SPAM"
    if "DRAFT" in have:
        return "DRAFTS"
    if "SENT" in have:
        return "SENT"
    if "INBOX" in have:
        return "INBOX"
    return "OTHER"


def _normalize_api_message(
    data: dict, *, strip_quotes: bool = True, include_file_attachments: bool = True,
) -> dict:
    """One Gmail API message payload -> the normalized inbound item."""
    text, html, attachments, headers, body_attachment_refs, body_errors = _walk_payload(
        data.get("payload") or {}, include_file_attachments=include_file_attachments,
    )
    labels = [str(x) for x in (data.get("labelIds") or [])]
    internal = int(data.get("internalDate") or 0) / 1000
    date = datetime.fromtimestamp(internal, tz=timezone.utc) if internal else None
    from_parsed = getaddresses([headers.get("From") or ""])
    from_name, from_address = from_parsed[0] if from_parsed else ("", "")
    to_addresses = _addresses_from(headers.get("To"))
    cc_addresses = _addresses_from(headers.get("Cc"))
    item = normalize_message(
        from_address=from_address,
        from_name=from_name or None,
        to_addresses=to_addresses,
        subject=headers.get("Subject") or None,
        # A reply preview must not carry the quoted history; a full mailbox
        # mirror must keep it, or the reading pane shows half a message.
        text=strip_quoted_reply(text) if strip_quotes else text,
        html=html,
        message_id=headers.get("Message-ID") or None,
        in_reply_to=headers.get("In-Reply-To") or None,
        references=headers.get("References") or None,
        date=date,
        attachments=attachments,
        headers=headers,
        folder=folder_from_labels(labels),
        provider_message_id=str(data.get("id") or ""),
        provider="gmail_api",
    )
    item["_labels"] = labels
    item["_thread_id"] = str(data.get("threadId") or "")
    item["_snippet"] = str(data.get("snippet") or "")
    item["_cc"] = cc_addresses
    item["_raw_text"] = text
    item["_history_id"] = str(data.get("historyId") or "")
    if body_attachment_refs:
        item["_body_attachment_refs"] = body_attachment_refs
    if body_errors:
        item["_body_errors"] = body_errors
    return item


def _addresses_from(header_value: str | None) -> list[str]:
    """Every To/Cc address; ``getaddresses`` handles groups and quoted commas."""
    addresses: list[str] = []
    for _name, address in getaddresses([header_value or ""]):
        clean = address.strip().lower()
        if "@" in clean and clean not in addresses:
            addresses.append(clean)
    return addresses


async def _hydrate_gmail_attachments(
    access_token: str, message_id: str, item: dict, *,
    strip_quotes: bool, include_file_attachments: bool,
    _client: httpx.AsyncClient | None,
) -> list[str]:
    """Fetch Gmail attachment IDs only when safe to import them inline.

    In Gmail's full format, non-text attachments (and occasionally very large
    text parts) contain only an ``attachmentId``. A message is not considered
    complete for reply-import until those eligible parts have been fetched.
    """
    errors: list[str] = [str(error)[:180] for error in item.pop("_body_errors", [])]
    message_path = quote(str(message_id), safe="")
    body_text: list[str] = []
    body_html: list[str] = []
    refs = item.pop("_body_attachment_refs", [])

    async def attachment_bytes(
        attachment_id: str, *, max_bytes: int,
    ) -> tuple[bytes | None, str | None]:
        safe_attachment = quote(str(attachment_id), safe="")
        result = await _api(
            access_token, "GET",
            f"/users/me/messages/{message_path}/attachments/{safe_attachment}",
            _client=_client,
        )
        if not result.get("success"):
            return None, str(result.get("error") or "Gmail attachment fetch failed")[:180]
        encoded = str((result.get("data") or {}).get("data") or "")
        if _estimated_decoded_bytes(encoded) > max_bytes:
            return None, "Gmail attachment exceeds the import size limit"
        return _b64url_decode(encoded), None

    for ref in refs:
        try:
            declared_size = int(ref.get("size") or 0)
        except (TypeError, ValueError):
            declared_size = 0
        if declared_size > MAX_GMAIL_TEXT_PART_BYTES:
            errors.append("Gmail text body part exceeds the import size limit")
            continue
        raw, error = await attachment_bytes(
            str(ref.get("id") or ""), max_bytes=MAX_GMAIL_TEXT_PART_BYTES,
        )
        if error:
            errors.append(error)
            continue
        if len(raw or b"") > MAX_GMAIL_TEXT_PART_BYTES:
            errors.append("Gmail text body part exceeds the import size limit")
            continue
        decoded = (raw or b"").decode("utf-8", errors="replace")
        if ref.get("mime") == "text/plain":
            body_text.append(decoded)
        elif ref.get("mime") == "text/html":
            body_html.append(decoded)

    if body_text or body_html:
        raw_text = "\n".join(
            part for part in [str(item.get("_raw_text") or ""), *body_text] if part
        ).strip()
        item["_raw_text"] = raw_text
        item["RawTextBody"] = strip_quoted_reply(raw_text) if strip_quotes else raw_text
        html = "\n".join(
            part for part in [str(item.get("RawHtmlBody") or ""), *body_html] if part
        ).strip()
        if html:
            item["RawHtmlBody"] = html

    if include_file_attachments:
        imported: list[dict] = []
        total_size = 0
        for record in item.get("Attachments") or []:
            if not isinstance(record, dict):
                continue
            name = str(record.get("name") or "").strip()
            if not name or len(imported) >= MAX_IMPORT_ATTACHMENTS:
                continue
            try:
                declared_size = int(record.get("size") or 0)
            except (TypeError, ValueError):
                declared_size = 0
            remaining = MAX_IMPORT_ATTACHMENTS_TOTAL_BYTES - total_size
            max_bytes = min(MAX_IMPORT_ATTACHMENT_BYTES, remaining)
            if max_bytes <= 0 or declared_size > max_bytes:
                continue
            content = record.get("content")
            if content:
                encoded_content = str(content)
                if _estimated_decoded_bytes(encoded_content) > max_bytes:
                    continue
                try:
                    raw = base64.b64decode(encoded_content, validate=False)
                except Exception:  # noqa: BLE001 — malformed attachment is ignored
                    continue
            elif record.get("_attachment_id"):
                raw, error = await attachment_bytes(
                    str(record["_attachment_id"]), max_bytes=max_bytes,
                )
                if error:
                    if "exceeds the import size limit" not in error:
                        errors.append(error)
                    continue
            else:
                continue
            raw = raw or b""
            if len(raw) > MAX_IMPORT_ATTACHMENT_BYTES:
                continue
            if total_size + len(raw) > MAX_IMPORT_ATTACHMENTS_TOTAL_BYTES:
                continue
            total_size += len(raw)
            imported.append({
                "name": name[:255],
                "contentType": str(record.get("contentType") or "application/octet-stream")[:120],
                "size": len(raw),
                "content": base64.b64encode(raw).decode("ascii"),
            })
        item["Attachments"] = imported
    else:
        # Mailbox mirroring/full-body reads need message text, not file contents.
        # Retain names/sizes for the mirror's count while stripping inline data.
        item["Attachments"] = [
            {key: value for key, value in record.items()
             if key not in {"content", "_attachment_id"}}
            for record in (item.get("Attachments") or []) if isinstance(record, dict)
        ]
    return errors


async def gmail_get_message(access_token: str, message_id: str, *,
                            strip_quotes: bool = True,
                            include_attachments: bool = False,
                            _client: httpx.AsyncClient | None = None) -> dict:
    """Fetch one message through Gmail's documented ``messages.get`` method."""
    safe_id = quote(str(message_id), safe="")
    result = await _api(
        access_token, "GET", f"/users/me/messages/{safe_id}",
        params={"format": "full"}, _client=_client,
    )
    if not result.get("success"):
        return result
    data = result["data"]
    item = _normalize_api_message(
        data, strip_quotes=strip_quotes, include_file_attachments=include_attachments,
    )
    attachment_errors = await _hydrate_gmail_attachments(
        access_token, message_id, item, strip_quotes=strip_quotes,
        include_file_attachments=include_attachments, _client=_client,
    )
    if attachment_errors:
        return {"success": False, "error": "; ".join(attachment_errors[:3])[:400]}
    return {"success": True, "item": item, "labels": item["_labels"],
            "history_id": item.get("_history_id") or ""}


async def gmail_batch_get(access_token: str, message_ids: list[str], *,
                          strip_quotes: bool = True,
                          include_attachments: bool = True) -> dict:
    """Fetch many messages with bounded, concurrent ``users.messages.get`` calls.

    Gmail has no ``users.messages.batchGet`` REST endpoint. This compatibility
    helper keeps existing call sites stable while issuing one documented GET
    per ID, sharing an HTTP connection pool and limiting in-flight requests.
    A stale ID that Gmail has deleted is skipped; other failures are returned
    and must not advance the caller's sync cursor.
    """
    ids = [i for i in dict.fromkeys(str(i).strip() for i in message_ids) if i]
    if not ids:
        return {"success": True, "items": [], "errors": [], "missing_ids": [], "history_id": ""}

    semaphore = asyncio.Semaphore(BATCH_CONCURRENCY)
    items: list[dict] = []
    errors: list[str] = []
    missing_ids: list[str] = []
    history_id = ""
    limits = httpx.Limits(max_connections=BATCH_CONCURRENCY,
                          max_keepalive_connections=BATCH_CONCURRENCY)

    async with httpx.AsyncClient(timeout=45.0, limits=limits) as client:
        async def fetch(message_id: str):
            async with semaphore:
                return await gmail_get_message(
                    access_token, message_id, strip_quotes=strip_quotes,
                    include_attachments=include_attachments, _client=client,
                )

        for start in range(0, len(ids), BATCH_GET_LIMIT):
            group = ids[start:start + BATCH_GET_LIMIT]
            results = await asyncio.gather(*(fetch(message_id) for message_id in group),
                                           return_exceptions=True)
            for message_id, result in zip(group, results):
                if isinstance(result, Exception):
                    errors.append(f"{message_id}: {type(result).__name__}")
                    continue
                if not result.get("success"):
                    if int(result.get("status_code") or 0) == 404:
                        missing_ids.append(message_id)
                    else:
                        errors.append(f"{message_id}: {str(result.get('error') or 'Gmail fetch failed')[:160]}")
                    continue
                item = result.get("item")
                if not isinstance(item, dict):
                    errors.append(f"{message_id}: Gmail returned an empty message")
                    continue
                items.append(item)
                history_id = str(result.get("history_id") or history_id)

    return {"success": not errors, "items": items, "errors": errors[:10],
            "missing_ids": missing_ids, "history_id": history_id}


async def gmail_list_ids(access_token: str, *, label_ids: list[str] | None = None,
                         query: str = "", max_results: int = MAX_PER_SYNC,
                         page_token: str | None = None) -> dict:
    """Just the message ids for a label/query — no bodies.

    Cheap enough to run every poll: a mailbox with nothing new costs one small
    request, and the expensive work only happens for ids not already mirrored.
    """
    params: dict[str, Any] = {"maxResults": min(int(max_results), 500), "fields": "messages/id,nextPageToken"}
    if query:
        params["q"] = query
    if label_ids:
        params["labelIds"] = label_ids
    if page_token:
        params["pageToken"] = page_token
    return await _api(access_token, "GET", "/users/me/messages", params=params)


async def gmail_list_all_ids(access_token: str, *, label_ids: list[str] | None = None,
                             query: str = "", page_size: int = 500) -> dict:
    """Follow every ``nextPageToken`` and return a deduplicated ID list.

    Gmail's ``maxResults`` is a per-page size (up to 500), not a total-result
    limit. A repeated/invalid page token is surfaced as an error so callers keep
    their old watermark and can retry safely.
    """
    ids: list[str] = []
    seen_ids: set[str] = set()
    seen_tokens: set[str] = set()
    page_token: str | None = None

    for _page in range(MAX_LIST_PAGES):
        result = await gmail_list_ids(
            access_token, label_ids=label_ids, query=query,
            max_results=min(max(1, int(page_size)), 500), page_token=page_token,
        )
        if not result.get("success"):
            return {"success": False, "status_code": result.get("status_code"),
                    "error": result.get("error") or "Gmail message list failed",
                    "data": {"messages": ids}}
        data = result.get("data") or {}
        for row in data.get("messages") or []:
            if not isinstance(row, dict):
                continue
            message_id = str(row.get("id") or "").strip()
            if message_id and message_id not in seen_ids:
                seen_ids.add(message_id)
                ids.append(message_id)
        next_token = str(data.get("nextPageToken") or "").strip()
        if not next_token:
            return {"success": True, "data": {"messages": [{"id": mid} for mid in ids]}}
        if next_token in seen_tokens or next_token == page_token:
            return {"success": False, "error": "Gmail message list repeated a page token",
                    "data": {"messages": [{"id": mid} for mid in ids]}}
        seen_tokens.add(next_token)
        page_token = next_token

    return {"success": False, "error": "Gmail message list exceeded the page safety limit",
            "data": {"messages": [{"id": mid} for mid in ids]}}


async def gmail_get_thread(access_token: str, thread_id: str) -> dict:
    """One whole thread — what the reading pane groups messages by."""
    result = await _api(access_token, "GET", f"/users/me/threads/{thread_id}",
                        params={"format": "full"})
    if not result.get("success"):
        return result
    data = result["data"]
    return {
        "success": True,
        "thread_id": str(data.get("id") or thread_id),
        "history_id": str(data.get("historyId") or ""),
        "items": [_normalize_api_message(m, strip_quotes=False, include_file_attachments=False)
                  for m in (data.get("messages") or []) if isinstance(m, dict)],
    }


async def gmail_labels_list(access_token: str) -> dict:
    return await _api(access_token, "GET", "/users/me/labels", params={"fields": "labels/id,labels/name,labels/type"})


async def gmail_trash(access_token: str, message_id: str) -> dict:
    return await _api(access_token, "POST", f"/users/me/messages/{message_id}/trash")


async def gmail_untrash(access_token: str, message_id: str) -> dict:
    return await _api(access_token, "POST", f"/users/me/messages/{message_id}/untrash")


async def gmail_history_changes(access_token: str, start_history_id: str) -> dict:
    """All message additions, deletions and label changes since a watermark."""
    return await gmail_history(
        access_token, start_history_id,
        history_types=["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"],
    )


async def gmail_list(access_token: str, *, query: str = "", label_ids: list[str] | None = None,
                     max_results: int = MAX_PER_SYNC, after: str | None = None) -> dict:
    """Compatibility wrapper for messages.list; ``after`` is a Gmail search date.

    ``startHistoryId`` belongs to ``users.history.list``, not ``messages.list``.
    """
    params: dict[str, Any] = {"maxResults": min(int(max_results), 500)}
    query_parts = [query.strip()] if query.strip() else []
    if after:
        date = str(after).strip()[:10].replace("-", "/")
        query_parts.append(f"after:{date}")
    if query_parts:
        params["q"] = " ".join(query_parts)
    if label_ids:
        params["labelIds"] = label_ids
    return await _api(access_token, "GET", "/users/me/messages", params=params)


async def gmail_modify(access_token: str, message_id: str, *, add: list[str] | None = None,
                       remove: list[str] | None = None) -> dict:
    """Change a message's labels — this is how a reply is rescued from Spam."""
    body: dict[str, Any] = {}
    if add:
        body["addLabelIds"] = add
    if remove:
        body["removeLabelIds"] = remove
    if not body:
        return {"success": False, "error": "Nothing to change"}
    return await _api(access_token, "POST", f"/users/me/messages/{message_id}/modify",
                      json_body=body)


async def gmail_ensure_label(access_token: str, name: str,
                             color: str | None = None) -> str | None:
    """Find or create a label, returning its id (or None on failure)."""
    listed = await _api(access_token, "GET", "/users/me/labels")
    if listed.get("success"):
        for label in listed["data"].get("labels") or []:
            if str(label.get("name") or "").lower() == name.lower():
                return str(label.get("id") or "") or None
    created = await _api(access_token, "POST", "/users/me/labels", json_body={
        "name": name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
        **({"color": {"backgroundColor": color}} if color else {}),
    })
    if created.get("success"):
        return str(created["data"].get("id") or "") or None
    logger.warning("GMAIL: could not create label %s (%s)", name, created.get("error"))
    return None


async def gmail_send(access_token: str, raw_rfc822: str, *, thread_id: str | None = None) -> dict:
    """Send through the mailbox itself (``users.messages.send``)."""
    body: dict[str, Any] = {"raw": _b64url_encode(raw_rfc822.encode("utf-8"))}
    if thread_id:
        body["threadId"] = thread_id
    result = await _api(access_token, "POST", "/users/me/messages/send", json_body=body)
    if not result.get("success"):
        return result
    data = result["data"]
    return {"success": True, "provider_message_id": str(data.get("id") or ""),
            "thread_id": str(data.get("threadId") or ""), "raw": data}


async def gmail_list_filters(access_token: str) -> dict:
    return await _api(access_token, "GET", "/users/me/settings/filters")


async def gmail_create_filter(access_token: str, criteria: dict, action: dict) -> dict:
    """Create a Gmail filter.

    ``{"removeLabelIds": ["SPAM"]}`` as the action is Gmail's "Never send it to
    Spam" — the documented way to stop a known-good sender being filtered, and
    the preventive half of the reply-rescue work (the reactive half is moving a
    message that already landed there back out).
    """
    return await _api(access_token, "POST", "/users/me/settings/filters",
                      json_body={"criteria": criteria, "action": action})


async def gmail_delete_filter(access_token: str, filter_id: str) -> dict:
    return await _api(access_token, "DELETE", f"/users/me/settings/filters/{filter_id}")


async def gmail_watch(access_token: str, topic_name: str, *, label_ids: list[str] | None = None,
                      history_id: str | None = None) -> dict:
    """Register for push notifications (Cloud Pub/Sub) instead of polling."""
    body: dict[str, Any] = {
        "topicName": topic_name,
        "labelIds": label_ids or ["INBOX", "SPAM"],
        "labelFilterBehavior": "include",
    }
    if history_id:
        body["historyId"] = history_id
    return await _api(access_token, "POST", "/users/me/watch", json_body=body)


async def gmail_history(access_token: str, start_history_id: str, *,
                        history_types: list[str] | None = None) -> dict:
    """Page through Gmail history and return the complete incremental change set."""
    allowed = {"messageAdded", "messageDeleted", "labelAdded", "labelRemoved"}
    requested = [kind for kind in (history_types or ["messageAdded"]) if kind in allowed]
    params: dict[str, Any] = {
        "startHistoryId": start_history_id,
        "historyTypes": requested or ["messageAdded"],
        "maxResults": 500,
        "fields": (
            "history(id,messagesAdded(message(id,threadId,labelIds)),"
            "messagesDeleted(message(id,threadId,labelIds)),"
            "labelsAdded(message(id,threadId,labelIds),labelIds),"
            "labelsRemoved(message(id,threadId,labelIds),labelIds)),"
            "nextPageToken,historyId"
        ),
    }
    history: list[dict] = []
    seen_tokens: set[str] = set()
    page_token: str | None = None
    current_history_id = str(start_history_id or "")

    for _page in range(MAX_LIST_PAGES):
        if page_token:
            params["pageToken"] = page_token
        result = await _api(access_token, "GET", "/users/me/history", params=params)
        if not result.get("success"):
            return {"success": False, "status_code": result.get("status_code"),
                    "error": result.get("error") or "Gmail history request failed",
                    "data": {"history": history, "historyId": current_history_id}}
        data = result.get("data") or {}
        history.extend(item for item in (data.get("history") or []) if isinstance(item, dict))
        current_history_id = str(data.get("historyId") or current_history_id)
        next_token = str(data.get("nextPageToken") or "").strip()
        if not next_token:
            return {"success": True,
                    "data": {"history": history, "historyId": current_history_id}}
        if next_token in seen_tokens or next_token == page_token:
            return {"success": False, "error": "Gmail history repeated a page token",
                    "data": {"history": history, "historyId": current_history_id}}
        seen_tokens.add(next_token)
        page_token = next_token

    return {"success": False, "error": "Gmail history exceeded the page safety limit",
            "data": {"history": history, "historyId": current_history_id}}
