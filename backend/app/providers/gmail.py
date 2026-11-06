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
import email.policy  # `email.policy.default` is not pulled in by `import email`
import imaplib
import json
import logging
import re
import smtplib
import socket
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr, parsedate_to_datetime
from typing import Any

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
#: Cap per folder per sync: enough to catch up, small enough that a mailbox with
#: ten thousand unread messages cannot stall a poll cycle. Raised from 40 —
#: ``batchGet`` returns 100 messages per request, so 250 is three round trips
#: instead of two hundred and fifty sequential ones.
MAX_PER_SYNC = 250
#: ``users.messages.batchGet`` accepts at most 100 ids per call.
BATCH_GET_LIMIT = 100
#: How many batchGet calls may be in flight at once. Gmail's quota is generous
#: enough for this, and it is what turns a 40-second sync into a 4-second one.
BATCH_CONCURRENCY = 3
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
    """One blocking IMAP pass. Runs in a worker thread."""
    connection = _imap_login(address, password)
    items: list[dict] = []
    new_cursors: dict[str, int] = {}
    errors: list[str] = []
    try:
        for folder in folders:
            if not _imap_select(connection, folder):
                errors.append(f"Cannot open folder {folder}")
                continue
            status = _imap_status(connection, folder)
            uid_next = status.get("UIDNEXT")
            if uid_next:
                new_cursors[folder] = uid_next
            since_uid = int(cursors.get(folder) or 0)
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
            if search[0] != "OK":
                continue
            uids = [int(u) for u in (search[1][0] or b"").split() if u.isdigit()]
            # UID n:* always returns at least the last message, even when there
            # is nothing new; drop anything we have already seen.
            uids = [u for u in uids if u >= since_uid][-MAX_PER_SYNC:]
            for uid in uids:
                fetched = connection.uid("FETCH", str(uid), "(RFC822)")
                if fetched[0] != "OK" or not fetched[1]:
                    continue
                raw = None
                for chunk in fetched[1]:
                    if isinstance(chunk, tuple) and len(chunk) > 1:
                        raw = chunk[1]
                        break
                if not raw:
                    continue
                try:
                    item = parse_mime(raw, folder=folder, provider_message_id=f"imap-{folder}-{uid}")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{folder}/{uid}: parse failed ({exc})")
                    continue
                item["_uid"] = uid
                items.append(item)
    finally:
        try:
            connection.logout()
        except Exception:  # noqa: BLE001
            pass
    return {"items": items, "cursors": new_cursors, "errors": errors,
            "profile_email": address.lower()}


async def imap_sync(address: str, password: str, folders: list[str], cursors: dict | None,
                    *, import_all: bool = False, first_sync: bool = False) -> dict:
    """Fetch new messages from each folder, newest-first, off the event loop."""
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
    reading here never marks anything unread in Gmail.
    """
    connection = _imap_login(address, password)
    items: list[dict] = []
    new_cursors: dict[str, int] = dict(cursors or {})
    errors: list[str] = []
    try:
        for view, names in views.items():
            folder_name = next((n for n in names if _imap_select(connection, n)), None)
            if folder_name is None:
                errors.append(f"Cannot open a folder for {view} (tried {', '.join(names)})")
                continue
            status = _imap_status(connection, folder_name)
            cursor_key = f"mirror:{view}"
            if status.get("UIDNEXT"):
                new_cursors[cursor_key] = status["UIDNEXT"]
            since_uid = int(cursors.get(cursor_key) or 0)
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
            if search[0] != "OK":
                continue
            uids = [int(u) for u in (search[1][0] or b"").split() if u.isdigit()]
            uids = [u for u in uids if u >= since_uid][-max_per_folder:]
            for uid in uids:
                fetched = connection.uid("FETCH", str(uid), "(FLAGS BODY.PEEK[])")
                if fetched[0] != "OK" or not fetched[1]:
                    continue
                raw, flags = None, set()
                for chunk in fetched[1]:
                    if isinstance(chunk, tuple) and len(chunk) > 1:
                        flags = _imap_flags(chunk[0])
                        raw = chunk[1]
                        break
                if not raw:
                    continue
                try:
                    item = parse_mime(raw, folder=view,
                                      provider_message_id=f"imap-{view}-{uid}")
                except Exception as exc:  # noqa: BLE001 — one bad message must not end the pass
                    errors.append(f"{view}/{uid}: parse failed ({exc})")
                    continue
                item["_uid"] = uid
                item["_folder"] = view
                item["_is_read"] = "SEEN" in flags
                item["_is_starred"] = "FLAGGED" in flags
                item["_raw_text"] = item.get("RawTextBody") or ""
                item["_provider"] = "imap"
                items.append(item)
    finally:
        try:
            connection.logout()
        except Exception:  # noqa: BLE001
            pass
    return {"items": items, "cursors": new_cursors, "errors": errors,
            "profile_email": address.lower()}


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
               params: dict | None = None, json_body: Any = None) -> dict:
    """One Gmail API call, with the error body turned into one readable line."""
    url = path if path.startswith("http") else f"{GMAIL_API}{path}"
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.request(
            method.upper(), url, params=params or None, json=json_body,
            headers={"authorization": f"Bearer {access_token}", "accept": "application/json"},
        )
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


def _walk_payload(payload: dict) -> tuple[str, str | None, list[dict], dict]:
    """Flatten a Gmail API message payload into (text, html, attachments, headers)."""
    headers = {str(h.get("name") or ""): str(h.get("value") or "")
               for h in (payload.get("headers") or []) if isinstance(h, dict)}
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict] = []

    def visit(part: dict) -> None:
        if not isinstance(part, dict):
            return
        mime = str(part.get("mimeType") or "")
        filename = part.get("filename")
        body = part.get("body") or {}
        data = body.get("data")
        if filename:
            record = {
                "name": str(filename)[:255],
                "contentType": mime or "application/octet-stream",
                "size": int(body.get("size") or 0) or None,
            }
            if data:
                record["content"] = base64.b64encode(_b64url_decode(data)).decode()
            elif body.get("attachmentId"):
                # A large attachment is fetched separately; record the id so the
                # caller can decide whether the download is worth it.
                record["_attachment_id"] = str(body["attachmentId"])
            attachments.append(record)
        elif mime == "text/plain" and data:
            text_parts.append(_b64url_decode(data).decode("utf-8", errors="replace"))
        elif mime == "text/html" and data:
            html_parts.append(_b64url_decode(data).decode("utf-8", errors="replace"))
        for child in part.get("parts") or []:
            visit(child)

    visit(payload)
    return ("\n".join(t for t in text_parts if t).strip(),
            "\n".join(h for h in html_parts if h).strip() or None,
            attachments[:10], headers)


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


def _normalize_api_message(data: dict, *, strip_quotes: bool = True) -> dict:
    """One Gmail API message payload -> the normalized inbound item."""
    text, html, attachments, headers = _walk_payload(data.get("payload") or {})
    labels = [str(x) for x in (data.get("labelIds") or [])]
    internal = int(data.get("internalDate") or 0) / 1000
    date = datetime.fromtimestamp(internal, tz=timezone.utc) if internal else None
    from_address, from_name = parseaddr(headers.get("From") or "")
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
    return item


def _addresses_from(header_value: str | None) -> list[str]:
    """Every address in a To/Cc header, lowercased, display names removed."""
    return [
        parseaddr(chunk)[1].strip().lower()
        for chunk in re.split(r",(?![^<]*>)", header_value or "") if "@" in chunk
    ]


async def gmail_get_message(access_token: str, message_id: str, *,
                            strip_quotes: bool = True) -> dict:
    """Fetch one message and normalize it."""
    result = await _api(access_token, "GET", f"/users/me/messages/{message_id}",
                        params={"format": "full"})
    if not result.get("success"):
        return result
    data = result["data"]
    item = _normalize_api_message(data, strip_quotes=strip_quotes)
    return {"success": True, "item": item, "labels": item["_labels"],
            "history_id": item.get("_history_id") or ""}


async def gmail_batch_get(access_token: str, message_ids: list[str], *,
                          strip_quotes: bool = True) -> dict:
    """Many messages in one request — the fast path for a mailbox sync.

    ``users.messages.batchGet`` returns up to 100 full messages per call, so a
    250-message sync is three requests instead of two hundred and fifty
    sequential ones. That single change is most of the difference between a
    reply taking a minute to appear and taking seconds.

    Chunks are fetched concurrently but bounded (``BATCH_CONCURRENCY``): Gmail's
    per-user quota is generous, and hammering it with 30 parallel requests is
    how a sync ends up rate-limited and slower than before.

    Ids Gmail has already deleted are simply absent from the response, so a
    message trashed mid-sync is skipped rather than failing the whole batch.
    """
    ids = [i for i in dict.fromkeys(str(i).strip() for i in message_ids) if i]
    if not ids:
        return {"success": True, "items": [], "history_id": ""}

    chunks = [ids[i:i + BATCH_GET_LIMIT] for i in range(0, len(ids), BATCH_GET_LIMIT)]
    semaphore = asyncio.Semaphore(BATCH_CONCURRENCY)
    items: list[dict] = []
    errors: list[str] = []
    history_id = ""

    async def fetch(chunk: list[str]) -> dict:
        async with semaphore:
            return await _api(
                access_token, "POST", "/users/me/messages/batchGet",
                json_body={"ids": chunk, "format": "full"},
            )

    for result in await asyncio.gather(*(fetch(chunk) for chunk in chunks),
                                       return_exceptions=True):
        if isinstance(result, Exception):
            errors.append(str(result)[:200])
            continue
        if not result.get("success"):
            errors.append(str(result.get("error"))[:200])
            continue
        for data in result["data"].get("messages") or []:
            if not isinstance(data, dict):
                continue
            items.append(_normalize_api_message(data, strip_quotes=strip_quotes))
            history_id = history_id or str(data.get("historyId") or "")

    return {"success": not errors or bool(items), "items": items,
            "errors": errors[:5], "history_id": history_id}


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
        "items": [_normalize_api_message(m, strip_quotes=False)
                  for m in (data.get("messages") or []) if isinstance(m, dict)],
    }


async def gmail_labels_list(access_token: str) -> dict:
    return await _api(access_token, "GET", "/users/me/labels", params={"fields": "labels/id,labels/name,labels/type"})


async def gmail_trash(access_token: str, message_id: str) -> dict:
    return await _api(access_token, "POST", f"/users/me/messages/{message_id}/trash")


async def gmail_untrash(access_token: str, message_id: str) -> dict:
    return await _api(access_token, "POST", f"/users/me/messages/{message_id}/untrash")


async def gmail_history_changes(access_token: str, start_history_id: str) -> dict:
    """Every message id added or labelled since ``startHistoryId``.

    This is the incremental path the poller should use: one request that answers
    "what is new in *any* folder?" instead of re-listing five folders and
    re-downloading everything in them.
    """
    params = {
        "startHistoryId": start_history_id,
        "historyTypes": ["messageAdded", "labelAdded", "labelRemoved"],
        "fields": "history/messageAdded/message/id,history/labelAdded/message/id,"
                  "history/labelRemoved/message/id,nextPageToken,historyId",
    }
    return await _api(access_token, "GET", "/users/me/history", params=params)


async def gmail_list(access_token: str, *, query: str = "", label_ids: list[str] | None = None,
                     max_results: int = MAX_PER_SYNC, after: str | None = None) -> dict:
    params: dict[str, Any] = {"maxResults": min(int(max_results), 500)}
    if query:
        params["q"] = query
    if label_ids:
        params["labelIds"] = label_ids
    if after:
        params["startHistoryId"] = after
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
    """Changes since a historyId — the incremental sync path for the API door."""
    params: dict[str, Any] = {"startHistoryId": start_history_id,
                              "historyTypes": history_types or ["messageAdded"]}
    return await _api(access_token, "GET", "/users/me/history", params=params)
