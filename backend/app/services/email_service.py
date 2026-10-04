"""Email channel service — Brevo accounts, outbound sends, inbound mail, events.

This is the email twin of ``services/sms_service.py``. It owns:

* **Senders (EmailAccount)** — create/update/test Brevo API keys, pick the
  account for a campaign, enforce per-account daily ceilings, and fail over to
  a backup account when the primary one is rejected (burned key, banned
  domain, quota exhausted). Multiple accounts at once is a first-class
  feature, not an afterthought.
* **Outbound email** — renders ``{{shortcodes}}`` exactly like SMS does,
  stores a ``Message`` row with ``channel="email"`` and hands the actual
  delivery to :func:`app.tasks.sms_tasks._send_one`, which routes on channel.
  One pipeline, two providers: delivery receipts, retries, idempotency,
  campaign counters and inbox threading all keep working untouched.
* **Inbound email** — Brevo's inbound-parsing webhook payload becomes a normal
  inbound ``Message`` on an email conversation, so the email inbox behaves
  like the SMS inbox (same unread counts, same campaign attribution, same
  automations/auto-reply hooks).
* **Provider events** — delivered / opened / clicked / bounce / blocked /
  spam / unsubscribe are applied to the message, the contact and the campaign,
  and journalled in ``email_events``.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.utils.urls import public_base_url
from app.models.campaign import Campaign, CampaignContact
from app.models.contact import Contact
from app.models.conversation import Conversation, Message
from app.models.email import EmailAccount, EmailEvent, EmailSuppression
from app.security.encryption import decrypt_value, encrypt_value
from app.utils.naming import contact_display_name

logger = logging.getLogger(__name__)

CHANNELS = ("sms", "email")

#: Never treat these as a real recipient: they are how a Brevo test list or a
#: CSV export spells "no email".
_PLACEHOLDER_DOMAINS = {"example.com", "example.org", "test.com", "localhost", "invalid"}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

UNSUBSCRIBE_KEYWORDS = ("unsubscribe", "stop", "opt out", "opt-out", "remove me", "no more emails")


# ==========================================================================
# Small helpers
# ==========================================================================


def normalize_email(raw: str | None) -> str | None:
    """Lower-case/trim an address, or ``None`` when it is unusable."""
    if not raw:
        return None
    value = str(raw).strip().strip("<>").strip().strip(",").lower()
    if not value or " " in value:
        return None
    if not _EMAIL_RE.match(value):
        return None
    return value


def email_problem(address: str | None) -> str | None:
    """Why an address cannot be emailed, or ``None`` when it is fine."""
    value = normalize_email(address)
    if not value:
        return "no_email"
    local, _, domain = value.partition("@")
    if domain in _PLACEHOLDER_DOMAINS:
        return "placeholder_email"
    if local in {"noreply", "no-reply", "donotreply", "test", "admin@example"}:
        return "placeholder_email"
    return None


def looks_like_unsubscribe(body: str | None) -> bool:
    text = (body or "").strip().lower()
    if not text:
        return False
    if len(text) > 400:
        # Only the first lines of a long reply can plausibly be an opt-out.
        text = text[:400]
    return any(k in text for k in UNSUBSCRIBE_KEYWORDS)


def html_to_text(html: str | None) -> str:
    """A readable plain-text fallback for a body that only exists as HTML.

    The composer writes either or both. A mail client (and every spam filter)
    expects a text part, so an HTML-only message gets one derived here instead
    of being rejected or sent without a text alternative.
    """
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"),
    ):
        text = text.replace(entity, char)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def text_to_html(text: str) -> str:
    """Wrap a plain-text email in a minimal, deliverability-friendly HTML shell."""
    import html as _html

    safe = _html.escape(text or "")
    paragraphs = "".join(
        f"<p style=\"margin:0 0 14px\">{p.replace(chr(10), '<br>')}</p>"
        for p in safe.split("\n\n")
        if p.strip()
    )
    return (
        "<!DOCTYPE html><html><body style=\"margin:0;padding:0;background:#f4f5f7\">"
        "<div style=\"max-width:600px;margin:0 auto;padding:24px;font-family:-apple-system,"
        "Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#111827\">"
        f"{paragraphs}</div></body></html>"
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _parse_ts(raw) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def mask_api_key(encrypted: str | None, api_key: str | None = None) -> str:
    """Show only the last 6 characters of a key, for the settings screen."""
    raw = api_key if api_key is not None else decrypt_value(encrypted or "")
    if not raw:
        return ""
    if len(raw) <= 6:
        return "*" * len(raw)
    return "*" * 8 + raw[-6:]


# ==========================================================================
# Sender (EmailAccount) management
# ==========================================================================


async def list_accounts(db: AsyncSession, *, include_inactive: bool = True) -> list[EmailAccount]:
    query = select(EmailAccount).order_by(EmailAccount.is_default.desc(), EmailAccount.id)
    if not include_inactive:
        query = query.where(EmailAccount.is_active == True)  # noqa: E712 — boolean column
    return list((await db.execute(query)).scalars().all())


async def get_account(db: AsyncSession, account_id: int | None) -> EmailAccount | None:
    if not account_id:
        return None
    return (
        await db.execute(select(EmailAccount).where(EmailAccount.id == account_id))
    ).scalar_one_or_none()


async def get_default_account(db: AsyncSession) -> EmailAccount | None:
    """The default active sender, or the first active one, or None."""
    row = (
        await db.execute(
            select(EmailAccount)
            .where(EmailAccount.is_active == True)  # noqa: E712
            .order_by(EmailAccount.is_default.desc(), EmailAccount.id)
            .limit(1)
        )
    ).scalars().first()
    return row


async def resolve_account(db: AsyncSession, account_id: int | None) -> EmailAccount | None:
    """Pick the sender for a send: requested account, else default, else first.

    An explicitly requested but *inactive* account still wins (the operator
    knows what they asked for) — it is only skipped when it cannot be found.
    When nothing is configured at all the caller must fail the send with a
    clear message instead of silently falling back to SMS.
    """
    if account_id:
        account = await get_account(db, account_id)
        if account is not None:
            return account
    return await get_default_account(db)


async def ensure_default_account(db: AsyncSession) -> EmailAccount | None:
    """Guarantee exactly one default when accounts exist."""
    accounts = await list_accounts(db)
    if not accounts:
        return None
    if any(a.is_default for a in accounts):
        return next(a for a in accounts if a.is_default)
    accounts[0].is_default = True
    await db.flush()
    return accounts[0]


async def set_default_account(db: AsyncSession, account: EmailAccount) -> None:
    await db.execute(update(EmailAccount).values(is_default=False))
    account.is_default = True
    account.is_active = True
    await db.flush()


async def reset_daily_counter(account: EmailAccount, today: date | None = None) -> None:
    """Roll ``sent_today`` over at midnight without a scheduled job."""
    today = today or _now().date()
    if account.last_reset_date != today:
        account.sent_today = 0
        account.last_reset_date = today


async def account_room(account: EmailAccount) -> int | None:
    """Remaining sends today for this account, or None when unlimited."""
    if not account.daily_limit:
        return None
    return max(account.daily_limit - (account.sent_today or 0), 0)


async def account_is_usable(account: EmailAccount) -> tuple[bool, str | None]:
    if not account.is_active:
        return False, "account_disabled"
    if not account.api_key_encrypted:
        return False, "no_api_key"
    if not account.from_email:
        return False, "no_from_address"
    await reset_daily_counter(account)
    room = await account_room(account)
    if room == 0:
        return False, "daily_limit_reached"
    return True, None


def serialize_account(account: EmailAccount, *, stats: dict | None = None) -> dict:
    """Account shape for the API. The API key itself is never returned."""
    api_key = decrypt_value(account.api_key_encrypted or "")
    return {
        "id": account.id,
        "name": account.name,
        "provider": account.provider,
        "from_name": account.from_name,
        "from_email": account.from_email,
        "reply_to": account.reply_to,
        "is_active": bool(account.is_active),
        "is_default": bool(account.is_default),
        "has_api_key": bool(account.api_key_encrypted),
        "api_key_masked": mask_api_key(account.api_key_encrypted) if api_key else "",
        "daily_limit": account.daily_limit,
        "sent_today": account.sent_today or 0,
        "total_sent": account.total_sent or 0,
        "track_opens": bool(account.track_opens),
        "track_clicks": bool(account.track_clicks),
        "connection_status": account.connection_status,
        "last_tested_at": account.last_tested_at.isoformat() if account.last_tested_at else None,
        "last_error": account.last_error,
        "webhook_url": email_webhook_url(account),
        # Path-only form so the UI can build an absolute URL from the browser's
        # own origin when PUBLIC_BASE_URL has not been configured yet.
        "webhook_path": (
            f"/api/v1/webhooks/brevo/{account.id}?token={account.webhook_token or ''}"
        ),
        "public_base_url": public_base_url(),
        "created_at": account.created_at.isoformat() if account.created_at else None,
        "updated_at": account.updated_at.isoformat() if account.updated_at else None,
        **(stats or {}),
    }


def email_webhook_url(account: EmailAccount) -> str | None:
    """The inbound/events webhook URL to paste into Brevo for this account."""
    base = (public_base_url() or "").rstrip("/")
    if not base:
        return None
    return f"{base}/api/v1/webhooks/brevo/{account.id}?token={account.webhook_token or ''}"


async def ensure_webhook_token(db: AsyncSession, account: EmailAccount) -> str:
    """Give an account its inbound webhook token (idempotent)."""
    if not account.webhook_token:
        account.webhook_token = uuid.uuid4().hex
        await db.flush()
    return account.webhook_token


async def test_account(db: AsyncSession, account: EmailAccount, *, persist: bool = True) -> dict:
    """Verify the API key against Brevo and remember the outcome."""
    from app.providers.brevo import test_api_key

    api_key = decrypt_value(account.api_key_encrypted or "")
    result = await test_api_key(api_key)
    if persist:
        account.connection_status = "connected" if result.get("success") else "error"
        account.last_tested_at = _now()
        account.last_error = None if result.get("success") else str(result.get("error"))[:2000]
        await db.flush()
    return result


async def create_account(
    db: AsyncSession,
    *,
    name: str,
    from_name: str,
    from_email: str,
    api_key: str,
    reply_to: str | None = None,
    daily_limit: int | None = None,
    is_active: bool = True,
    is_default: bool = False,
    track_opens: bool = True,
    track_clicks: bool = True,
) -> EmailAccount:
    address = normalize_email(from_email)
    account = EmailAccount(
        name=(name or from_email or "Brevo account").strip()[:150],
        provider="brevo",
        from_name=(from_name or name or "").strip()[:150],
        from_email=address or (from_email or "").strip()[:255],
        reply_to=normalize_email(reply_to) or None,
        api_key_encrypted=encrypt_value((api_key or "").strip()),
        is_active=is_active,
        is_default=is_default,
        daily_limit=daily_limit,
        track_opens=track_opens,
        track_clicks=track_clicks,
        webhook_token=uuid.uuid4().hex,
        last_reset_date=_now().date(),
    )
    db.add(account)
    await db.flush()
    if is_default or len(await list_accounts(db)) == 1:
        await set_default_account(db, account)
    return account


# ==========================================================================
# Suppression / eligibility
# ==========================================================================


async def get_suppression(db: AsyncSession, address: str | None) -> EmailSuppression | None:
    value = normalize_email(address)
    if not value:
        return None
    return (
        await db.execute(select(EmailSuppression).where(EmailSuppression.email_address == value))
    ).scalar_one_or_none()


async def suppress_email(
    db: AsyncSession,
    address: str,
    *,
    contact_id: int | None = None,
    campaign_id: int | None = None,
    reason: str | None = None,
    source: str = "manual",
    hard_bounce: bool = False,
    keyword: str | None = None,
) -> EmailSuppression | None:
    value = normalize_email(address)
    if not value:
        return None
    existing = await get_suppression(db, value)
    if existing:
        if hard_bounce:
            existing.hard_bounce = True
        return existing
    entry = EmailSuppression(
        email_address=value,
        contact_id=contact_id,
        campaign_id=campaign_id,
        reason=(reason or "")[:500] or None,
        source=source,
        hard_bounce=hard_bounce,
        opt_out_keyword=keyword,
    )
    db.add(entry)
    await db.flush()
    return entry


async def unsuppress_email(db: AsyncSession, entry: EmailSuppression) -> None:
    await db.delete(entry)
    await db.flush()


async def contact_email_problem(db: AsyncSession, contact: Contact) -> str | None:
    """Every reason this contact must not be emailed, or ``None``."""
    problem = email_problem(contact.email)
    if problem:
        return problem
    if contact.is_email_opted_out:
        return "email_opted_out"
    if contact.email_status in ("unsubscribed", "complained"):
        return "email_opted_out"
    if contact.is_email_undeliverable or contact.email_status == "bounced":
        return "email_bounced"
    if await get_suppression(db, contact.email):
        return "suppressed"
    return None


async def _suppressed_addresses(db: AsyncSession, addresses: Iterable[str]) -> set[str]:
    values = [normalize_email(a) for a in addresses]
    values = [v for v in values if v]
    if not values:
        return set()
    rows = (
        await db.execute(
            select(EmailSuppression.email_address).where(EmailSuppression.email_address.in_(values))
        )
    ).scalars().all()
    return set(rows)


async def screen_contacts_for_email(
    db: AsyncSession, contacts: list[Contact]
) -> tuple[list[Contact], dict[str, int]]:
    """Email eligibility filter used by audience screening and previews."""
    counts: dict[str, int] = {}

    def bump(reason: str) -> None:
        counts[reason] = counts.get(reason, 0) + 1

    suppressed = await _suppressed_addresses(db, [c.email for c in contacts])
    eligible: list[Contact] = []
    seen: set[str] = set()
    for contact in contacts:
        address = normalize_email(contact.email)
        problem = email_problem(contact.email)
        if problem:
            bump("missing_email" if problem == "no_email" else problem)
            continue
        if contact.is_email_opted_out or contact.email_status in ("unsubscribed", "complained"):
            bump("email_opted_out")
            continue
        if contact.is_email_undeliverable or contact.email_status == "bounced":
            bump("email_bounced")
            continue
        if address in suppressed:
            bump("suppressed")
            continue
        if address in seen:
            bump("duplicate")
            continue
        seen.add(address)
        eligible.append(contact)
    return eligible, counts


# ==========================================================================
# Conversations
# ==========================================================================


async def get_or_create_conversation(
    db: AsyncSession, contact: Contact, *, channel: str = "email", account_id: int | None = None
) -> Conversation:
    """One thread per (contact, channel)."""
    conversation = (
        await db.execute(
            select(Conversation)
            .where(Conversation.contact_id == contact.id, Conversation.channel == channel)
            .order_by(Conversation.id)
            .limit(1)
        )
    ).scalars().first()
    if conversation is None:
        conversation = Conversation(
            contact_id=contact.id,
            channel=channel,
            status="active",
            email_account_id=account_id,
        )
        db.add(conversation)
        await db.flush()
    elif account_id and not conversation.email_account_id:
        conversation.email_account_id = account_id
    return conversation


async def find_conversations(db: AsyncSession, contact_id: int, channel: str) -> Conversation | None:
    return (
        await db.execute(
            select(Conversation)
            .where(Conversation.contact_id == contact_id, Conversation.channel == channel)
            .order_by(Conversation.id)
            .limit(1)
        )
    ).scalars().first()


# ==========================================================================
# Outbound
# ==========================================================================


#: Attachments travel as base64 through JSON into Brevo. These caps keep one
#: message inside Brevo's ~10MB envelope and stop a runaway upload from filling
#: the database.
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 4 * 1024 * 1024          # per file, decoded
MAX_ATTACHMENTS_TOTAL_BYTES = 8 * 1024 * 1024   # per message, decoded


def clean_attachments(items) -> list[dict]:
    """Validate/normalise an attachment list from the API or a template.

    Accepts ``{"name": ..., "content_base64"|"content": ...}`` (and an optional
    ``content_type``). Files over the per-file or per-message caps are dropped
    with a warning rather than silently bloating the request — Brevo would
    reject the whole send otherwise, which is a far worse failure for the user.
    """
    import base64

    cleaned: list[dict] = []
    total = 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:255]
        content = item.get("content_base64") or item.get("content")
        if not name or not content:
            continue
        if isinstance(content, str) and "," in content[:80] and content[:5] == "data:":
            # A data: URL from a browser file reader — keep only the payload.
            content = content.split(",", 1)[1]
        content = str(content).strip()
        try:
            size = len(base64.b64decode(content, validate=False))
        except Exception:  # noqa: BLE001 - not valid base64 at all
            logger.warning("EMAIL: dropping attachment %r (invalid base64)", name)
            continue
        if size > MAX_ATTACHMENT_BYTES:
            logger.warning("EMAIL: dropping attachment %r (%d bytes over cap)", name, size)
            continue
        if total + size > MAX_ATTACHMENTS_TOTAL_BYTES:
            logger.warning("EMAIL: dropping attachment %r (message size cap)", name)
            continue
        if len(cleaned) >= MAX_ATTACHMENTS:
            break
        total += size
        cleaned.append({
            "name": name,
            "content": content,
            "content_type": str(item.get("content_type") or "application/octet-stream")[:120],
            "size": size,
        })
    return cleaned


def clean_addresses(items) -> list[str]:
    """Normalise a CC/BCC list from JSON or a comma-separated string.

    Anything without an ``@`` is dropped so a typo cannot make Brevo reject the
    whole message, and the list is capped at 20 like the provider does.
    """
    if not items:
        return []
    if isinstance(items, str):
        items = re.split(r"[,;\s]+", items)
    seen: list[str] = []
    for item in items:
        value = normalize_email(item if isinstance(item, str) else None)
        if value and value not in seen:
            seen.append(value)
    return seen[:20]


def dump_attachments(items) -> str | None:
    """JSON for storage. Metadata stays queryable; the payload is kept so a
    failed send can be retried without asking the user to upload again."""
    import json as _json

    return _json.dumps(items)[:12_000_000] if items else None


def load_attachments(raw) -> list[dict]:
    import json as _json

    if not raw:
        return []
    try:
        data = _json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def attachment_summary(raw) -> list[dict]:
    """Attachment metadata without the base64 payload (for API responses)."""
    summary = []
    for a in load_attachments(raw):
        entry = {
            "name": a.get("name"),
            "content_type": a.get("content_type"),
            "size": a.get("size"),
        }
        # Mail we RECEIVED has no base64 to show; Brevo gives a download link,
        # and the inbox renders that as a clickable attachment.
        if a.get("url") and not a.get("content"):
            entry["url"] = a.get("url")
        summary.append(entry)
    return summary


def parse_inbound_attachments(item: dict) -> list[dict]:
    """Attachments Brevo forwarded with an inbound message.

    Brevo's inbound payload carries metadata plus either a download URL (an
    "attachment proxy" the account owner enables) or inline base64. Both shapes
    are accepted; anything without a name or a way to fetch it is dropped so the
    inbox never shows an empty paperclip.
    """
    raw = item.get("Attachments") or item.get("attachments") or []
    if isinstance(raw, dict):
        raw = [raw]
    attachments: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("Name") or entry.get("name") or "").strip()
        if not name:
            continue
        content = entry.get("Content") or entry.get("content")
        url = (
            entry.get("DownloadToken")
            or entry.get("downloadToken")
            or entry.get("Url")
            or entry.get("url")
        )
        size = entry.get("ContentLength") or entry.get("contentLength") or entry.get("size")
        record = {
            "name": name[:255],
            "content_type": str(
                entry.get("ContentType") or entry.get("contentType") or "application/octet-stream"
            )[:120],
            "size": int(size) if str(size or "").isdigit() else None,
        }
        if content:
            record["content"] = str(content)
        elif url:
            record["url"] = str(url)[:1000]
        else:
            continue
        attachments.append(record)
    return attachments[:10]


def _unsubscribe_token(address: str) -> str:
    """Deterministic HMAC so an unsubscribe link needs no database row."""
    import hashlib
    import hmac

    secret = (settings.SECRET_KEY or "sendersms").encode()
    return hmac.new(secret, (address or "").strip().lower().encode(), hashlib.sha256).hexdigest()[:32]


def unsubscribe_url(address: str) -> str | None:
    from urllib.parse import quote

    from app.utils.urls import public_base_url

    base = (public_base_url() or "").rstrip("/")
    if not base or not address:
        return None
    return (
        f"{base}/api/v1/email/unsubscribe"
        f"?e={quote(address.strip().lower())}&t={_unsubscribe_token(address)}"
    )


def unsubscribe_token_valid(address: str, token: str) -> bool:
    import hmac

    return bool(address and token) and hmac.compare_digest(_unsubscribe_token(address), token or "")


def _new_rfc_message_id(from_email: str | None) -> str:
    """Our own RFC 5322 Message-ID, so replies can point at exactly this mail."""
    domain = (from_email or "sendersms.local").split("@")[-1] or "sendersms.local"
    return f"<{uuid.uuid4().hex}.{int(_now().timestamp())}@{domain}>"


async def build_headers(
    db: AsyncSession,
    message: "Message",
    account: EmailAccount | None,
    *,
    bulk: bool = False,
) -> dict:
    """Threading + deliverability headers for one outbound email.

    * **Threading** — ``In-Reply-To``/``References`` are taken from the earlier
      messages on this conversation that we sent, so a reply or a follow-up
      stays inside the same mail thread in Gmail/Outlook instead of starting a
      new one. This is the difference between "same chat" and a wall of
      disconnected one-liners.
    * **List-Unsubscribe** — required by Gmail/Yahoo for bulk senders; omitted
      on one-to-one mail so a personal reply still reads as personal.
    """
    headers: dict = {}

    previous = (
        await db.execute(
            select(Message.rfc_message_id, Message.in_reply_to)
            .where(
                Message.conversation_id == message.conversation_id,
                Message.id != (message.id or 0),
                Message.rfc_message_id.isnot(None),
            )
            .order_by(Message.id.desc())
            .limit(6)
        )
    ).all()
    chain = [row[0] for row in previous if row[0]]
    if chain:
        headers["In-Reply-To"] = chain[0]
        # References must read oldest → newest and stay under ~2KB total.
        headers["References"] = " ".join(reversed(chain))[:2000]

    if bulk:
        address = (message.to_address or "").strip().lower()
        url = unsubscribe_url(address)
        mailto = None
        if account is not None:
            reply = (account.reply_to or account.from_email or "").strip()
            if reply:
                mailto = f"mailto:{reply}?subject=unsubscribe"
        parts = [p for p in (f"<{mailto}>" if mailto else None, f"<{url}>" if url else None) if p]
        if parts:
            headers["List-Unsubscribe"] = ", ".join(parts)
            headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    return headers


def _unsubscribe_footer(account: EmailAccount | None) -> str:
    address = (account.reply_to if account else "") or (account.from_email if account else "")
    if not address:
        return ""
    return (
        "\n\n—\nIf you would rather not hear from us again, reply to this email "
        f"with the word UNSUBSCRIBE and you will be removed immediately ({address})."
    )


async def render_email(
    db: AsyncSession,
    contact: Contact,
    *,
    subject: str | None,
    text_body: str | None,
    html_body: str | None,
    account: EmailAccount | None = None,
    append_unsubscribe: bool = False,
) -> dict:
    """Render subject/text/html for one contact through the variable registry.

    Uses the exact same renderer as SMS so ``{{first_name}}``, imported CSV
    columns and operator-defined short codes behave identically on both
    channels. Any short code the contact has no value for is removed rather
    than mailed out verbatim.
    """
    from app.services.variable_service import render_for_contact

    text = await render_for_contact(db, text_body or "", contact) if text_body else ""
    html = await render_for_contact(db, html_body, contact) if html_body else None
    rendered_subject = await render_for_contact(db, subject or "", contact) if subject else ""

    if not rendered_subject.strip():
        rendered_subject = "(no subject)"

    footer = _unsubscribe_footer(account) if append_unsubscribe else ""
    if footer:
        text = (text or "") + footer
    if html:
        if footer:
            html = html + (
                "<div style=\"margin-top:24px;padding-top:12px;border-top:1px solid #e5e7eb;"
                "font-size:12px;color:#6b7280\">If you would rather not hear from us again, reply "
                "with the word UNSUBSCRIBE and you will be removed immediately.</div>"
            )
    else:
        html = text_to_html(text or "")

    return {"subject": rendered_subject, "text": text or "", "html": html}


async def queue_email(
    db: AsyncSession,
    contact: Contact,
    *,
    subject: str | None,
    text_body: str | None,
    html_body: str | None = None,
    account: EmailAccount | None = None,
    campaign_id: int | None = None,
    ads_campaign_id: int | None = None,
    followup: bool = False,
    is_auto_reply: bool = False,
    status: str = "queued",
    idempotency_key: str | None = None,
    scheduled_message_id: int | None = None,
    attachments: list[dict] | None = None,
    bulk: bool = False,
    cc: list[str] | str | None = None,
    bcc: list[str] | str | None = None,
) -> Message | None:
    """Create the outbound email ``Message`` row (delivery happens elsewhere).

    ``attachments`` are base64 entries from the composer/template; ``bulk``
    marks campaign mail (adds the List-Unsubscribe headers a bulk sender needs).
    Returns ``None`` when the contact cannot be emailed — callers treat that as
    "skipped", exactly like an opted-out SMS contact.
    """
    problem = await contact_email_problem(db, contact)
    if problem:
        logger.info("EMAIL: skipping contact %s (%s)", contact.id, problem)
        return None

    account = account or await resolve_account(db, None)
    if account is None:
        logger.warning("EMAIL: no sender account configured; cannot queue for %s", contact.id)
        return None

    address = normalize_email(contact.email)
    # An HTML-only body must still produce a text part: it is the fallback every
    # mail client shows and what spam filters expect. Doing it here (rather than
    # only in the API routes) covers campaigns, scheduled sends and automations.
    if not (text_body or "").strip() and (html_body or "").strip():
        text_body = html_to_text(html_body)

    rendered = await render_email(
        db, contact,
        subject=subject, text_body=text_body, html_body=html_body,
        account=account, append_unsubscribe=True,
    )

    conversation = await get_or_create_conversation(
        db, contact, channel="email", account_id=account.id
    )
    if campaign_id is not None:
        from app.services.attribution import stamp_conversation

        stamp_conversation(conversation, campaign_id=campaign_id)
    if ads_campaign_id is not None:
        from app.services.attribution import stamp_conversation

        stamp_conversation(conversation, ads_campaign_id=ads_campaign_id)

    cleaned_attachments = clean_attachments(attachments)
    message = Message(
        conversation_id=conversation.id,
        contact_id=contact.id,
        campaign_id=campaign_id,
        ads_campaign_id=ads_campaign_id,
        channel="email",
        rfc_message_id=_new_rfc_message_id(account.from_email),
        attachments=dump_attachments(cleaned_attachments),
        bulk_send=bool(bulk),
        cc_addresses=", ".join(clean_addresses(cc)) or None,
        bcc_addresses=", ".join(clean_addresses(bcc)) or None,
        direction="outgoing",
        body=rendered["text"],
        html_body=rendered["html"],
        subject=rendered["subject"][:500],
        from_address=account.from_email,
        to_address=address,
        email_account_id=account.id,
        status=status,
        provider="brevo",
        idempotency_key=idempotency_key or f"email-{contact.id}-{uuid.uuid4().hex[:12]}",
    )
    db.add(message)
    await db.flush()

    conversation.message_count = (conversation.message_count or 0) + 1
    conversation.last_message_preview = rendered["subject"][:100]
    conversation.last_message_at = _now()
    conversation.subject = rendered["subject"][:500]
    if not conversation.email_account_id:
        conversation.email_account_id = account.id

    return message


async def send_now(
    db: AsyncSession,
    contact: Contact,
    *,
    subject: str | None,
    text_body: str | None,
    html_body: str | None = None,
    account: EmailAccount | None = None,
    campaign_id: int | None = None,
    ads_campaign_id: int | None = None,
    is_auto_reply: bool = False,
    attachments: list[dict] | None = None,
    bulk: bool = False,
    cc: list[str] | str | None = None,
    bcc: list[str] | str | None = None,
) -> tuple[Message | None, dict]:
    """Queue + deliver in one call. Used by the API's "send now" paths."""
    message = await queue_email(
        db, contact,
        subject=subject, text_body=text_body, html_body=html_body,
        account=account, campaign_id=campaign_id, ads_campaign_id=ads_campaign_id,
        is_auto_reply=is_auto_reply, attachments=attachments, bulk=bulk,
        cc=cc, bcc=bcc,
    )
    if message is None:
        return None, {"success": False, "error": "contact_not_emailable"}
    result = await deliver(db, message)
    return message, result


async def deliver(db: AsyncSession, message: Message) -> dict:
    """Perform the actual Brevo delivery for an already-claimed message row.

    Account resolution order: the message's account → the campaign's primary →
    the campaign's fallback → the default account. A rejection that looks
    permanent (invalid key, banned sender, blocked) falls through to the next
    candidate; a transient error is reported so the caller can retry later.
    """
    account = await resolve_account(db, message.email_account_id)
    candidates: list[EmailAccount] = []
    if account is not None:
        candidates.append(account)

    campaign_account: EmailAccount | None = None
    if message.campaign_id:
        campaign = (
            await db.execute(select(Campaign).where(Campaign.id == message.campaign_id))
        ).scalar_one_or_none()
        if campaign is not None:
            campaign_account = await get_account(db, campaign.email_account_id) or campaign_account
            if campaign.fallback_email_account_id:
                fallback = await get_account(db, campaign.fallback_email_account_id)
                if fallback is not None:
                    candidates.append(fallback)

    for candidate in candidates:
        usable, why = await account_is_usable(candidate)
        if not usable:
            logger.info("EMAIL: account %s unusable (%s)", candidate.id, why)
            continue

        result = await _send_via_account(db, candidate, message)
        if result.get("success") or not result.get("_try_next"):
            return result

    return {
        "success": False,
        "error": "No usable email sender (check the Brevo API key, daily limit and From address)",
    }


async def _send_via_account(db: AsyncSession, account: EmailAccount, message: Message) -> dict:
    from app.providers.brevo import send_email as brevo_send

    api_key = decrypt_value(account.api_key_encrypted or "")
    if not api_key:
        return {"success": False, "error": "Account has no API key", "_try_next": True}

    contact = (
        await db.execute(select(Contact).where(Contact.id == message.contact_id))
    ).scalar_one_or_none()
    to_name = contact_display_name(contact) if contact else None

    headers = await build_headers(db, message, account, bulk=bool(message.bulk_send))
    result = await brevo_send(
        api_key,
        to_email=message.to_address or (contact.email if contact else None) or "",
        to_name=to_name,
        subject=message.subject or "",
        html=message.html_body,
        text=message.body,
        from_email=account.from_email,
        from_name=account.from_name,
        reply_to=account.reply_to,
        tags=[t for t in (f"campaign-{message.campaign_id}" if message.campaign_id else None,
                          f"ads-{message.ads_campaign_id}" if message.ads_campaign_id else None,
                          "channel-email") if t],
        track_opens=bool(account.track_opens),
        track_clicks=bool(account.track_clicks),
        headers=headers,
        attachments=load_attachments(message.attachments),
        cc=clean_addresses(message.cc_addresses),
        bcc=clean_addresses(message.bcc_addresses),
    )

    if result.get("success"):
        message.email_account_id = account.id
        message.provider = "brevo"
        message.provider_message_id = (result.get("provider_message_id") or "")[:255] or None
        message.provider_response = json.dumps(result.get("raw"))[:8000] if result.get("raw") else None
        message.status = "sent"
        message.sent_at = _now()
        message.failed_at = None
        message.last_error = None
        account.sent_today = (account.sent_today or 0) + 1
        account.total_sent = (account.total_sent or 0) + 1
        account.connection_status = "connected"
        await _note_email_event(db, "sent", message, account=account, detail="accepted by Brevo")
        await db.flush()
        return result

    status_code = result.get("status_code")
    error = str(result.get("error") or "send failed")[:500]
    # 401/403 = bad or revoked key; 400 = sender/domain not allowed. Both mean
    # "this account will not work now" → try the fallback account.
    retryable_other_account = status_code in (400, 401, 402, 403, 429)
    message.last_error = error
    message.provider_response = json.dumps(result.get("raw"))[:8000] if result.get("raw") else None

    if retryable_other_account:
        account.connection_status = "error"
        account.last_error = error
    await db.flush()
    return {**result, "_try_next": retryable_other_account}


# ==========================================================================
# Inbound + events
# ==========================================================================


async def _note_email_event(
    db: AsyncSession,
    event_type: str,
    message: Message | None,
    *,
    account: EmailAccount | None = None,
    contact_id: int | None = None,
    address: str | None = None,
    subject: str | None = None,
    detail: str | None = None,
    link: str | None = None,
    provider_event_id: str | None = None,
    payload: dict | None = None,
    campaign_id: int | None = None,
    ads_campaign_id: int | None = None,
) -> EmailEvent:
    event = EmailEvent(
        event_type=event_type,
        account_id=account.id if account else (message.email_account_id if message else None),
        message_id=message.id if message else None,
        contact_id=contact_id or (message.contact_id if message else None),
        campaign_id=campaign_id or (message.campaign_id if message else None),
        ads_campaign_id=ads_campaign_id or (message.ads_campaign_id if message else None),
        email_address=(address or (message.to_address if message else None) or "")[:255] or None,
        subject=(subject or (message.subject if message else None) or "")[:500] or None,
        provider_event_id=provider_event_id,
        link=(link or "")[:1000] or None,
        detail=(detail or "")[:500] or None,
        payload=json.dumps(payload)[:8000] if payload else None,
    )
    db.add(event)
    await db.flush()
    return event


async def find_message_by_provider_id(db: AsyncSession, provider_id: str | None) -> Message | None:
    if not provider_id:
        return None
    return (
        await db.execute(
            select(Message).where(
                Message.provider_message_id == provider_id,
                Message.channel == "email",
            )
        )
    ).scalar_one_or_none()


async def apply_event(db: AsyncSession, event_type: str, data: dict) -> dict:
    """Apply one Brevo transactional-webhook event to the local state."""
    message_id = data.get("message-id") or data.get("messageId") or data.get("id")
    address = normalize_email(data.get("email"))
    provider_event_id = str(data.get("event-id") or data.get("eventId") or "") or None
    link = data.get("link")
    subject = data.get("subject")

    if provider_event_id:
        duplicate = (
            await db.execute(
                select(EmailEvent.id).where(EmailEvent.provider_event_id == provider_event_id)
            )
        ).scalar_one_or_none()
        if duplicate:
            return {"applied": False, "reason": "duplicate"}

    message = await find_message_by_provider_id(db, message_id)
    if message is None and address:
        # Fall back to the most recent outbound email to that address: Brevo
        # occasionally omits the message id on open/click events.
        message = (
            await db.execute(
                select(Message)
                .where(
                    Message.channel == "email",
                    Message.direction == "outgoing",
                    Message.to_address == address,
                )
                .order_by(Message.id.desc())
                .limit(1)
            )
        ).scalars().first()

    contact_id = message.contact_id if message else None
    account_id = message.email_account_id if message else None

    await _note_email_event(
        db, event_type, message,
        address=address, subject=subject, link=link,
        provider_event_id=provider_event_id, payload=data,
        detail=data.get("reason") or data.get("tag") or None,
    )

    if message is not None:
        now = _now()
        if event_type == "delivered":
            if message.status not in ("delivered",):
                message.status = "delivered"
                message.delivered_at = _parse_ts(data.get("ts_event")) or now
                await _bump_campaign_delivered(db, message, delivered=True)
        elif event_type == "opened":
            message.open_count = (message.open_count or 0) + 1
            message.opened_at = message.opened_at or (_parse_ts(data.get("ts_event")) or now)
            if message.status in ("sent", "queued", "sending"):
                message.status = "delivered"
                message.delivered_at = message.delivered_at or now
        elif event_type == "clicked":
            message.click_count = (message.click_count or 0) + 1
            message.clicked_at = message.clicked_at or (_parse_ts(data.get("ts_event")) or now)
            message.opened_at = message.opened_at or message.clicked_at
        elif event_type in ("bounce", "blocked", "spam", "error"):
            await _apply_bounce(db, message, event_type, data)
        elif event_type == "unsubscribed":
            await unsubscribe_contact(
                db,
                contact_id=contact_id,
                address=address or message.to_address,
                reason=f"Brevo {event_type}",
                source="unsubscribe",
                campaign_id=message.campaign_id,
            )

    await db.flush()
    return {"applied": True, "message_id": message.id if message else None, "event": event_type}


async def _bump_campaign_delivered(db: AsyncSession, message: Message, *, delivered: bool) -> None:
    if not message.campaign_id:
        return
    campaign = (
        await db.execute(select(Campaign).where(Campaign.id == message.campaign_id))
    ).scalar_one_or_none()
    if campaign is not None:
        campaign.messages_delivered = (campaign.messages_delivered or 0) + 1


async def _apply_bounce(db: AsyncSession, message: Message, event_type: str, data: dict) -> None:
    hard = event_type in ("bounce", "blocked", "spam") or str(data.get("type", "")).lower() in (
        "hard_bounce", "hard",
    )
    message.status = "failed"
    message.failed_at = _parse_ts(data.get("ts_event")) or _now()
    message.bounced_at = message.failed_at
    message.bounced_hard = hard
    message.last_error = str(data.get("reason") or data.get("description") or event_type)[:500]

    contact = (
        await db.execute(select(Contact).where(Contact.id == message.contact_id))
    ).scalar_one_or_none()
    if contact is not None:
        contact.email_fail_count = (contact.email_fail_count or 0) + 1
        contact.email_last_error = message.last_error
        if hard:
            contact.is_email_undeliverable = True
            contact.email_status = "bounced"
    if hard and (message.to_address or (contact.email if contact else None)):
        await suppress_email(
            db,
            message.to_address or contact.email,
            contact_id=contact.id if contact else None,
            campaign_id=message.campaign_id,
            reason=f"Hard bounce: {message.last_error}",
            source="bounce",
            hard_bounce=True,
        )
    if message.campaign_id:
        campaign = (
            await db.execute(select(Campaign).where(Campaign.id == message.campaign_id))
        ).scalar_one_or_none()
        if campaign is not None:
            campaign.messages_failed = (campaign.messages_failed or 0) + 1


async def send_composer_test(
    db: AsyncSession,
    *,
    account: EmailAccount,
    to_address: str,
    subject: str | None,
    text_body: str | None,
    html_body: str | None,
    attachments: list[dict] | None = None,
    contact: Contact | None = None,
) -> dict:
    """Mail the composer's current content to one address, as a test.

    Deliberately does NOT create a Message/thread: a test must not look like
    outreach in the inbox, must not bump a campaign and must not teach the
    suppression logic anything. It goes through the same Brevo provider call,
    so what arrives is what a real recipient would get.
    """
    from app.providers.brevo import send_email as brevo_send

    address = normalize_email(to_address)
    if not address:
        return {"success": False, "error": "Enter a valid destination address"}
    api_key = decrypt_value(account.api_key_encrypted or "")
    if not api_key:
        return {"success": False, "error": "This sender has no API key yet"}

    if not (text_body or "").strip() and (html_body or "").strip():
        text_body = html_to_text(html_body)

    rendered_subject = subject or "(no subject)"
    text, html = text_body or "", html_body
    if contact is not None:
        rendered = await render_email(
            db, contact, subject=subject, text_body=text_body, html_body=html_body
        )
        rendered_subject = rendered["subject"]
        text, html = rendered["text"], rendered["html"]
    elif not html:
        html = text_to_html(text)

    files = clean_attachments(attachments)
    result = await brevo_send(
        api_key,
        to_email=address,
        to_name=None,
        subject=f"[TEST] {rendered_subject}"[:500],
        html=html,
        text=text or " ",
        from_email=account.from_email,
        from_name=account.from_name,
        reply_to=account.reply_to,
        tags=["composer-test"],
        track_opens=bool(account.track_opens),
        track_clicks=bool(account.track_clicks),
        attachments=files,
    )
    if result.get("success"):
        return {"success": True, "to": address, "attachments": attachment_summary(
            dump_attachments(files)
        )}
    return {"success": False, "error": result.get("error") or "Brevo rejected the test email"}


async def unsubscribe_contact(
    db: AsyncSession,
    *,
    contact_id: int | None,
    address: str | None,
    reason: str | None = None,
    source: str = "unsubscribe",
    campaign_id: int | None = None,
    keyword: str | None = None,
) -> None:
    """Opt a contact out of email only — their SMS consent is untouched."""
    value = normalize_email(address)
    contact = None
    if contact_id:
        contact = (
            await db.execute(select(Contact).where(Contact.id == contact_id))
        ).scalar_one_or_none()
    if contact is None and value:
        contact = (
            await db.execute(select(Contact).where(func.lower(Contact.email) == value))
        ).scalar_one_or_none()

    if contact is not None:
        contact.is_email_opted_out = True
        contact.email_opted_out_at = _now()
        contact.email_opt_out_reason = (reason or "Unsubscribed")[:500]
        contact.email_status = "unsubscribed"
        value = value or normalize_email(contact.email)

    if value:
        await suppress_email(
            db, value,
            contact_id=contact.id if contact else None,
            campaign_id=campaign_id,
            reason=reason or "Unsubscribed",
            source=source,
            keyword=keyword,
        )
    await db.flush()


async def find_conversation_for_inbound(
    db: AsyncSession,
    *,
    contact: Contact,
    in_reply_to: str | None,
    references: str | None,
    subject: str | None,
) -> tuple[Conversation | None, str | None]:
    """Which existing email thread did this inbound message answer?

    Strict priority — this is what makes a reply land in the SAME chat:

    1. the RFC ``In-Reply-To`` / ``References`` chain pointing at a message we
       sent (exact match, survives a changed subject line);
    2. a ``Re: <subject>`` matching a thread we already hold for this contact;
    3. the contact's newest email thread, if the reply came from their address.

    Only when none of those apply is a brand-new thread started.
    """
    ids: list[str] = []
    for blob in (in_reply_to, references):
        for token in re.findall(r"<[^>\s]+>", blob or ""):
            if token not in ids:
                ids.append(token)
    if ids:
        parent = (
            await db.execute(
                select(Message.conversation_id)
                .where(Message.channel == "email", Message.rfc_message_id.in_(ids))
                .order_by(Message.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if parent:
            conversation = (
                await db.execute(select(Conversation).where(Conversation.id == parent))
            ).scalar_one_or_none()
            if conversation is not None:
                return conversation, "reply-chain"

    base = re.sub(
        r"^\s*((re|fwd|fw|aw|sv)\s*:\s*)+", "", (subject or "").strip(), flags=re.IGNORECASE
    ).strip().lower()
    if base:
        candidate = (
            await db.execute(
                select(Conversation)
                .where(
                    Conversation.contact_id == contact.id,
                    func.coalesce(Conversation.channel, "sms") == "email",
                    func.lower(func.coalesce(Conversation.subject, "")).contains(base[:120]),
                )
                .order_by(Conversation.id.desc())
                .limit(1)
            )
        ).scalars().first()
        if candidate is not None:
            return candidate, "subject"

    # Fall back to the contact's most recent email thread: a reply from the
    # same address is overwhelmingly about the mail it is replying to, and
    # dropping it into the existing chat beats scattering single-message
    # threads for the same person.
    fallback = await find_conversations(db, contact.id, "email")
    if fallback is not None:
        return fallback, "latest-thread"

    return None, None


# ---- Inbound parsing ------------------------------------------------------


def _extract_inbound_items(payload: dict) -> list[dict]:
    """Brevo posts ``{"items": [...]}``; some setups post a bare object."""
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return [i for i in payload["items"] if isinstance(i, dict)]
    if isinstance(payload, list):
        return [i for i in payload if isinstance(i, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _first_address(value) -> str | None:
    """Brevo uses either a string or ``{"Address": ..., "Name": ...}``."""
    if isinstance(value, dict):
        return normalize_email(value.get("Address") or value.get("address") or value.get("Email"))
    if isinstance(value, (list, tuple)):
        return _first_address(value[0]) if value else None
    return normalize_email(str(value or ""))


def _inbound_body(item: dict) -> tuple[str, str | None]:
    """Return ``(plain_text, html)`` from whichever fields Brevo supplied."""
    html = item.get("RawHtmlBody") or item.get("HtmlBody") or item.get("body")
    text = (
        item.get("ExtractedMarkdownMessage")
        or item.get("TextBody")
        or item.get("text")
        or ""
    )
    if not text and html:
        text = re.sub(r"<[^>]+>", " ", str(html))
        text = re.sub(r"\s+", " ", text).strip()
    return (str(text or "").strip(), str(html) if html else None)


async def process_inbound_email(db: AsyncSession, payload: dict) -> dict:
    """Store inbound mail as ``Message`` rows on the email inbox thread."""
    from app.services.sms_service import SMSService

    items = _extract_inbound_items(payload)
    stored = 0
    for item in items:
        from_address = _first_address(
            item.get("From") or item.get("from") or item.get("Sender") or item.get("sender")
        )
        to_address = _first_address(
            item.get("To") or item.get("to") or item.get("Recipient") or item.get("recipient")
        )
        subject = str(item.get("Subject") or item.get("subject") or "")[:500] or None
        # Threading headers Brevo forwards verbatim; either field name appears
        # depending on which inbound-parsing template the account uses.
        headers_blob = item.get("Headers") or item.get("headers") or {}
        if isinstance(headers_blob, list):
            headers_blob = {
                str(h.get("Name") or h.get("name") or ""): str(h.get("Value") or h.get("value") or "")
                for h in headers_blob
                if isinstance(h, dict)
            }
        if not isinstance(headers_blob, dict):
            headers_blob = {}

        def _header(*names: str) -> str | None:
            for name in names:
                for key, value in headers_blob.items():
                    if key.split(":")[0].strip().lower() == name.lower() and value:
                        return str(value)
            for key in names:
                if item.get(key):
                    return str(item[key])
            return None

        in_reply_to = _header("In-Reply-To", "inReplyTo")
        references = _header("References", "references")
        body, html = _inbound_body(item)
        if not from_address or not (body or html):
            logger.info("EMAIL-IN: ignoring unusable item (from=%s)", from_address)
            continue

        provider_id = str(
            item.get("MessageId") or item.get("messageId") or item.get("Uuid") or ""
        ) or None
        received_at = (
            _parse_ts(item.get("Date") or item.get("date") or item.get("receivedAt")) or _now()
        )
        idem = f"email-in-{provider_id}" if provider_id else f"email-in-{uuid.uuid4().hex[:16]}"
        if (
            await db.execute(select(Message.id).where(Message.idempotency_key == idem[:255]))
        ).scalar_one_or_none():
            continue

        contact = (
            await db.execute(select(Contact).where(func.lower(Contact.email) == from_address))
        ).scalars().first()
        if contact is None:
            # The address may arrive via a reply from a different mailbox; fall
            # back to a new contact so nothing is ever silently dropped.
            name_hint = item.get("From")
            display = ""
            if isinstance(name_hint, dict):
                display = str(name_hint.get("Name") or "")
            contact = Contact(
                email=from_address,
                first_name=(display.split(" ")[0] if display else None),
                last_name=(" ".join(display.split(" ")[1:]) or None) if display else None,
                phone_number=f"email:{from_address}"[:20],
                country="Nigeria",
                lead_status="replied",
                source="inbound_email",
            )
            db.add(contact)
            await db.flush()
        elif contact.lead_status == "new":
            contact.lead_status = "replied"

        conversation, matched_by = await find_conversation_for_inbound(
            db, contact=contact, in_reply_to=in_reply_to, references=references, subject=subject
        )
        if conversation is None:
            conversation = await get_or_create_conversation(db, contact, channel="email")
        logger.info(
            "EMAIL-IN: thread %s (matched by %s) for %s",
            conversation.id, matched_by or "new-thread", from_address,
        )
        conversation.status = "unread"
        conversation.unread_count = (conversation.unread_count or 0) + 1
        if subject:
            conversation.subject = subject

        message = Message(
            conversation_id=conversation.id,
            contact_id=contact.id,
            channel="email",
            direction="incoming",
            body=body or "",
            html_body=html,
            subject=subject,
            from_address=from_address,
            to_address=to_address,
            status="delivered",
            provider="brevo",
            provider_message_id=provider_id,
            in_reply_to=in_reply_to,
            attachments=dump_attachments(parse_inbound_attachments(item)),
            idempotency_key=idem[:255],
        )
        message.created_at = received_at
        db.add(message)
        await db.flush()

        # Classify exactly like an inbound SMS so sentiment/intent analytics and
        # the automation triggers work identically on email.
        try:
            from app.services.ai_classifier import classify

            ai = classify(f"{subject or ''} {body}".strip(), contact)
            message.ai_sentiment = ai["sentiment"]
            message.ai_intent = ai["intent"]
            message.ai_confidence = ai["confidence"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("EMAIL-IN classify failed: %s", exc)

        conversation.message_count = (conversation.message_count or 0) + 1
        previous = _aware(conversation.last_message_at)
        if not previous or received_at >= previous:
            conversation.last_message_at = received_at
            conversation.last_message_preview = (subject or body or "")[:100]
        contact.emails_received = (contact.emails_received or 0) + 1
        contact.last_email_reply_at = received_at

        # Credit the reply to the campaign that last emailed them.
        last_out = (
            await db.execute(
                select(Message)
                .where(
                    Message.contact_id == contact.id,
                    Message.direction == "outgoing",
                    Message.channel == "email",
                    Message.campaign_id.isnot(None),
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if last_out is not None:
            campaign = (
                await db.execute(select(Campaign).where(Campaign.id == last_out.campaign_id))
            ).scalar_one_or_none()
            already = (
                await db.execute(
                    select(Message.id).where(
                        Message.contact_id == contact.id,
                        Message.campaign_id == last_out.campaign_id,
                        Message.direction == "incoming",
                        Message.id != message.id,
                    )
                )
            ).scalar_one_or_none()
            if campaign is not None and not already:
                campaign.replies = (campaign.replies or 0) + 1
                cc = (
                    await db.execute(
                        select(CampaignContact).where(
                            CampaignContact.campaign_id == campaign.id,
                            CampaignContact.contact_id == contact.id,
                        )
                    )
                ).scalar_one_or_none()
                if cc is not None:
                    cc.status = "replied"
                    cc.next_action_at = None

        keyword = None
        if looks_like_unsubscribe(body):
            keyword = "unsubscribe"
            await unsubscribe_contact(
                db,
                contact_id=contact.id,
                address=from_address,
                reason=f"Replied with '{keyword}'",
                source="keyword",
                campaign_id=last_out.campaign_id if last_out else None,
                keyword=keyword,
            )

        # Mirror the SMS pipeline: attribute the reply, run auto-reply rules and
        # then the automation engine for this channel.
        if not keyword:
            try:
                await _maybe_auto_reply(db, contact, conversation, message)
            except Exception as exc:  # noqa: BLE001
                logger.error("EMAIL autoreply error: %s", exc)
            try:
                from app.services.automation_service import AutomationService

                await AutomationService(db).run_for_reply(
                    contact,
                    body,
                    {
                        "sentiment": message.ai_sentiment,
                        "intent": message.ai_intent,
                        "labels": [],
                        "channel": "email",
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("EMAIL automation error: %s", exc)
        else:
            from app.models.followup import FollowUp

            await db.execute(
                update(FollowUp)
                .where(FollowUp.contact_id == contact.id, FollowUp.status.in_(["pending", "sending"]))
                .values(status="cancelled")
            )

        stored += 1

    await db.flush()
    return {"stored": stored, "received": len(items)}


async def _maybe_auto_reply(
    db: AsyncSession, contact: Contact, conversation: Conversation, incoming: Message
) -> None:
    """Answer an inbound email if an email auto-reply rule matches."""
    from app.services.autoreply_service import AutoReplyService

    rule, text = await AutoReplyService(db).build_reply(contact, incoming.body, channel="email")
    if not rule or not text:
        return
    account = await get_account(db, rule.email_account_id) or await resolve_account(db, None)
    reply = await queue_email(
        db, contact,
        subject=(rule.subject or f"Re: {incoming.subject or ''}").strip() or None,
        text_body=text,
        account=account,
        is_auto_reply=True,
    )
    if reply is None:
        return
    if reply.conversation_id != conversation.id:
        # Keep the autoresponse inside the thread it answers.
        reply.conversation_id = conversation.id
    rule.times_triggered = (rule.times_triggered or 0) + 1
    rule.last_triggered_at = _now()
    conversation.last_message_preview = (reply.subject or reply.body or "")[:100]
    await db.flush()

    from app.tasks.queue import try_enqueue
    from app.tasks.sms_tasks import send_sms

    try_enqueue(send_sms, reply.id)


async def send_test_email(
    db: AsyncSession, account: EmailAccount, to_address: str, *, subject: str | None = None
) -> dict:
    """Send a real one-off test email through a saved account."""
    from app.providers.brevo import send_email as brevo_send

    address = normalize_email(to_address)
    if not address:
        return {"success": False, "error": "Enter a valid destination address"}
    api_key = decrypt_value(account.api_key_encrypted or "")
    if not api_key:
        return {"success": False, "error": "This account has no API key yet"}
    result = await brevo_send(
        api_key,
        to_email=address,
        to_name=None,
        subject=subject or f"Test email from {account.name}",
        html=text_to_html(
            f"This is a test email sent through your Brevo sender “{account.name}” "
            f"({account.from_email}) from the app. If you received it, the API key, "
            "sender address and domain are all working."
        ),
        text=(
            f"This is a test email sent through your Brevo sender “{account.name}” "
            f"({account.from_email}). If you received it, the API key, sender address "
            "and domain are all working."
        ),
        from_email=account.from_email,
        from_name=account.from_name,
        reply_to=account.reply_to,
        track_opens=False,
        track_clicks=False,
    )
    account.connection_status = "connected" if result.get("success") else "error"
    account.last_tested_at = _now()
    account.last_error = None if result.get("success") else str(result.get("error"))[:2000]
    await db.flush()
    return result


# ==========================================================================
# Stats / overview for the Email Manager
# ==========================================================================


async def email_totals(db: AsyncSession, *, days: int = 30, account_id: int | None = None) -> dict:
    since = _now() - timedelta(days=days - 1)
    query = select(Message).where(Message.channel == "email", Message.created_at >= since)
    if account_id:
        query = query.where(Message.email_account_id == account_id)
    messages = list((await db.execute(query)).scalars().all())

    outbound = [m for m in messages if m.direction == "outgoing"]
    inbound = [m for m in messages if m.direction == "incoming"]
    delivered = [m for m in outbound if m.status == "delivered" or m.delivered_at or m.opened_at]
    failed = [m for m in outbound if m.status == "failed"]
    opened = [m for m in outbound if (m.open_count or 0) > 0 or m.opened_at]
    clicked = [m for m in outbound if (m.click_count or 0) > 0 or m.clicked_at]
    suppressed = (await db.execute(select(func.count()).select_from(EmailSuppression))).scalar() or 0

    def rate(part: int, whole: int) -> float:
        return round(part / whole * 100, 2) if whole else 0.0

    return {
        "period_days": days,
        "sent": len(outbound),
        "delivered": len(delivered),
        "failed": len(failed),
        "queued": len([m for m in outbound if m.status in ("queued", "sending", "retrying")]),
        "replies": len(inbound),
        "opened": len(opened),
        "clicked": len(clicked),
        "opens": sum((m.open_count or 0) for m in outbound),
        "clicks": sum((m.click_count or 0) for m in outbound),
        "unsubscribed": suppressed,
        "delivery_rate": rate(len(delivered), len(outbound)),
        "open_rate": rate(len(opened), len(outbound)),
        "click_rate": rate(len(clicked), len(outbound)),
        "reply_rate": rate(len(inbound), len(outbound)),
        "failure_rate": rate(len(failed), len(outbound)),
        "series": _series(outbound, inbound, since=since, days=days),
    }


def _series(outbound: list[Message], inbound: list[Message], *, since: datetime, days: int) -> list[dict]:
    buckets: dict[str, dict] = {}
    for i in range(days):
        day = (since + timedelta(days=i)).date().isoformat()
        buckets[day] = {"date": day, "sent": 0, "opened": 0, "clicks": 0, "replies": 0}
    for message in outbound:
        key = _aware(message.created_at).date().isoformat()
        if key in buckets:
            buckets[key]["sent"] += 1
            buckets[key]["opened"] += message.open_count or 0
            buckets[key]["clicks"] += message.click_count or 0
    for message in inbound:
        key = _aware(message.created_at).date().isoformat()
        if key in buckets:
            buckets[key]["replies"] += 1
    return list(buckets.values())


async def account_stats(db: AsyncSession, account_id: int) -> dict:
    total_out = (
        await db.execute(
            select(func.count()).select_from(Message).where(
                Message.channel == "email",
                Message.direction == "outgoing",
                Message.email_account_id == account_id,
            )
        )
    ).scalar() or 0
    failed = (
        await db.execute(
            select(func.count()).select_from(Message).where(
                Message.channel == "email",
                Message.direction == "outgoing",
                Message.email_account_id == account_id,
                Message.status == "failed",
            )
        )
    ).scalar() or 0
    campaigns = (
        await db.execute(
            select(func.count()).select_from(Campaign).where(Campaign.email_account_id == account_id)
        )
    ).scalar() or 0
    return {"sent_total": total_out, "failed_total": failed, "campaigns_using": campaigns}
