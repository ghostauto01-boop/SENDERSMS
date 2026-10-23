"""Invoice business logic + Paystack collection.

Everything money-related lives here so the API layer stays thin and testable:

* totals are always recomputed from line items on the server (a client cannot
  POST a total of ₦1 for a ₦1,000,000 invoice);
* invoice numbers come from a locked per-year counter, so two operators saving
  at the same moment cannot produce the same number;
* the public link token is random and independent of the database id;
* Paystack is called through :func:`paystack_request`, which is the only place
  that knows the API base URL and the Bearer header — tests monkeypatch it;
* a payment can arrive twice (browser callback *and* webhook). Both paths call
  :func:`record_payment`, which is idempotent on (provider, reference).

Secrets (the Paystack secret key) are stored with the same Fernet encryption as
the SMS gateway and Brevo credentials, and are never returned by the API.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from urllib.parse import quote
from uuid import uuid4

import httpx
from sqlalchemy import func, inspect as sa_inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.invoice import (
    INVOICE_CURRENCIES,
    Invoice,
    InvoiceEvent,
    InvoiceItem,
    InvoicePayment,
)
from app.models.system import SystemSetting
from app.security.encryption import decrypt_value, encrypt_value, mask_value
from app.services.system_settings import get_setting, set_setting
from app.utils.urls import public_base_url

logger = logging.getLogger(__name__)

PAYSTACK_API = "https://api.paystack.co"

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

#: Every invoice/paystack setting key, kept together so the settings screen and
#: the service can never drift apart.
S_BUSINESS_NAME = "invoice.business_name"
S_BUSINESS_EMAIL = "invoice.business_email"
S_BUSINESS_PHONE = "invoice.business_phone"
S_BUSINESS_ADDRESS = "invoice.business_address"
S_BUSINESS_WEBSITE = "invoice.business_website"
S_BUSINESS_LOGO = "invoice.business_logo_url"
S_PREFIX = "invoice.prefix"
S_DEFAULT_CURRENCY = "invoice.default_currency"
S_DEFAULT_TAX = "invoice.default_tax_rate"
S_DEFAULT_TERMS = "invoice.default_terms"
S_DEFAULT_NOTES = "invoice.default_notes"
S_FOOTER = "invoice.footer"
S_PAYSTACK_PUBLIC = "paystack.public_key"
S_PAYSTACK_SECRET = "paystack.secret_key"
S_PAYSTACK_CHANNELS = "paystack.channels"

DEFAULT_PREFIX = "INV"
DEFAULT_CURRENCY = "NGN"
DEFAULT_TERMS = "Payment is due by the date shown above. Thank you for your business."
DEFAULT_FOOTER = ""

CURRENCY_SYMBOLS = {
    "NGN": "₦",
    "USD": "$",
    "GHS": "GH₵",
    "ZAR": "R",
    "KES": "KSh",
    "XOF": "CFA ",
    "EGP": "E£",
}


def currency_symbol(code: str | None) -> str:
    return CURRENCY_SYMBOLS.get((code or "").upper(), f"{(code or '').upper()} ")


def format_money(minor: int | None, currency: str | None) -> str:
    """₦12,500.00 — the same string in the UI, emails and notifications."""
    amount = (minor or 0) / 100.0
    body = f"{amount:,.2f}"
    return f"{currency_symbol(currency)}{body}"


def _clean_currency(value: str | None, fallback: str = DEFAULT_CURRENCY) -> str:
    code = (value or "").strip().upper()
    return code if code in INVOICE_CURRENCIES else fallback


def _clean_prefix(value: str | None) -> str:
    raw = re.sub(r"[^A-Za-z0-9-]", "", (value or DEFAULT_PREFIX).upper())
    return (raw or DEFAULT_PREFIX)[:12]


async def get_invoice_settings(db: AsyncSession) -> dict[str, Any]:
    """Effective settings, with env fallbacks and the secret decrypted.

    **Internal use only** — :func:`serialize_settings` is what the API returns.
    """
    async def _read(key: str, default: str = "") -> str:
        value = await get_setting(db, key)
        return value if value is not None else default

    secret_stored = await _read(S_PAYSTACK_SECRET)
    secret = decrypt_value(secret_stored) if secret_stored else (settings.PAYSTACK_SECRET_KEY or "")
    public = await _read(S_PAYSTACK_PUBLIC) or (settings.PAYSTACK_PUBLIC_KEY or "")
    channels_raw = await _read(S_PAYSTACK_CHANNELS)
    channels = [c.strip() for c in channels_raw.split(",") if c.strip()] if channels_raw else []
    try:
        tax = float(await _read(S_DEFAULT_TAX, "0") or 0)
    except (TypeError, ValueError):
        tax = 0.0
    return {
        "business_name": await _read(S_BUSINESS_NAME, settings.APP_NAME),
        "business_email": await _read(S_BUSINESS_EMAIL),
        "business_phone": await _read(S_BUSINESS_PHONE),
        "business_address": await _read(S_BUSINESS_ADDRESS),
        "business_website": await _read(S_BUSINESS_WEBSITE),
        "business_logo_url": await _read(S_BUSINESS_LOGO),
        "prefix": await _read(S_PREFIX, DEFAULT_PREFIX),
        "default_currency": _clean_currency(await _read(S_DEFAULT_CURRENCY, DEFAULT_CURRENCY)),
        "default_tax_rate": max(0.0, min(100.0, tax)),
        "default_terms": await _read(S_DEFAULT_TERMS, DEFAULT_TERMS),
        "default_notes": await _read(S_DEFAULT_NOTES),
        "footer": await _read(S_FOOTER, DEFAULT_FOOTER),
        "paystack_public_key": public,
        "paystack_secret_key": secret,
        "paystack_channels": channels,
    }


def serialize_settings(cfg: dict[str, Any], base_url: str | None = None) -> dict[str, Any]:
    """Settings safe to send to the browser (the secret is masked)."""
    secret = cfg.get("paystack_secret_key") or ""
    base = (base_url or public_base_url() or "").rstrip("/")
    return {
        "business_name": cfg["business_name"],
        "business_email": cfg["business_email"],
        "business_phone": cfg["business_phone"],
        "business_address": cfg["business_address"],
        "business_website": cfg["business_website"],
        "business_logo_url": cfg["business_logo_url"],
        "prefix": cfg["prefix"],
        "default_currency": cfg["default_currency"],
        "default_tax_rate": cfg["default_tax_rate"],
        "default_terms": cfg["default_terms"],
        "default_notes": cfg["default_notes"],
        "footer": cfg["footer"],
        "currencies": list(INVOICE_CURRENCIES),
        "paystack": {
            "configured": bool(secret and cfg.get("paystack_public_key")),
            "secret_key_masked": mask_value(secret) if secret else "",
            "public_key": cfg.get("paystack_public_key") or "",
            "channels": cfg.get("paystack_channels") or [],
            "webhook_url": f"{base}/api/v1/invoices/paystack/webhook" if base else "",
            "mode": "test" if secret.startswith(("sk_test_", "pk_test_")) else (
                "live" if secret else "not_configured"
            ),
        },
    }


async def save_invoice_settings(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist only the keys present in ``payload``.

    ``paystack_secret_key`` uses three-state semantics:
      * key absent       → leave the saved secret alone
      * non-empty string → encrypt and replace it
      * empty string     → clear it
    The UI therefore never has to send the secret back to keep it.
    """
    simple = {
        S_BUSINESS_NAME: "business_name",
        S_BUSINESS_EMAIL: "business_email",
        S_BUSINESS_PHONE: "business_phone",
        S_BUSINESS_ADDRESS: "business_address",
        S_BUSINESS_WEBSITE: "business_website",
        S_BUSINESS_LOGO: "business_logo_url",
        S_PREFIX: "prefix",
        S_DEFAULT_CURRENCY: "default_currency",
        S_DEFAULT_TAX: "default_tax_rate",
        S_DEFAULT_TERMS: "default_terms",
        S_DEFAULT_NOTES: "default_notes",
        S_FOOTER: "footer",
        S_PAYSTACK_PUBLIC: "paystack_public_key",
    }
    for key, field in simple.items():
        if field not in payload:
            continue
        value = payload.get(field)
        if field == "prefix":
            value = _clean_prefix(value)
        elif field == "default_currency":
            value = _clean_currency(value)
        elif field == "default_tax_rate":
            try:
                value = str(max(0.0, min(100.0, float(value or 0))))
            except (TypeError, ValueError):
                value = "0"
        else:
            value = "" if value is None else str(value).strip()
        await set_setting(db, key, value, category="invoice")

    if "paystack_secret_key" in payload:
        secret = str(payload.get("paystack_secret_key") or "").strip()
        await set_setting(
            db, S_PAYSTACK_SECRET, encrypt_value(secret) if secret else "", category="invoice"
        )
    if "paystack_channels" in payload:
        raw = payload.get("paystack_channels") or []
        if isinstance(raw, str):
            raw = [part.strip() for part in raw.split(",")]
        clean = [str(c).strip().lower() for c in raw if str(c).strip()]
        await set_setting(db, S_PAYSTACK_CHANNELS, ",".join(clean), category="invoice")
    await db.flush()
    return await get_invoice_settings(db)


def paystack_ready(cfg: dict[str, Any]) -> tuple[bool, str]:
    """Is there enough configuration to collect money? (ok, reason)."""
    if not (cfg.get("paystack_secret_key") or "").strip():
        return False, (
            "Paystack is not connected yet. Add your secret key in Settings → Payments "
            "to collect payment online."
        )
    return True, ""


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------

def _minor(value: Any, *, field: str = "amount") -> int:
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if not math.isfinite(amount):
        raise ValueError(f"{field} must be a finite number")
    # The API accepts a number; everything is rounded once, here, to whole
    # minor units. Negative line prices are rejected by the schema/validator.
    return int(round(amount))


def calculate_totals(
    items: Iterable[dict[str, Any]],
    *,
    discount_minor: int = 0,
    tax_rate: float = 0.0,
) -> dict[str, Any]:
    """Recompute an invoice from its line items. The single source of truth."""
    cleaned: list[dict[str, Any]] = []
    subtotal = 0
    for index, item in enumerate(items):
        description = str(item.get("description") or "").strip() or f"Item {index + 1}"
        try:
            quantity = float(item.get("quantity", 1) or 1)
        except (TypeError, ValueError):
            quantity = 1.0
        quantity = max(0.0, min(1_000_000.0, round(quantity, 3))) or 1.0
        unit = max(0, _minor(item.get("unit_price_minor", item.get("unit_price", 0)), field="unit price"))
        amount = int(round(quantity * unit))
        subtotal += amount
        cleaned.append(
            {
                "description": description[:500],
                "quantity": quantity,
                "unit_price_minor": unit,
                "amount_minor": amount,
                "position": index,
            }
        )
    discount = max(0, min(int(discount_minor or 0), subtotal))
    taxable = subtotal - discount
    rate = max(0.0, min(100.0, float(tax_rate or 0.0)))
    tax = int(round(taxable * rate / 100.0))
    return {
        "items": cleaned,
        "subtotal_minor": subtotal,
        "discount_minor": discount,
        "tax_rate": round(rate, 2),
        "tax_minor": tax,
        "total_minor": taxable + tax,
    }


# ---------------------------------------------------------------------------
# Numbers, status and serialisation
# ---------------------------------------------------------------------------

def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    aware = _as_utc(value)
    return aware.isoformat() if aware else None


async def next_invoice_number(db: AsyncSession, prefix: str | None = None) -> str:
    """``INV-2026-0001`` from a locked per-year counter.

    The row in ``system_settings`` is the counter. On Postgres ``FOR UPDATE``
    serialises two concurrent saves; SQLite serialises the write anyway.
    """
    clean_prefix = _clean_prefix(prefix)
    year = datetime.now(timezone.utc).year
    key = f"invoice.sequence.{year}"
    row = (
        await db.execute(select(SystemSetting).where(SystemSetting.key == key).with_for_update())
    ).scalar_one_or_none()
    if row is None:
        # First invoice of the year. Start after any number already in the table
        # (e.g. an import) so a counter reset can never duplicate one.
        like = f"{clean_prefix}-{year}-%"
        existing = (
            await db.execute(select(Invoice.number).where(Invoice.number.like(like)))
        ).scalars().all()
        highest = 0
        for number in existing:
            tail = str(number).rsplit("-", 1)[-1]
            if tail.isdigit():
                highest = max(highest, int(tail))
        row = SystemSetting(key=key, value=str(highest), category="invoice")
        db.add(row)
        await db.flush()
    current = int(row.value or 0)
    row.value = str(current + 1)
    await db.flush()
    return f"{clean_prefix}-{year}-{current + 1:04d}"


def effective_status(invoice: Invoice) -> str:
    """Stored status plus the two states derived from money and dates."""
    stored = (invoice.status or "draft").lower()
    if stored in ("draft", "void"):
        return stored
    total = int(invoice.total_minor or 0)
    paid = int(invoice.amount_paid_minor or 0)
    if total > 0 and paid >= total:
        return "paid"
    due = _as_utc(invoice.due_date)
    if due is not None and due < datetime.now(timezone.utc):
        return "overdue"
    if paid > 0:
        return "partial"
    return stored if stored in ("sent", "viewed") else "sent"


def status_label(invoice: Invoice) -> str:
    return {
        "draft": "Draft",
        "sent": "Sent",
        "viewed": "Viewed",
        "partial": "Part paid",
        "paid": "Paid",
        "overdue": "Overdue",
        "void": "Void",
    }.get(effective_status(invoice), "Draft")


def _payment_out(payment: InvoicePayment) -> dict[str, Any]:
    return {
        "id": payment.id,
        "provider": payment.provider,
        "reference": payment.reference,
        "amount_minor": payment.amount_minor,
        "currency": payment.currency,
        "status": payment.status,
        "channel": payment.channel,
        "gateway_id": payment.gateway_id,
        "payer_email": payment.payer_email,
        "note": payment.note,
        "paid_at": _iso(payment.paid_at),
        "created_at": _iso(payment.created_at),
    }


def _event_out(event: InvoiceEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "title": event.title,
        "body": event.body,
        "created_at": _iso(event.created_at),
    }


def _item_out(item: InvoiceItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "description": item.description,
        "quantity": item.quantity,
        "unit_price_minor": item.unit_price_minor,
        "amount_minor": item.amount_minor,
        "position": item.position,
    }


def public_link(token: str) -> str:
    base = (public_base_url() or "").rstrip("/")
    return f"{base}/i/{token}" if base else f"/i/{token}"


def _collection(obj: Any, name: str) -> list:
    """Return a relationship only when it is already loaded.

    Serialising a *newly flushed* invoice (the create/duplicate response) would
    otherwise touch an unloaded collection and emit a synchronous lazy SELECT
    from async code — SQLAlchemy raises MissingGreenlet for that. Every list
    endpoint uses ``selectin`` eager loading, so they are unaffected; this guard
    simply makes the create path safe as well.
    """
    try:
        if name in sa_inspect(obj).unloaded:
            return []
    except Exception:  # pragma: no cover - non-mapped object defensive
        return []
    return list(getattr(obj, name, []) or [])


def serialize_invoice(
    invoice: Invoice,
    *,
    public: bool = False,
    business: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paid = int(invoice.amount_paid_minor or 0)
    total = int(invoice.total_minor or 0)
    due = _as_utc(invoice.due_date)
    days_left = None
    if due:
        days_left = (due - datetime.now(timezone.utc)).days
    base = {
        "id": invoice.id,
        "number": invoice.number,
        "public_token": invoice.public_token,
        "public_url": public_link(invoice.public_token),
        # A payment link and an invoice are the same record with a different
        # presentation; the client uses `kind` to choose the simple or the
        # document-shaped screen, and `title` as the human headline.
        "kind": (getattr(invoice, "kind", None) or "invoice"),
        "link_title": getattr(invoice, "link_title", None),
        "title": (
            getattr(invoice, "link_title", None)
            or getattr(invoice, "customer_name", None)
            or (invoice.items[0].description if getattr(invoice, "items", None) else None)
            or "Payment"
        ),
        "status": effective_status(invoice),
        "status_label": status_label(invoice),
        "stored_status": invoice.status,
        "currency": invoice.currency,
        "currency_symbol": currency_symbol(invoice.currency),
        "customer": {
            "contact_id": invoice.contact_id,
            "name": invoice.customer_name,
            "email": invoice.customer_email,
            "phone": invoice.customer_phone,
            "address": invoice.customer_address,
            "business": invoice.customer_business,
        },
        "issue_date": _iso(invoice.issue_date),
        "due_date": _iso(invoice.due_date),
        "days_until_due": days_left,
        "subtotal_minor": int(invoice.subtotal_minor or 0),
        "discount_minor": int(invoice.discount_minor or 0),
        "tax_rate": float(invoice.tax_rate or 0),
        "tax_minor": int(invoice.tax_minor or 0),
        "total_minor": total,
        "amount_paid_minor": paid,
        "balance_minor": max(0, total - paid),
        "notes": invoice.notes,
        "terms": invoice.terms,
        "footer": invoice.footer,
        "items": [_item_out(item) for item in _collection(invoice, "items")],
        "payment_link": invoice.payment_link or public_link(invoice.public_token),
        "paystack_reference": invoice.paystack_reference,
        "created_at": _iso(invoice.created_at),
        "updated_at": _iso(invoice.updated_at),
        "sent_at": _iso(invoice.sent_at),
        "viewed_at": _iso(invoice.viewed_at),
        "paid_at": _iso(invoice.paid_at),
        "last_reminder_at": _iso(invoice.last_reminder_at),
        "reminders_sent": int(invoice.reminders_sent or 0),
    }
    if public:
        base["business"] = {
            "name": (business or {}).get("business_name") or settings.APP_NAME,
            "email": (business or {}).get("business_email") or "",
            "phone": (business or {}).get("business_phone") or "",
            "address": (business or {}).get("business_address") or "",
            "website": (business or {}).get("business_website") or "",
            "logo_url": (business or {}).get("business_logo_url") or "",
        }
        base["payments"] = [
            _payment_out(p) for p in _collection(invoice, "payments") if p.status == "success"
        ]
        # Never expose the activity log (it may contain internal notes).
        return base
    base["payments"] = [_payment_out(p) for p in _collection(invoice, "payments")]
    base["events"] = [_event_out(e) for e in _collection(invoice, "events")]
    base["created_by"] = invoice.created_by
    return base


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------

def log_event(
    db: AsyncSession,
    invoice: Invoice,
    event_type: str,
    title: str,
    body: str | None = None,
    meta: dict | None = None,
) -> InvoiceEvent:
    event = InvoiceEvent(
        event_type=event_type,
        title=title[:255],
        body=(body or None),
        meta_json=meta,
    )
    # Appending (rather than db.add) keeps the already-loaded ``events``
    # collection in sync, so the response that follows a mutation actually
    # includes the event it just wrote.
    invoice.events.append(event)
    return event


# ---------------------------------------------------------------------------
# Paystack
# ---------------------------------------------------------------------------

class PaystackError(RuntimeError):
    """A human-readable Paystack failure (message is safe to show the user)."""


async def paystack_request(
    method: str,
    path: str,
    secret_key: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
) -> dict[str, Any]:
    """One authenticated call to Paystack.

    Kept as a single seam: tests patch this function, and the rest of the app
    never deals with HTTP status parsing.
    """
    if not secret_key:
        raise PaystackError("Paystack secret key is not configured")
    url = f"{PAYSTACK_API}{path}"
    headers = {"Authorization": f"Bearer {secret_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method.upper(), url, headers=headers, json=json_body, params=params
            )
    except httpx.HTTPError as exc:
        raise PaystackError(f"Could not reach Paystack: {exc}") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400 or payload.get("status") is False:
        message = payload.get("message") or response.text[:300] or f"HTTP {response.status_code}"
        raise PaystackError(str(message))
    if not isinstance(payload, dict):
        raise PaystackError("Paystack returned an unexpected response")
    return payload


async def initialize_transaction(
    secret_key: str,
    *,
    email: str,
    amount_minor: int,
    currency: str,
    reference: str,
    callback_url: str | None = None,
    metadata: dict | None = None,
    channels: list[str] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "email": email,
        "amount": int(amount_minor),
        "currency": _clean_currency(currency),
        "reference": reference,
        "metadata": metadata or {},
    }
    if callback_url:
        body["callback_url"] = callback_url
    if channels:
        body["channels"] = channels
    payload = await paystack_request("POST", "/transaction/initialize", secret_key, json_body=body)
    data = payload.get("data") or {}
    if not data.get("authorization_url"):
        raise PaystackError("Paystack did not return a payment link")
    return {
        "authorization_url": data.get("authorization_url"),
        "access_code": data.get("access_code"),
        "reference": data.get("reference") or reference,
    }


async def verify_transaction(secret_key: str, reference: str) -> dict[str, Any]:
    payload = await paystack_request(
        "GET", f"/transaction/verify/{quote(reference, safe='')}", secret_key
    )
    return payload.get("data") or {}


async def paystack_balance(secret_key: str) -> list[dict[str, Any]]:
    payload = await paystack_request("GET", "/balance", secret_key)
    data = payload.get("data")
    return data if isinstance(data, list) else []


def verify_webhook_signature(raw_body: bytes, signature: str | None, secret_key: str) -> bool:
    """Paystack signs the *raw* body with HMAC-SHA512 using the secret key."""
    if not signature or not secret_key:
        return False
    expected = hmac.new(secret_key.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

async def _recalculate_paid(db: AsyncSession, invoice: Invoice) -> int:
    paid = (
        await db.execute(
            select(func.coalesce(func.sum(InvoicePayment.amount_minor), 0)).where(
                InvoicePayment.invoice_id == invoice.id,
                InvoicePayment.status == "success",
            )
        )
    ).scalar() or 0
    invoice.amount_paid_minor = int(paid)
    total = int(invoice.total_minor or 0)
    if total > 0 and invoice.amount_paid_minor >= total:
        invoice.status = "paid"
        invoice.paid_at = invoice.paid_at or datetime.now(timezone.utc)
    elif invoice.amount_paid_minor > 0 and invoice.status == "draft":
        # Money has been received against it, so it is no longer a draft: the
        # operator shared it (or took a deposit). "partial" is then derived.
        invoice.status = "sent"
    elif invoice.status == "paid" and invoice.amount_paid_minor < total:
        # A reversal brought it back below the total.
        invoice.status = "sent"
        invoice.paid_at = None
    return int(paid)


async def record_payment(
    db: AsyncSession,
    invoice: Invoice,
    *,
    provider: str,
    reference: str,
    amount_minor: int,
    currency: str | None = None,
    status: str = "success",
    channel: str | None = None,
    gateway_id: str | None = None,
    payer_email: str | None = None,
    note: str | None = None,
    paid_at: datetime | None = None,
    raw: dict | None = None,
) -> tuple[InvoicePayment, bool]:
    """Idempotently record a payment. Returns ``(payment, created)``.

    Both the Paystack webhook and the browser-side verification call this. The
    unique (provider, reference) pair makes the second arrival a no-op rather
    than a double credit.
    """
    reference = (reference or "").strip()[:120]
    if not reference:
        reference = f"MAN-{uuid4().hex[:12].upper()}"
    existing = (
        await db.execute(
            select(InvoicePayment).where(
                InvoicePayment.provider == provider,
                InvoicePayment.reference == reference,
            )
        )
    ).scalar_one_or_none()
    now = paid_at or datetime.now(timezone.utc)
    if existing is not None:
        changed = False
        # A pending attempt later confirmed by the webhook is upgraded in place.
        if existing.status != "success" and status == "success":
            existing.status = "success"
            existing.amount_minor = int(amount_minor or existing.amount_minor)
            existing.paid_at = now
            existing.channel = channel or existing.channel
            existing.gateway_id = gateway_id or existing.gateway_id
            existing.raw_json = raw or existing.raw_json
            changed = True
        if changed:
            await _after_success(db, invoice, existing, amount_minor=int(amount_minor or 0))
        await db.flush()
        return existing, False

    payment = InvoicePayment(
        invoice_id=invoice.id,
        provider=provider[:30],
        reference=reference,
        amount_minor=int(amount_minor or 0),
        currency=_clean_currency(currency or invoice.currency, invoice.currency),
        status=status if status in ("pending", "success", "failed", "reversed") else "pending",
        channel=(channel or None),
        gateway_id=(gateway_id or None),
        payer_email=(payer_email or None),
        note=(note or None)[:500] if note else None,
        paid_at=now if status == "success" else None,
        raw_json=raw,
    )
    # Keep the loaded collection truthful for the response that follows.
    try:
        invoice.payments.append(payment)
    except Exception:  # pragma: no cover - defensive
        db.add(payment)
    await db.flush()
    if payment.status == "success":
        await _after_success(db, invoice, payment, amount_minor=payment.amount_minor)
    return payment, True


async def _after_success(
    db: AsyncSession, invoice: Invoice, payment: InvoicePayment, *, amount_minor: int
) -> None:
    was_paid = effective_status(invoice) == "paid"
    await _recalculate_paid(db, invoice)
    fully_paid = effective_status(invoice) == "paid"
    if fully_paid and not was_paid:
        invoice.status = "paid"
        invoice.paid_at = payment.paid_at or datetime.now(timezone.utc)
        log_event(
            db,
            invoice,
            "paid",
            f"Paid in full — {format_money(amount_minor, payment.currency)}",
            f"Payment received via {payment.provider.title()}"
            + (f" ({payment.channel})" if payment.channel else ""),
        )
        await _notify_paid(db, invoice, payment)
    else:
        log_event(
            db,
            invoice,
            "partial",
            f"Payment received — {format_money(amount_minor, payment.currency)}",
            f"{payment.provider.title()} payment recorded."
            + (f" Balance: {format_money(invoice.balance_minor, invoice.currency)}"),
        )
    await db.flush()


async def _notify_paid(db: AsyncSession, invoice: Invoice, payment: InvoicePayment) -> None:
    """In-app bell + Web Push. Best-effort: never break the payment record."""
    try:
        from app.services.push_service import notify

        await notify(
            db,
            "invoice_paid",
            f"Invoice {invoice.number} paid",
            f"{format_money(payment.amount_minor, payment.currency)} from "
            f"{invoice.customer_name} via {payment.provider.title()}.",
            url=f"/invoices?invoice={invoice.id}",
            reference_id=invoice.id,
            reference_type="invoice",
            tag="invoice-paid",
        )
    except Exception as exc:  # noqa: BLE001 — notifications are optional
        logger.warning("Invoice paid notification failed: %s", exc)


async def mark_viewed(db: AsyncSession, invoice: Invoice) -> None:
    """First view of the public link: sent → viewed, with a single event."""
    now = datetime.now(timezone.utc)
    recently = invoice.viewed_at and (now - _as_utc(invoice.viewed_at)) < timedelta(hours=1)
    invoice.viewed_at = invoice.viewed_at or now
    if invoice.status == "sent":
        invoice.status = "viewed"
    if not recently:
        log_event(db, invoice, "viewed", "Invoice opened by the customer")
    await db.flush()


# ---------------------------------------------------------------------------
# Email delivery (best effort)
# ---------------------------------------------------------------------------

def invoice_email_html(invoice: Invoice, *, business: dict[str, Any]) -> str:
    symbol = currency_symbol(invoice.currency)
    rows = "".join(
        f"<tr><td style='padding:10px 0;border-bottom:1px solid #eceee9'>{html.escape(item.description)}"
        f"<div style='color:#7d877f;font-size:12px'>{item.quantity:g} × {symbol}{item.unit_price_minor/100:,.2f}</div></td>"
        f"<td align='right' style='padding:10px 0;border-bottom:1px solid #eceee9;white-space:nowrap'>"
        f"{symbol}{item.amount_minor/100:,.2f}</td></tr>"
        for item in invoice.items
    )
    link = invoice.payment_link or public_link(invoice.public_token)
    business_name = html.escape(business.get("business_name") or settings.APP_NAME)
    return f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f2f3f0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1c2620">
  <div style="max-width:620px;margin:0 auto;background:#ffffff;border-radius:20px;padding:32px">
    <div style="font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:#5f6962">{business_name}</div>
    <h1 style="font-size:26px;margin:12px 0 4px">Invoice {html.escape(invoice.number)}</h1>
    <p style="color:#5f6962;margin:0 0 20px">
      Issued {_as_utc(invoice.issue_date):%d %b %Y} · Due {_as_utc(invoice.due_date):%d %b %Y}
    </p>
    <p style="margin:0 0 18px">Hi {html.escape(invoice.customer_name)}, here is your invoice.</p>
    <table style="width:100%;border-collapse:collapse;font-size:14px">{rows}</table>
    <table style="width:100%;margin-top:18px;font-size:14px">
      <tr><td style="color:#5f6962">Subtotal</td><td align="right">{symbol}{invoice.subtotal_minor/100:,.2f}</td></tr>
      {f'<tr><td style="color:#5f6962">Discount</td><td align="right">−{symbol}{invoice.discount_minor/100:,.2f}</td></tr>' if invoice.discount_minor else ''}
      {f'<tr><td style="color:#5f6962">VAT ({invoice.tax_rate:g}%)</td><td align="right">{symbol}{invoice.tax_minor/100:,.2f}</td></tr>' if invoice.tax_minor else ''}
      <tr><td style="font-weight:700;padding-top:10px;font-size:17px">Total due</td>
          <td align="right" style="font-weight:700;padding-top:10px;font-size:17px">{format_money(invoice.total_minor, invoice.currency)}</td></tr>
    </table>
    <a href="{html.escape(link)}" style="display:block;text-align:center;margin:26px 0;background:#123c2b;color:#fff;text-decoration:none;padding:14px 18px;border-radius:12px;font-weight:600">
      View &amp; pay {format_money(invoice.balance_minor or invoice.total_minor, invoice.currency)}
    </a>
    <p style="color:#5f6962;font-size:13px;white-space:pre-line">{html.escape(invoice.terms or "")}</p>
    <p style="color:#7d877f;font-size:12px;margin-top:24px">{html.escape(business.get("business_address") or "")}<br>{html.escape(business.get("business_email") or "")} {html.escape(business.get("business_phone") or "")}</p>
  </div>
</body></html>"""


async def email_invoice(db: AsyncSession, invoice: Invoice, *, business: dict[str, Any]) -> dict[str, Any]:
    """Send the invoice to the customer through the configured Brevo sender.

    Returns ``{"sent": bool, "reason": str}``. It never raises: sending the mail
    is a convenience, the invoice and its link already exist.
    """
    address = (invoice.customer_email or "").strip()
    if "@" not in address:
        return {"sent": False, "reason": "This invoice has no customer email address."}
    try:
        from app.providers import brevo as brevo_provider
        from app.services import email_service

        account = await email_service.resolve_sender(db, None)
        if account is None:
            return {"sent": False, "reason": "No email sender is configured. The invoice is saved and its link is ready to share."}
        if int(getattr(account, "id", 0) or 0) < 0:
            return {"sent": False, "reason": "The default sender is a connected mailbox; invoice email needs a Brevo sender."}
        api_key = decrypt_value(getattr(account, "api_key_encrypted", "") or "")
        if not api_key:
            return {"sent": False, "reason": "The default email sender has no API key saved."}
        from_email, reply_to = await email_service.brevo_envelope(db, account)
        result = await brevo_provider.send_email(
            api_key,
            to_email=address,
            to_name=invoice.customer_name,
            subject=f"Invoice {invoice.number} from {business.get('business_name') or settings.APP_NAME}",
            html=invoice_email_html(invoice, business=business),
            text=(
                f"Invoice {invoice.number}\n"
                f"Total due: {format_money(invoice.balance_minor, invoice.currency)}\n"
                f"View and pay: {invoice.payment_link or public_link(invoice.public_token)}"
            ),
            from_email=from_email,
            from_name=business.get("business_name") or settings.APP_NAME,
            reply_to=reply_to or business.get("business_email") or None,
            tags=["invoice"],
            track_opens=True,
            track_clicks=True,
        )
        if not result.get("success"):
            return {"sent": False, "reason": result.get("error") or "Brevo rejected the invoice email."}
        return {"sent": True, "reason": "Invoice emailed to the customer."}
    except Exception as exc:  # noqa: BLE001 — never lose an invoice to an email problem
        logger.warning("Invoice email failed for %s: %s", invoice.number, exc)
        return {"sent": False, "reason": f"Could not email the invoice: {exc}"}
