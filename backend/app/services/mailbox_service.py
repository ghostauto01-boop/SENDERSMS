"""
The reply path: get a prospect's answer out of their mailbox and into this app.

THE PROBLEM THIS SOLVES
-----------------------
A campaign can land perfectly in the prospect's inbox and still be worthless,
because the reply has nowhere to go. Brevo's inbound parser only ever sees mail
addressed to a domain whose MX records point at Brevo, so a reply to
``you@gmail.com`` never reaches the app at all — and Gmail, on the operator's
side, quite often files that reply under Spam. Two separate failures, both
invisible from inside the app: the reply is not in the inbox, and the operator
cannot see it in Gmail either.

WHAT THIS DOES
--------------
1. **Reads the mailbox** — INBOX *and* Spam — over IMAP (app password) or the
   Gmail API (OAuth), whichever the operator connected.
2. **Recognises a reply** rather than importing a whole mailbox: a message
   counts when its ``In-Reply-To``/``References`` name a Message-ID this app
   sent, or when a reply-prefixed subject matches mail sent to that contact.
   A known sender by itself is not enough, so newsletters and promotions stay
   out of the CRM inbox. Anything else is left alone.
3. **Rescues it from Spam** — with the Gmail API that is one label change
   (remove ``SPAM``, add ``INBOX``); over IMAP it is a copy to INBOX plus a
   delete from Spam, which is what Gmail's own "Not spam" button does.
4. **Prevents the next one** — with the Gmail API it can create a filter whose
   action is ``removeLabelIds: ["SPAM"]``, Gmail's documented "Never send it to
   Spam", for the addresses that matter.
5. **Ingests it through the existing pipeline** — every normalized message is
   handed to :func:`app.services.email_service.process_inbound_email`, so
   threading, campaign attribution, AI classification, auto-replies, automations
   and notifications behave exactly as they do for a Brevo-inbound reply. One
   pipeline, three doors.
6. **Sends replies back through the mailbox** when it is connected, because that
   is the root-cause fix: a message Brevo sends with ``From: you@gmail.com``
   fails DMARC for gmail.com, Gmail treats the thread as spoofed, and the
   prospect's reply is then filtered. Sent from the mailbox itself, Google signs
   it, DMARC aligns, and the reply threads normally.

Deliberately not done here: any second implementation of threading or
attribution. Those live in ``email_service`` and this module feeds them.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.models.email import EmailAccount
from app.models.email_inbox import EmailMailbox
from app.providers import gmail as gmail_provider
from app.security.encryption import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)

#: The label Gmail messages get when this app imports or rescues them, so the
#: operator can find them in Gmail too.
LABEL_NAME = "SENDERSMS Replies"
#: Cap on how many "never send it to Spam" filters this app will create. Gmail
#: allows a few hundred, and a filter per contact is not what anybody wants.
MAX_NEVER_SPAM_FILTERS = 25
#: A reply older than this is not imported on the first sync of a mailbox.
FIRST_SYNC_WINDOW_DAYS = 7

FREEMAIL_DOMAINS = (
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "outlook.com",
    "hotmail.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
    "proton.me", "protonmail.com", "mail.com", "zoho.com", "yandex.com",
    "gmx.com", "gmx.net", "inbox.com", "fastmail.com",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_freemail(address: str | None) -> bool:
    """Is this a free webmail address?

    It matters for one reason and it is not snobbery: a campaign whose ``From``
    or ``Reply-To`` is a freemail address but whose mail is actually sent by
    Brevo fails DMARC alignment for that freemail domain. Gmail treats mail
    claiming to be from gmail.com that does not authenticate as gmail.com as
    spoofing, and a freemail ``Reply-To`` on bulk mail from a different domain
    is itself a documented phishing signal. The consequence lands on the
    operator, not the prospect: the *reply* gets filtered into Spam.
    """
    domain = (address or "").strip().lower().split("@")[-1]
    return domain in FREEMAIL_DOMAINS


# ---------------------------------------------------------------------------
# Mailbox rows
# ---------------------------------------------------------------------------


async def list_mailboxes(db: AsyncSession, *, include_inactive: bool = False) -> list[EmailMailbox]:
    query = select(EmailMailbox).order_by(EmailMailbox.id.desc())
    if not include_inactive:
        query = query.where(EmailMailbox.is_active.is_(True))
    return list((await db.execute(query)).scalars().all())


async def get_mailbox(db: AsyncSession, mailbox_id: int | None) -> EmailMailbox | None:
    if not mailbox_id:
        return None
    return (
        await db.execute(select(EmailMailbox).where(EmailMailbox.id == mailbox_id))
    ).scalar_one_or_none()


async def find_mailbox_for_address(db: AsyncSession, address: str | None) -> EmailMailbox | None:
    """The connected mailbox that owns an address, if any."""
    clean = (address or "").strip().lower()
    if not clean:
        return None
    rows = await list_mailboxes(db)
    for mailbox in rows:
        if (mailbox.email_address or "").strip().lower() == clean:
            return mailbox
    return None


async def create_mailbox(
    db: AsyncSession,
    *,
    name: str,
    email_address: str,
    provider: str,
    credential: str,
    client_id: str | None = None,
    client_secret: str | None = None,
    folders: str | None = None,
    **options: Any,
) -> EmailMailbox:
    address = (email_address or "").strip().lower()
    kind = "gmail_api" if str(provider).lower() in ("gmail_api", "gmail", "oauth") else "imap"
    mailbox = EmailMailbox(
        name=(name or f"Replies — {address}")[:150],
        provider=kind,
        email_address=address,
        credential_encrypted=encrypt_value(credential or "")[:2000],
        client_id=(client_id or None),
        client_secret_encrypted=encrypt_value(client_secret)[:1000] if client_secret else None,
        folders=(folders or ("INBOX,[Gmail]/Spam" if kind == "imap" else "INBOX,SPAM"))[:500],
    )
    for field in ("import_all", "rescue_from_spam", "never_spam_filter", "send_replies",
                  "is_active"):
        if field in options and options[field] is not None:
            setattr(mailbox, field, bool(options[field]))
    if options.get("poll_interval"):
        mailbox.poll_interval = max(15, min(int(options["poll_interval"]), 3600))
    db.add(mailbox)
    await db.flush()
    return mailbox


def serialize_mailbox(mailbox: EmailMailbox) -> dict:
    """What the UI may see. The credential never leaves, in any form."""
    return {
        "id": mailbox.id,
        "name": mailbox.name,
        "provider": mailbox.provider,
        "email_address": mailbox.email_address,
        "folders": mailbox.folder_list(),
        "import_all": bool(mailbox.import_all),
        "rescue_from_spam": bool(mailbox.rescue_from_spam),
        "never_spam_filter": bool(mailbox.never_spam_filter),
        "send_replies": bool(mailbox.send_replies),
        "poll_interval": mailbox.poll_interval,
        "is_active": bool(mailbox.is_active),
        "last_sync_at": mailbox.last_sync_at.isoformat() if mailbox.last_sync_at else None,
        "last_sync_status": mailbox.last_sync_status,
        "last_error": mailbox.last_error,
        "total_synced": mailbox.total_synced or 0,
        "total_replies": mailbox.total_replies or 0,
        "total_rescued": mailbox.total_rescued or 0,
        "total_sent": mailbox.total_sent or 0,
        "filters_installed": len(json.loads(mailbox.filter_ids or "[]")),
        "label_id": mailbox.label_id,
        "has_credential": bool(mailbox.credential_encrypted),
        # P1-10: the sync box's banner signal. "needs_reconnect" means the
        # OAuth grant itself is dead (expired/revoked token) — no amount of
        # retrying will fix it, the owner must press Reconnect.
        "needs_reconnect": _error_needs_reconnect(mailbox.last_error),
        "created_at": mailbox.created_at.isoformat() if mailbox.created_at else None,
    }


#: Error fingerprints that mean the OAuth grant itself is dead, not a
#: transient failure: retrying cannot fix these, only Reconnect can.
_RECONNECT_ERROR_MARKERS = (
    "invalid_grant",
    "token.*expired",
    "token.*revoked",
    "unauthorized",
    "401",
    "invalid credentials",
    "credentials.*not.*found",
    "reauthoriz",
    "re-auth",
    "login required",
    "authfail",
    "authentication failed",
)


def _error_needs_reconnect(error: str | None) -> bool:
    import re

    raw = (error or "").strip().lower()
    if not raw:
        return False
    return any(re.search(marker, raw) for marker in _RECONNECT_ERROR_MARKERS)


def _cursor_state(mailbox: EmailMailbox) -> dict:
    try:
        state = json.loads(mailbox.cursor_state or "{}")
        return state if isinstance(state, dict) else {}
    except ValueError:
        return {}


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def _google_credentials(mailbox: EmailMailbox) -> tuple[str | None, str | None]:
    client_id = (mailbox.client_id or settings.GOOGLE_CLIENT_ID or "").strip() or None
    secret = decrypt_value(mailbox.client_secret_encrypted or "") or settings.GOOGLE_CLIENT_SECRET
    return client_id, (secret or "").strip() or None


async def _google_credentials_with_db(
    db: AsyncSession, mailbox: EmailMailbox
) -> tuple[str | None, str | None]:
    """The mailbox's own OAuth client, then the app-saved one, then the env.

    A mailbox stores the client it was connected with, so this only matters when
    it does not (an older row, or one connected before the secret was kept per
    mailbox) — but without the database step, an inbox connected through
    Settings → Gmail replies could not be refreshed by the poller, which reads
    no request and therefore sees no environment override.
    """
    client_id, secret = _google_credentials(mailbox)
    if client_id and secret:
        return client_id, secret
    from app.services import system_settings

    saved_id, saved_secret = await system_settings.google_oauth_credentials(db)
    return client_id or saved_id, secret or saved_secret


async def access_token_for(db: AsyncSession, mailbox: EmailMailbox) -> tuple[str | None, str | None]:
    """A live Gmail API access token, refreshing the stored one when needed.

    Returns ``(token, error)``. The refresh token itself is only replaced if
    Google rotates it, which it does on a re-consent.
    """
    refresh_token = decrypt_value(mailbox.credential_encrypted or "")
    if not refresh_token:
        return None, "This mailbox has no stored Google credential"
    client_id, client_secret = await _google_credentials_with_db(db, mailbox)
    if not client_id or not client_secret:
        return None, (
            "No Google OAuth client is configured, so the refresh token cannot be exchanged. "
            "Save one under Settings → Gmail replies (or set GOOGLE_CLIENT_ID / "
            "GOOGLE_CLIENT_SECRET in the environment), or connect with an IMAP app password "
            "instead."
        )
    result = await gmail_provider.google_refresh_access_token(client_id, client_secret,
                                                             refresh_token)
    if not result.get("success"):
        return None, str(result.get("error"))
    if result.get("refresh_token") and result["refresh_token"] != refresh_token:
        mailbox.credential_encrypted = encrypt_value(result["refresh_token"])[:2000]
        await db.flush()
    return str(result["access_token"]), None


def imap_credentials(mailbox: EmailMailbox) -> tuple[str, str | None]:
    return mailbox.email_address, decrypt_value(mailbox.credential_encrypted or "") or None


# ---------------------------------------------------------------------------
# Is this message a reply we want?
# ---------------------------------------------------------------------------


async def _our_addresses(db: AsyncSession) -> set[str]:
    """Every address that belongs to the operator, so we never import ourselves."""
    addresses: set[str] = set()
    accounts = (await db.execute(select(EmailAccount))).scalars().all()
    from app.services.email_service import account_reply_to_addresses

    for account in accounts:
        for value in [account.from_email, *account_reply_to_addresses(account)]:
            if value:
                addresses.add(value.strip().lower())
    mailboxes = (await db.execute(select(EmailMailbox.email_address))).scalars().all()
    addresses.update((value or "").strip().lower() for value in mailboxes)
    return {a for a in addresses if a}


def _message_ids(item: dict) -> list[str]:
    """Every Message-ID this message points at (its own chain)."""
    headers = item.get("Headers") or {}
    values: list[str] = []
    for key in ("In-Reply-To", "inReplyTo", "InReplyTo"):
        if item.get(key):
            values.append(str(item[key]))
        if headers.get(key):
            values.append(str(headers[key]))
    for key in ("References", "references"):
        if item.get(key):
            values.append(str(item[key]))
        if headers.get(key):
            values.append(str(headers[key]))
    out: list[str] = []
    for chunk in values:
        for token in re.split(r"\s+", chunk.strip()):
            token = token.strip()
            if token and token not in out:
                out.append(token)
    return out



async def classify_inbound(
    db: AsyncSession, item: dict, mailbox: EmailMailbox, our_addresses: set[str]
) -> tuple[bool, str]:
    """Decide whether a mailbox message answers something this app sent.

    A sender's presence in Contacts is deliberately insufficient: known people
    receive newsletters and promotions too. We import explicit RFC-threaded
    messages, a reply subject tied to that known contact's outbound mail, or an
    unambiguous reply subject from a changed/personal sender.
    """
    sender = str((item.get("From") or {}).get("Address") or "").strip().lower()
    if not sender:
        return False, "no sender"
    if sender in our_addresses:
        return False, "from you"

    if mailbox.import_all:
        return True, "import-all"

    from app.services import email_service

    in_reply_to = item.get("InReplyTo") or item.get("In-Reply-To") or item.get("inReplyTo")
    references = item.get("References") or item.get("references")
    threaded = await email_service.find_outgoing_email_by_message_ids(
        db, str(in_reply_to or "") or None, str(references or "") or None
    )
    if threaded is not None:
        return True, "threads a message you sent"

    contact = await email_service.find_contact_by_email(db, sender)
    if contact is not None:
        answered = await email_service.find_outgoing_email_by_reply_subject(
            db, str(item.get("Subject") or ""), contact_id=contact.id
        )
        if answered is not None:
            return True, "reply subject matches mail sent to this contact"
        return False, "known contact, but no matching sent thread"

    # A changed/personal address can still be recognized when a reply-prefixed
    # subject uniquely identifies one outbound contact. The ingestion service
    # then stores this address as an alternate instead of creating a duplicate.
    answered = await email_service.find_outgoing_email_by_reply_subject(
        db, str(item.get("Subject") or "")
    )
    if answered is not None:
        return True, "reply subject uniquely matches mail you sent"

    return False, "not a reply to anything this app sent"


# ---------------------------------------------------------------------------
# Spam rescue
# ---------------------------------------------------------------------------


async def rescue_from_spam(db: AsyncSession, mailbox: EmailMailbox, item: dict,
                           access_token: str | None = None) -> dict:
    """Move a reply Gmail put in Spam back into the Inbox.

    This is the visible half of the fix for "the prospect replied and it landed
    in my spam". It only ever runs for a message :func:`classify_inbound`
    recognised as a reply to this app's own mail, so it cannot be used to pull
    real spam back into the operator's inbox.
    """
    if not mailbox.rescue_from_spam:
        return {"rescued": False, "reason": "rescue is switched off for this mailbox"}

    if mailbox.provider == "gmail_api":
        message_id = str(item.get("_provider_message_id") or "")
        if not message_id:
            return {"rescued": False, "reason": "no Gmail message id"}
        token = access_token or (await access_token_for(db, mailbox))[0]
        if not token:
            return {"rescued": False, "reason": "no Gmail API access token"}
        add = ["INBOX", "UNREAD"] + ([mailbox.label_id] if mailbox.label_id else [])
        result = await gmail_provider.gmail_modify(token, message_id, add=add, remove=["SPAM"])
        if result.get("success"):
            if not mailbox.label_id:
                mailbox.label_id = await gmail_provider.gmail_ensure_label(token, LABEL_NAME)
            return {"rescued": True, "how": "gmail-api"}
        return {"rescued": False, "reason": str(result.get("error"))[:300]}

    # IMAP: copy to INBOX, then delete the Spam copy — Gmail's own "Not spam".
    uid = item.get("_uid")
    password = decrypt_value(mailbox.credential_encrypted or "")
    if not uid or not password:
        return {"rescued": False, "reason": "no IMAP uid or credential"}
    folder = str(item.get("_folder") or "[Gmail]/Spam")
    result = await gmail_provider.imap_rescue_from_spam(
        mailbox.email_address, password, int(uid), folder
    )
    if result.get("success"):
        return {"rescued": True, "how": "imap-copy"}
    return {"rescued": False, "reason": str(result.get("error"))[:300]}


async def ensure_never_spam_filters(
    db: AsyncSession, mailbox: EmailMailbox, addresses: list[str] | None = None
) -> dict:
    """Create Gmail filters that say "never send it to Spam".

    ``removeLabelIds: ["SPAM"]`` is the documented filter action for Gmail's
    *Never send it to Spam* checkbox — the preventive half of the rescue work.
    One filter per address (a filter's criteria cannot hold a list), capped, and
    existing filters are detected first so re-running does not pile up
    duplicates.
    """
    if mailbox.provider != "gmail_api":
        return {
            "success": False,
            "error": "Gmail filters need the Gmail API connection (OAuth). The IMAP app-password "
                     "door cannot create filters — replies that land in Spam are still rescued "
                     "and imported, they just are not prevented.",
        }
    token, error = await access_token_for(db, mailbox)
    if not token:
        return {"success": False, "error": error}

    if not mailbox.label_id:
        mailbox.label_id = await gmail_provider.gmail_ensure_label(token, LABEL_NAME)

    if addresses is None:
        rows = (
            await db.execute(
                select(Contact.email)
                .where(Contact.email.isnot(None), Contact.last_email_reply_at.isnot(None))
                .order_by(Contact.last_email_reply_at.desc())
                .limit(MAX_NEVER_SPAM_FILTERS * 2)
            )
        ).scalars().all()
        addresses = [str(r).strip().lower() for r in rows if r]

    existing = await gmail_provider.gmail_list_filters(token)
    already: set[str] = set()
    if existing.get("success"):
        for row in existing["data"].get("filter") or []:
            sender = str(((row or {}).get("criteria") or {}).get("from") or "").strip().lower()
            if sender:
                already.add(sender)

    installed = json.loads(mailbox.filter_ids or "[]")
    created, failed = 0, []
    action: dict[str, Any] = {"removeLabelIds": ["SPAM"]}
    if mailbox.label_id:
        action["addLabelIds"] = [mailbox.label_id]

    for address in dict.fromkeys(a for a in addresses if a):
        if address in already or len(installed) >= MAX_NEVER_SPAM_FILTERS:
            continue
        result = await gmail_provider.gmail_create_filter(
            token, {"from": address, "to": mailbox.email_address}, action
        )
        if result.get("success") and result["data"].get("id"):
            installed.append({"id": str(result["data"]["id"]), "from": address,
                              "created_at": _now().isoformat()})
            created += 1
        else:
            failed.append(f"{address}: {str(result.get('error'))[:120]}")
        if created + len(failed) >= MAX_NEVER_SPAM_FILTERS:
            break

    mailbox.filter_ids = json.dumps(installed)[:8000]
    await db.flush()
    return {"success": True, "created": created, "total": len(installed),
            "failed": failed[:5], "label_id": mailbox.label_id}


async def remove_filters(db: AsyncSession, mailbox: EmailMailbox) -> int:
    """Delete the filters this app created — called when a mailbox is removed."""
    installed = json.loads(mailbox.filter_ids or "[]")
    if not installed or mailbox.provider != "gmail_api":
        mailbox.filter_ids = "[]"
        return 0
    token, _ = await access_token_for(db, mailbox)
    if not token:
        return 0
    removed = 0
    for entry in installed:
        result = await gmail_provider.gmail_delete_filter(token, str(entry.get("id")))
        if result.get("success"):
            removed += 1
    mailbox.filter_ids = "[]"
    await db.flush()
    return removed


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


async def _fetch_imap(db: AsyncSession, mailbox: EmailMailbox) -> tuple[list[dict], dict, list[str]]:
    password = decrypt_value(mailbox.credential_encrypted or "")
    if not password:
        return [], {}, ["This mailbox has no stored app password"]
    state = _cursor_state(mailbox)
    first_sync = not bool(state.get("folders"))
    result = await gmail_provider.imap_sync(
        mailbox.email_address, password, mailbox.folder_list(), state.get("folders") or {},
        import_all=bool(mailbox.import_all), first_sync=first_sync,
    )
    new_state = {**state, "folders": {**state.get("folders", {}), **(result.get("cursors") or {})}}
    return result.get("items") or [], new_state, result.get("errors") or []


async def _fetch_gmail_api(db: AsyncSession, mailbox: EmailMailbox) -> tuple[list[dict], dict, list[str]]:
    token, error = await access_token_for(db, mailbox)
    if not token:
        return [], {}, [error or "Could not refresh the Gmail API token"]
    state = _cursor_state(mailbox)
    errors: list[str] = []
    items: list[dict] = []
    window_start = state.get("since") or (
        _now() - timedelta(days=FIRST_SYNC_WINDOW_DAYS)
    ).isoformat()
    # Gmail search cannot take an RFC3339 timestamp, only a date, and it is
    # inclusive — so the dedupe in process_inbound_email is what stops a message
    # on the boundary being stored twice.
    after = str(window_start)[:10].replace("-", "/")
    for label in ("INBOX", "SPAM"):
        listed = await gmail_provider.gmail_list(
            token, query=f"after:{after}", label_ids=[label],
            max_results=gmail_provider.MAX_PER_SYNC,
        )
        if not listed.get("success"):
            errors.append(f"{label}: {listed.get('error')}")
            continue
        for stub in listed["data"].get("messages") or []:
            fetched = await gmail_provider.gmail_get_message(token, str(stub.get("id")))
            if not fetched.get("success"):
                errors.append(f"message {stub.get('id')}: {fetched.get('error')}")
                continue
            items.append(fetched["item"])
    new_state = {**state, "since": _now().isoformat()}
    return items, new_state, errors


async def sync_mailbox(db: AsyncSession, mailbox: EmailMailbox) -> dict:
    """One sync pass over a connected mailbox.

    Returns a summary the UI shows verbatim: how many messages were seen, how
    many were recognised as replies, how many were rescued from Spam, how many
    were stored, and what went wrong.
    """
    started = _now()
    summary: dict[str, Any] = {
        "mailbox_id": mailbox.id,
        "email": mailbox.email_address,
        "provider": mailbox.provider,
        "seen": 0, "replies": 0, "rescued": 0, "stored": 0,
        "skipped": 0, "errors": [], "started_at": started.isoformat(),
    }
    try:
        if mailbox.provider == "gmail_api":
            items, state, errors = await _fetch_gmail_api(db, mailbox)
        else:
            items, state, errors = await _fetch_imap(db, mailbox)
    except Exception as exc:  # noqa: BLE001 — a mailbox failure is reported, not raised
        logger.warning("MAILBOX %s sync failed: %s", mailbox.email_address, exc)
        mailbox.last_sync_status = "error"
        mailbox.last_error = str(exc)[:500]
        mailbox.last_sync_at = started
        await db.flush()
        summary["errors"].append(str(exc)[:300])
        summary["success"] = False
        await _mark_sync_failure(db, mailbox, str(exc)[:500])
        return summary

    summary["seen"] = len(items)
    summary["errors"].extend(errors[:5])

    our_addresses = await _our_addresses(db)
    access_token: str | None = None
    if mailbox.provider == "gmail_api":
        access_token, token_error = await access_token_for(db, mailbox)
        if token_error:
            summary["errors"].append(token_error)

    wanted: list[dict] = []
    for item in items:
        is_reply, reason = await classify_inbound(db, item, mailbox, our_addresses)
        if not is_reply:
            summary["skipped"] += 1
            continue
        summary["replies"] += 1
        folder = str(item.get("_folder") or "").upper()
        labels = [str(x).upper() for x in (item.get("_labels") or [])]
        in_spam = "SPAM" in folder or "SPAM" in labels
        if in_spam:
            rescue = await rescue_from_spam(db, mailbox, item, access_token=access_token)
            if rescue.get("rescued"):
                summary["rescued"] += 1
                mailbox.total_rescued = (mailbox.total_rescued or 0) + 1
                logger.info("MAILBOX %s: rescued a reply from Spam (%s)",
                            mailbox.email_address, (item.get("From") or {}).get("Address"))
            else:
                summary["errors"].append(f"rescue failed: {rescue.get('reason')}")
        wanted.append(item)

    stored = 0
    if wanted:
        from app.services.email_service import process_inbound_email

        for item in wanted:
            item["_mailbox_id"] = mailbox.id
        result = await process_inbound_email(db, {"items": wanted})
        stored = int(result.get("stored") or 0)
    summary["stored"] = stored

    mailbox.cursor_state = json.dumps(state)[:8000] if state else mailbox.cursor_state
    mailbox.last_sync_at = started
    mailbox.last_sync_status = "error" if (errors and not stored) else "ok"
    mailbox.last_error = ("; ".join(errors)[:500] if errors else None)
    mailbox.total_synced = (mailbox.total_synced or 0) + len(items)
    mailbox.total_replies = (mailbox.total_replies or 0) + stored
    await db.flush()
    if mailbox.last_sync_status == "error":
        await _mark_sync_failure(db, mailbox, mailbox.last_error or "sync failed")
    else:
        await _clear_sync_failure(db, mailbox)

    if stored or summary["rescued"]:
        await _notify(db, mailbox, summary)

    summary["success"] = mailbox.last_sync_status == "ok"
    summary["duration_ms"] = int((_now() - started).total_seconds() * 1000)
    return summary


async def _mark_sync_failure(db: AsyncSession, mailbox: EmailMailbox, error: str) -> None:
    """Remember when the failures started and tell the owner — at most hourly.

    "It has been failing for more than an hour" must be said out loud: a
    mailbox quietly failing means replies silently land nowhere. The failure
    start and last notice live in system_settings (no schema change).
    """
    try:
        from app.services.system_settings import get_setting, set_setting

        since_key = f"mailbox.failing_since.{mailbox.id}"
        noted_key = f"mailbox.failing_notified.{mailbox.id}"
        now = _now()
        since_raw = await get_setting(db, since_key)
        if not since_raw:
            await set_setting(db, since_key, now.isoformat(), category="mailboxes",
                              description="When this mailbox's sync failures started")
            return
        try:
            since = datetime.fromisoformat(since_raw)
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
        except ValueError:
            since = now
        failing_minutes = int((now - since).total_seconds() // 60)
        last_noted_raw = await get_setting(db, noted_key)
        if last_noted_raw:
            try:
                last_noted = datetime.fromisoformat(last_noted_raw)
                if last_noted.tzinfo is None:
                    last_noted = last_noted.replace(tzinfo=timezone.utc)
            except ValueError:
                last_noted = since
        else:
            last_noted = since
        if failing_minutes < 60 or (now - last_noted).total_seconds() < 3600:
            return
        await set_setting(db, noted_key, now.isoformat(), category="mailboxes",
                          description="When this mailbox's failure was last announced")
        from app.services.push_service import notify

        needs_reconnect = _error_needs_reconnect(error)
        title = f"⚠️ {mailbox.email_address} has not synced for {failing_minutes // 60}h+"
        body = (
            ("Gmail access needs to be reconnected: " if needs_reconnect else "Mailbox sync is failing: ")
            + (error or "unknown error")[:200]
        )
        await notify(db, "email_reply", title, body, url="/email-accounts",
                     reference_id=mailbox.id, reference_type="mailbox")
    except Exception as exc:  # noqa: BLE001 — bookkeeping must never break a sync
        logger.warning("MAILBOX failure bookkeeping failed: %s", exc)


async def _clear_sync_failure(db: AsyncSession, mailbox: EmailMailbox) -> None:
    try:
        from app.services.system_settings import set_setting

        await set_setting(db, f"mailbox.failing_since.{mailbox.id}", "",
                          category="mailboxes",
                          description="When this mailbox's sync failures started")
        await set_setting(db, f"mailbox.failing_notified.{mailbox.id}", "",
                          category="mailboxes",
                          description="When this mailbox's failure was last announced")
    except Exception as exc:  # noqa: BLE001
        logger.warning("MAILBOX failure clear failed: %s", exc)


async def _notify(db: AsyncSession, mailbox: EmailMailbox, summary: dict) -> None:
    """Report what a sync rescued from Spam.

    Individual replies are announced per message, with the sender's name and a
    preview, by ``email_service.process_inbound_email`` — which is the one
    place every reply passes through, whatever door it came in by. This summary
    is therefore only for the thing the per-message notice cannot say: that
    Gmail had filed a real reply as Spam and this app moved it back.
    """
    try:
        from app.services.push_service import notify

        rescued = int(summary.get("rescued") or 0)
        if not rescued:
            return
        title = f"🛟 {rescued} repl{'y' if rescued == 1 else 'ies'} rescued from Spam"
        body = (
            f"{mailbox.email_address} had {rescued} real "
            f"repl{'y' if rescued == 1 else 'ies'} in Spam. Moved to the inbox and into this "
            "app's Email Inbox."
        )
        await notify(db, "email_reply", title, body, url="/email-inbox",
                     reference_id=mailbox.id, reference_type="mailbox")
    except Exception as exc:  # noqa: BLE001 — notifications must never break a sync
        logger.warning("MAILBOX notify failed: %s", exc)


async def sync_due_mailboxes(db: AsyncSession) -> list[dict]:
    """Every active mailbox whose poll interval has elapsed. Called by the poller."""
    rows = (
        await db.execute(
            select(EmailMailbox).where(EmailMailbox.is_active.is_(True))
        )
    ).scalars().all()
    due = [
        mailbox for mailbox in rows
        if mailbox.last_sync_at is None
        or mailbox.last_sync_at <= _now() - timedelta(seconds=max(15, mailbox.poll_interval or 60))
    ]
    if not due:
        return []
    results = []
    for mailbox in due:
        results.append(await sync_mailbox(db, mailbox))
    return results


async def check_credentials(db: AsyncSession, mailbox: EmailMailbox) -> dict:
    """Prove a stored credential still works, and say what the mailbox offers."""
    if mailbox.provider == "gmail_api":
        token, error = await access_token_for(db, mailbox)
        if not token:
            return {"success": False, "error": error}
        profile = await gmail_provider.gmail_profile(token)
        if not profile.get("success"):
            return {"success": False, "error": profile.get("error")}
        if not mailbox.label_id:
            mailbox.label_id = await gmail_provider.gmail_ensure_label(token, LABEL_NAME)
            await db.flush()
        return {"success": True, "provider": "gmail_api", **profile,
                "can_create_filters": True, "label_id": mailbox.label_id}

    address, password = imap_credentials(mailbox)
    if not password:
        return {"success": False, "error": "No app password stored for this mailbox"}
    result = await gmail_provider.imap_check(address, password)
    return {**result, "provider": "imap", "can_create_filters": False}


# ---------------------------------------------------------------------------
# Sending a reply through the mailbox
# ---------------------------------------------------------------------------


async def send_via_mailbox(
    db: AsyncSession,
    mailbox: EmailMailbox,
    *,
    to_address: str,
    to_name: str | None,
    subject: str,
    text: str,
    html: str | None,
    in_reply_to: str | None = None,
    references: str | None = None,
    message_id: str | None = None,
    from_name: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    attachments: list[dict] | None = None,
    headers: dict | None = None,
) -> dict:
    """Send one message from the connected mailbox itself.

    Used whenever a connected mailbox is the selected email sender (including
    direct sends and campaigns), and for one-to-one replies routed through the
    mailbox that owns the From address. Bulk sending from Gmail is allowed when
    explicitly selected; Brevo is not a requirement.
    """
    raw, recipients = gmail_provider.build_rfc822(
        from_address=mailbox.email_address,
        from_name=from_name,
        to_address=to_address,
        to_name=to_name,
        subject=subject,
        text=text,
        html=html,
        in_reply_to=in_reply_to,
        references=references,
        message_id=message_id,
        cc=cc, bcc=bcc, attachments=attachments, headers=headers,
    )

    if mailbox.provider == "gmail_api":
        token, error = await access_token_for(db, mailbox)
        if not token:
            return {"success": False, "error": error}
        result = await gmail_provider.gmail_send(token, raw)
    else:
        password = decrypt_value(mailbox.credential_encrypted or "")
        if not password:
            return {"success": False, "error": "No app password stored for this mailbox"}
        result = await gmail_provider.smtp_send(mailbox.email_address, password, raw, recipients)

    if result.get("success"):
        mailbox.total_sent = (mailbox.total_sent or 0) + 1
        await db.flush()
    return result


async def mailbox_for_outgoing(db: AsyncSession, message: Message,
                               account: EmailAccount | None) -> EmailMailbox | None:
    """No longer reroutes a Brevo send just because an address matches a mailbox.

    That override sent composed Brevo mail through Gmail, and then fell back to
    Brevo when Gmail failed — the opposite of both "send from the selected
    sender" and "replies leave from Gmail". Inbox replies use
    :func:`mailbox_for_reply` instead, and only when the message is flagged.
    """
    return None


def _mailbox_rank(mailbox: EmailMailbox) -> tuple:
    """Gmail API before IMAP, then a stable id."""
    return (0 if mailbox.provider == "gmail_api" else 1, mailbox.id)


async def mailbox_for_reply(
    db: AsyncSession,
    message: Message,
    account: EmailAccount | None = None,
    *,
    exclude_ids: set[int] | None = None,
) -> EmailMailbox | None:
    """The connected mailbox an inbox reply should leave from.

    Order: an address match (Reply-To, stored From, or the inbound To), the
    mailbox that imported the inbound, then any active mailbox with
    ``send_replies``. Gmail API is preferred over IMAP at each step.
    """
    excluded = {int(item) for item in (exclude_ids or set())}
    mailboxes = [
        mailbox for mailbox in await list_mailboxes(db)
        if mailbox.is_active and mailbox.send_replies and mailbox.id not in excluded
        and (mailbox.email_address or "").strip()
    ]
    if not mailboxes:
        return None

    addresses: set[str] = set()
    if account is not None:
        from app.services.email_service import account_reply_to_addresses

        addresses.update(
            value.strip().lower()
            for value in account_reply_to_addresses(account)
            if value
        )
        if account.from_email:
            addresses.add(account.from_email.strip().lower())
    imported_ids: list[int] = []
    if message.conversation_id:
        rows = (
            await db.execute(
                select(Message.to_address, Message.email_account_id)
                .where(
                    Message.conversation_id == message.conversation_id,
                    Message.direction == "incoming",
                    Message.channel == "email",
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(8)
            )
        ).all()
        for to_address, account_id in rows:
            if to_address:
                addresses.add(str(to_address).strip().lower())
            if account_id and int(account_id) < 0:
                imported_ids.append(abs(int(account_id)))

    def first_match(predicate) -> EmailMailbox | None:
        matches = [mailbox for mailbox in mailboxes if predicate(mailbox)]
        if not matches:
            return None
        return sorted(matches, key=_mailbox_rank)[0]

    matched = first_match(
        lambda mailbox: (mailbox.email_address or "").strip().lower() in addresses
    )
    if matched is not None:
        return matched
    imported = first_match(lambda mailbox: mailbox.id in imported_ids and mailbox.provider == "gmail_api")
    if imported is not None:
        return imported
    api = first_match(lambda mailbox: mailbox.provider == "gmail_api")
    if api is not None:
        return api
    imported_any = first_match(lambda mailbox: mailbox.id in imported_ids)
    if imported_any is not None:
        return imported_any
    return sorted(mailboxes, key=_mailbox_rank)[0]


# ---------------------------------------------------------------------------
# Reply routing report — what the Deliverability tab shows
# ---------------------------------------------------------------------------


async def reply_routing_report(db: AsyncSession) -> dict:
    """Where a reply can actually go, and why it might be landing in Spam.

    The point of this report is to answer the operator's question in plain
    language instead of showing them DNS records: "my campaigns land in the
    inbox, so why is the *reply* in my spam folder?" Almost always the answer is
    that the From/Reply-To is a freemail address on mail Brevo actually sent,
    which fails DMARC for that domain — and Gmail files the whole thread, reply
    included, as spoofed.
    """
    accounts = (await db.execute(select(EmailAccount))).scalars().all()
    mailboxes = await list_mailboxes(db, include_inactive=True)
    connected = [m for m in mailboxes if m.is_active]

    findings: list[dict] = []
    from app.providers.brevo import is_brevo_relay_address
    from app.services.email_service import account_reply_to_addresses

    for account in accounts:
        if not account.is_active:
            continue
        reply_addresses = account_reply_to_addresses(account)
        for label, value in [("From", account.from_email)] + [
            ("Reply-To", address) for address in reply_addresses
        ]:
            mailbox_for_value = next(
                (
                    mailbox for mailbox in connected
                    if (mailbox.email_address or "").strip().lower() == (value or "").strip().lower()
                ),
                None,
            )
            if is_freemail(value):
                if label == "Reply-To" and mailbox_for_value is not None:
                    findings.append({
                        "severity": "info",
                        "where": f"Sender '{account.name}' — {label}",
                        "value": value,
                        "problem": (
                            "Reply-To is your Gmail address, and that mailbox is connected. "
                            "Replies you write leave from it through the Gmail API. Campaign "
                            "mail still leaves through Brevo."
                        ),
                        "fix": "No change needed for replies. Keep this address as Reply-To so prospect answers land here.",
                    })
                else:
                    findings.append({
                        "severity": "high",
                        "where": f"Sender '{account.name}' — {label}",
                        "value": value,
                        "problem": (
                            f"{label} is a free webmail address, but the mail is actually sent by "
                            "Brevo. Gmail cannot verify a gmail.com/yahoo.com From on mail Brevo "
                            "signed, so DMARC fails and the whole thread is treated as spoofed — "
                            "which is why the prospect's reply lands in your Spam folder, not theirs."
                        ),
                        "fix": (
                            "Connect that mailbox below so replies are read (and sent) from it "
                            "directly. Campaign mail should leave from the @brevosend.com address "
                            "Brevo lists for the key, with this Gmail address as Reply-To."
                        ),
                    })
        relay_from = is_brevo_relay_address(account.from_email) or is_brevo_relay_address(
            getattr(account, "brevo_from_email", None)
        )
        if (
            account.reply_to and account.from_email
            and account.reply_to.split("@")[-1].lower() != account.from_email.split("@")[-1].lower()
            and not relay_from
            and not is_brevo_relay_address(account.reply_to)
        ):
            findings.append({
                "severity": "medium",
                "where": f"Sender '{account.name}'",
                "value": f"{account.from_email} → replies to {account.reply_to}",
                "problem": (
                    "From and Reply-To are on different domains. Receivers' filters read that as "
                    "a phishing pattern, and it splits your replies between two inboxes."
                ),
                "fix": "Use one domain for both, or connect both mailboxes here.",
            })

    if not connected:
        findings.append({
            "severity": "high",
            "where": "Reply inbox",
            "value": "no mailbox connected",
            "problem": (
                "Nothing is reading your mailbox, so a reply can only reach this app if it was "
                "sent to a Brevo inbound address. Replies to a Gmail address are invisible here."
            ),
            "fix": (
                "Connect your Gmail below (app password, or Google OAuth for spam filters and "
                "push). The app then reads INBOX and Spam, threads each reply into the right "
                "conversation, and moves replies out of Spam."
            ),
        })
    else:
        for mailbox in connected:
            if mailbox.provider != "gmail_api":
                findings.append({
                    "severity": "low",
                    "where": f"Mailbox '{mailbox.name}'",
                    "value": mailbox.provider,
                    "problem": (
                        "Connected over IMAP, which can read and rescue replies but cannot create "
                        "Gmail filters."
                    ),
                    "fix": (
                        "Optional: switch this mailbox to the Google (OAuth) connection to let the "
                        "app install 'Never send it to Spam' filters for the people who reply."
                    ),
                })
            if mailbox.last_sync_status == "error":
                findings.append({
                    "severity": "high",
                    "where": f"Mailbox '{mailbox.name}'",
                    "value": mailbox.last_error or "sync failed",
                    "problem": "The last sync failed, so replies are not arriving.",
                    "fix": "Re-check the credential (an app password is revoked when the Google "
                           "password changes; an OAuth refresh token expires on re-consent).",
                })

    rescued_total = sum(m.total_rescued or 0 for m in connected)
    replies_total = sum(m.total_replies or 0 for m in connected)
    return {
        "findings": findings,
        "healthy": not any(f["severity"] == "high" for f in findings),
        "mailboxes": [serialize_mailbox(m) for m in mailboxes],
        "connected": len(connected),
        "replies_imported": replies_total,
        "rescued_from_spam": rescued_total,
        "last_sync_at": (
            max((m.last_sync_at for m in connected if m.last_sync_at), default=None).isoformat()
            if any(m.last_sync_at for m in connected) else None
        ),
        "brevo_inbound_ready": any(a.webhook_token for a in accounts),
        "paths": [
            {
                "id": "brevo",
                "label": "Brevo inbound parsing",
                "status": "ready" if any(a.webhook_token for a in accounts) else "not configured",
                "detail": "Replies addressed to a domain whose MX points at Brevo. Needs a domain "
                          "you control — the strongest setup, because replies never touch Gmail.",
            },
            {
                "id": "gmail_api",
                "label": "Gmail API (OAuth)",
                "status": "connected" if any(m.provider == "gmail_api" and m.is_active
                                             for m in mailboxes) else "not connected",
                "detail": "Reads INBOX and Spam, rescues replies, can install 'never spam' filters "
                          "and send replies from the mailbox itself.",
            },
            {
                "id": "imap",
                "label": "IMAP + app password",
                "status": "connected" if any(m.provider == "imap" and m.is_active
                                             for m in mailboxes) else "not connected",
                "detail": "The five-minute option: reads INBOX and Spam, rescues replies, sends "
                          "through Gmail's SMTP. No Google Cloud project needed.",
            },
        ],
    }
