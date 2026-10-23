"""Invoices API — create, edit, send, collect with Paystack, and analyse.

Design notes
------------
* **Admin routes** require the normal session (``get_current_user``), exactly
  like every other page in the app.
* **Public routes** (``/public/{token}`` and the Paystack webhook) do not. The
  token is unguessable and is the only thing the customer needs.
* Totals are always recomputed server-side from the line items. The client may
  send a total for display, but it is ignored.
* Every mutation writes an :class:`InvoiceEvent`, so the detail screen's
  "History" is a real audit trail rather than a reconstruction.
* Paystack initialisation is separate from "mark as paid". A payment only ever
  becomes real when the gateway confirms it (verify or webhook), or when the
  operator records cash/bank transfer by hand.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.database import get_db
from app.models.contact import Contact
from app.models.invoice import (
    INVOICE_CURRENCIES,
    Invoice,
    InvoiceEvent,
    InvoiceItem,
    InvoicePayment,
)
from app.models.user import User
from app.security.auth import get_current_user
from app.services import invoice_service as svc
from app.services.system_settings import get_setting
from app.utils.urls import public_base_url

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_ITEMS = 100


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ItemIn(BaseModel):
    description: str = Field(..., min_length=1, max_length=500)
    quantity: float = Field(default=1, gt=0, le=1_000_000)
    unit_price_minor: int = Field(default=0, ge=0)


class InvoiceIn(BaseModel):
    customer_name: str = Field(..., min_length=1, max_length=255)
    customer_email: Optional[str] = Field(default=None, max_length=320)
    customer_phone: Optional[str] = Field(default=None, max_length=40)
    customer_address: Optional[str] = None
    customer_business: Optional[str] = Field(default=None, max_length=255)
    contact_id: Optional[int] = None
    currency: str = "NGN"
    issue_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    items: list[ItemIn] = Field(default_factory=list)
    discount_minor: int = Field(default=0, ge=0)
    tax_rate: float = Field(default=0, ge=0, le=100)
    notes: Optional[str] = None
    terms: Optional[str] = None
    footer: Optional[str] = None
    #: Save as draft (default) or mark sent straight away.
    send: bool = False
    send_email: bool = True


class InvoicePatch(BaseModel):
    customer_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    customer_email: Optional[str] = Field(default=None, max_length=320)
    customer_phone: Optional[str] = Field(default=None, max_length=40)
    customer_address: Optional[str] = None
    customer_business: Optional[str] = Field(default=None, max_length=255)
    contact_id: Optional[int] = None
    currency: Optional[str] = None
    issue_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    items: Optional[list[ItemIn]] = None
    discount_minor: Optional[int] = Field(default=None, ge=0)
    tax_rate: Optional[float] = Field(default=None, ge=0, le=100)
    notes: Optional[str] = None
    terms: Optional[str] = None
    footer: Optional[str] = None
    status: Optional[str] = None


class PaymentIn(BaseModel):
    amount_minor: int = Field(..., gt=0)
    provider: str = "manual"
    reference: Optional[str] = Field(default=None, max_length=120)
    status: str = "success"
    note: Optional[str] = Field(default=None, max_length=500)
    paid_at: Optional[datetime] = None


class PayIn(BaseModel):
    email: Optional[str] = None


class SettingsIn(BaseModel):
    business_name: Optional[str] = None
    business_email: Optional[str] = None
    business_phone: Optional[str] = None
    business_address: Optional[str] = None
    business_website: Optional[str] = None
    business_logo_url: Optional[str] = None
    prefix: Optional[str] = None
    default_currency: Optional[str] = None
    default_tax_rate: Optional[float] = None
    default_terms: Optional[str] = None
    default_notes: Optional[str] = None
    footer: Optional[str] = None
    paystack_public_key: Optional[str] = None
    #: Absent = keep the saved secret. "" = clear it. A value replaces it.
    paystack_secret_key: Optional[str] = None
    paystack_channels: Optional[list[str]] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _get_invoice(db: AsyncSession, invoice_id: int) -> Invoice:
    invoice = (
        await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    ).scalar_one_or_none()
    if invoice is None:
        raise HTTPException(404, "Invoice not found")
    return invoice


def _clean_email(value: str | None) -> str | None:
    email = (value or "").strip()
    if not email:
        return None
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(422, "Enter a valid customer email address")
    return email[:320]


def _apply_totals(invoice: Invoice, totals: dict[str, Any]) -> None:
    invoice.subtotal_minor = totals["subtotal_minor"]
    invoice.discount_minor = totals["discount_minor"]
    invoice.tax_rate = totals["tax_rate"]
    invoice.tax_minor = totals["tax_minor"]
    invoice.total_minor = totals["total_minor"]


def _status_clause(status: str):
    now = datetime.now(timezone.utc)
    unpaid = (Invoice.amount_paid_minor < Invoice.total_minor) & (
        Invoice.status.notin_(("draft", "void"))
    )
    paid = (Invoice.amount_paid_minor >= Invoice.total_minor) & (Invoice.total_minor > 0)
    overdue = unpaid & (Invoice.due_date < now)
    partial = unpaid & (Invoice.amount_paid_minor > 0) & (Invoice.due_date >= now)
    if status == "paid":
        return paid | (Invoice.status == "paid")
    if status == "overdue":
        return overdue
    if status == "partial":
        return partial
    if status in ("draft", "void"):
        return Invoice.status == status
    if status in ("sent", "viewed"):
        return (Invoice.status == status) & ~paid
    return None


def _summary(invoices: list[Invoice]) -> dict[str, Any]:
    """Money summary for a set of invoices (already currency-filtered)."""
    total = paid = outstanding = overdue = draft = void = 0
    counts = {"draft": 0, "sent": 0, "viewed": 0, "partial": 0, "paid": 0, "overdue": 0, "void": 0}
    for invoice in invoices:
        status = svc.effective_status(invoice)
        counts[status] = counts.get(status, 0) + 1
        amount = int(invoice.total_minor or 0)
        amount_paid = int(invoice.amount_paid_minor or 0)
        if status == "void":
            void += 1
            continue
        if status == "draft":
            draft += amount
            continue
        total += amount
        paid += min(amount_paid, amount)
        outstanding += max(0, amount - amount_paid)
        if status == "overdue":
            overdue += max(0, amount - amount_paid)
    return {
        "invoiced_minor": total,
        "paid_minor": paid,
        "outstanding_minor": outstanding,
        "overdue_minor": overdue,
        "draft_minor": draft,
        "invoice_count": len(invoices),
        "paid_count": counts.get("paid", 0),
        "overdue_count": counts.get("overdue", 0),
        "draft_count": counts.get("draft", 0),
        "partial_count": counts.get("partial", 0),
        "average_invoice_minor": int(round(total / max(1, len([i for i in invoices if svc.effective_status(i) not in ("draft", "void")])))),
        "by_status": counts,
    }


async def _currency_default(db: AsyncSession) -> str:
    cfg = await svc.get_invoice_settings(db)
    return cfg.get("default_currency") or "NGN"


def _mailto(invoice: Invoice, business: dict[str, Any]) -> str | None:
    if not invoice.customer_email:
        return None
    from urllib.parse import quote

    subject = quote(f"Invoice {invoice.number} from {business.get('business_name') or 'us'}")
    body = quote(
        f"Hi {invoice.customer_name},\n\n"
        f"Here is invoice {invoice.number} for {svc.format_money(invoice.balance_minor, invoice.currency)}.\n"
        f"View and pay: {invoice.payment_link or svc.public_link(invoice.public_token)}\n\n"
        f"Thank you."
    )
    return f"mailto:{invoice.customer_email}?subject={subject}&body={body}"


# ---------------------------------------------------------------------------
# Settings (must precede /{invoice_id})
# ---------------------------------------------------------------------------

@router.get("/settings")
async def read_settings(db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    cfg = await svc.get_invoice_settings(db)
    return svc.serialize_settings(cfg)


@router.put("/settings")
async def write_settings(
    body: SettingsIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    cfg = await svc.save_invoice_settings(db, body.model_dump(exclude_unset=True))
    return svc.serialize_settings(cfg)


@router.post("/settings/test")
async def test_paystack(db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)):
    """Prove the saved secret key works, and show the balance Paystack reports."""
    cfg = await svc.get_invoice_settings(db)
    ok, reason = svc.paystack_ready(cfg)
    if not ok:
        return {"success": False, "error": reason}
    try:
        balances = await svc.paystack_balance(cfg["paystack_secret_key"])
    except svc.PaystackError as exc:
        return {"success": False, "error": str(exc)}
    return {
        "success": True,
        "message": "Paystack connection is working.",
        "balances": [
            {
                "currency": str(b.get("currency") or "").upper(),
                "balance_minor": int(b.get("balance") or 0),
            }
            for b in balances
            if isinstance(b, dict)
        ],
    }


@router.get("/currencies")
async def list_currencies(
    db: AsyncSession = Depends(get_db), cu: User = Depends(get_current_user)
):
    cfg = await svc.get_invoice_settings(db)
    return {
        "currencies": [
            {"code": code, "symbol": svc.currency_symbol(code), "label": code}
            for code in INVOICE_CURRENCIES
        ],
        "default": cfg.get("default_currency") or "NGN",
    }


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@router.get("/analytics")
async def analytics(
    currency: Optional[str] = Query(None),
    days: int = Query(365, ge=30, le=1825),
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    code = (currency or await _currency_default(db)).upper()
    if code not in INVOICE_CURRENCIES:
        raise HTTPException(422, "Unsupported currency")

    invoices = (
        await db.execute(
            select(Invoice)
            .where(Invoice.currency == code)
            .options(noload(Invoice.items), noload(Invoice.payments), noload(Invoice.events))
            .order_by(Invoice.id.desc())
        )
    ).scalars().all()
    summary = _summary(list(invoices))

    # Monthly buckets: invoiced (by issue date) and collected (by payment date).
    now = datetime.now(timezone.utc)
    months: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for offset in range(11, -1, -1):
        year = now.year
        month = now.month - offset
        while month <= 0:
            month += 12
            year -= 1
        key = f"{year:04d}-{month:02d}"
        bucket = {
            "month": key,
            "label": datetime(year, month, 1).strftime("%b"),
            "invoiced_minor": 0,
            "collected_minor": 0,
        }
        months.append(bucket)
        index[key] = bucket

    for invoice in invoices:
        issued = svc._as_utc(invoice.issue_date)
        if issued and svc.effective_status(invoice) != "void":
            key = issued.strftime("%Y-%m")
            if key in index:
                index[key]["invoiced_minor"] += int(invoice.total_minor or 0)
        paid = svc._as_utc(invoice.paid_at)
        if paid:
            key = paid.strftime("%Y-%m")
            if key in index:
                index[key]["collected_minor"] += int(invoice.amount_paid_minor or 0)

    payments = (
        await db.execute(
            select(InvoicePayment, Invoice)
            .join(Invoice, Invoice.id == InvoicePayment.invoice_id)
            .where(
                InvoicePayment.status == "success",
                InvoicePayment.currency == code,
                InvoicePayment.paid_at >= now - timedelta(days=days),
            )
            .order_by(InvoicePayment.paid_at.desc(), InvoicePayment.id.desc())
            .limit(12)
        )
    ).all()
    recent_payments = [
        {
            "id": payment.id,
            "invoice_id": invoice.id,
            "invoice_number": invoice.number,
            "customer_name": invoice.customer_name,
            "amount_minor": payment.amount_minor,
            "currency": payment.currency,
            "provider": payment.provider,
            "channel": payment.channel,
            "paid_at": svc._iso(payment.paid_at),
        }
        for payment, invoice in payments
    ]

    customers: dict[str, dict[str, Any]] = {}
    for invoice in invoices:
        if svc.effective_status(invoice) in ("draft", "void"):
            continue
        bucket = customers.setdefault(
            invoice.customer_name,
            {"customer_name": invoice.customer_name, "invoiced_minor": 0, "paid_minor": 0, "invoices": 0},
        )
        bucket["invoiced_minor"] += int(invoice.total_minor or 0)
        bucket["paid_minor"] += min(int(invoice.amount_paid_minor or 0), int(invoice.total_minor or 0))
        bucket["invoices"] += 1
    top_customers = sorted(customers.values(), key=lambda c: c["paid_minor"], reverse=True)[:6]

    due_soon = 0
    for invoice in invoices:
        if svc.effective_status(invoice) in ("draft", "void", "paid"):
            continue
        due = svc._as_utc(invoice.due_date)
        if due and now <= due <= now + timedelta(days=14):
            due_soon += max(0, int(invoice.total_minor or 0) - int(invoice.amount_paid_minor or 0))

    return {
        "currency": code,
        "summary": summary,
        "monthly": months,
        "recent_payments": recent_payments,
        "top_customers": top_customers,
        "due_in_14_days_minor": due_soon,
        "generated_at": now.isoformat(),
    }


@router.get("/payments")
async def all_payments(
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    currency: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    status: Optional[str] = Query("success"),
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Every money movement, across every invoice — the Payments history tab."""
    query = (
        select(InvoicePayment, Invoice)
        .join(Invoice, Invoice.id == InvoicePayment.invoice_id)
        .where(InvoicePayment.status == (status or "success"))
    )
    if currency:
        query = query.where(InvoicePayment.currency == currency.upper())
    if provider:
        query = query.where(InvoicePayment.provider == provider)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    rows = (
        await db.execute(
            query.order_by(InvoicePayment.paid_at.desc(), InvoicePayment.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).all()
    items = [
        {
            **svc._payment_out(payment),
            "invoice_id": invoice.id,
            "invoice_number": invoice.number,
            "customer_name": invoice.customer_name,
            "invoice_status": svc.effective_status(invoice),
        }
        for payment, invoice in rows
    ]
    totals = (
        await db.execute(
            select(func.coalesce(func.sum(InvoicePayment.amount_minor), 0)).where(
                InvoicePayment.status == "success",
                *( [InvoicePayment.currency == currency.upper()] if currency else [] ),
            )
        )
    ).scalar() or 0
    by_provider = (
        await db.execute(
            select(InvoicePayment.provider, func.sum(InvoicePayment.amount_minor))
            .where(
                InvoicePayment.status == "success",
                *( [InvoicePayment.currency == currency.upper()] if currency else [] ),
            )
            .group_by(InvoicePayment.provider)
        )
    ).all()
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": items,
        "total_collected_minor": int(totals),
        "by_provider": [
            {"provider": provider_name, "amount_minor": int(amount or 0)}
            for provider_name, amount in by_provider
        ],
    }


# ---------------------------------------------------------------------------
# Public invoice + Paystack webhook (no session)
# ---------------------------------------------------------------------------

@router.post("/paystack/webhook")
async def paystack_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Paystack's server-to-server confirmation.

    The signature is verified against the *raw* request body using the saved
    secret key. A request without a valid signature is rejected with 401 and
    changes nothing, so an attacker cannot mark invoices paid by guessing URLs.
    """
    raw = await request.body()
    cfg = await svc.get_invoice_settings(db)
    secret = cfg.get("paystack_secret_key") or ""
    if not secret:
        raise HTTPException(503, "Paystack is not configured on this deployment")
    signature = request.headers.get("x-paystack-signature")
    if not svc.verify_webhook_signature(raw, signature, secret):
        raise HTTPException(401, "Invalid Paystack signature")

    try:
        event = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")
    event_type = str(event.get("event") or "")
    data = event.get("data") or {}
    reference = str(data.get("reference") or "")

    payment = None
    invoice = None
    if reference:
        row = (
            await db.execute(
                select(InvoicePayment).where(
                    InvoicePayment.provider == "paystack",
                    InvoicePayment.reference == reference,
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            payment = row
            invoice = (
                await db.execute(select(Invoice).where(Invoice.id == row.invoice_id))
            ).scalar_one_or_none()
    if invoice is None:
        meta = data.get("metadata") or {}
        invoice_id = meta.get("invoice_id") if isinstance(meta, dict) else None
        token = meta.get("invoice_token") if isinstance(meta, dict) else None
        if invoice_id:
            invoice = (
                await db.execute(select(Invoice).where(Invoice.id == int(invoice_id)))
            ).scalar_one_or_none()
        if invoice is None and token:
            invoice = (
                await db.execute(select(Invoice).where(Invoice.public_token == str(token)))
            ).scalar_one_or_none()

    if invoice is None:
        logger.warning("Paystack webhook %s for unknown reference %s", event_type, reference)
        return {"status": "ignored"}

    if event_type == "charge.success" and str(data.get("status")) == "success":
        paid_at = None
        if data.get("paid_at"):
            try:
                paid_at = datetime.fromisoformat(str(data["paid_at"]).replace("Z", "+00:00"))
            except ValueError:
                paid_at = None
        currency = str(data.get("currency") or invoice.currency).upper()
        if currency and currency != invoice.currency:
            logger.warning(
                "Paystack currency mismatch on %s: %s vs %s",
                invoice.number, currency, invoice.currency,
            )
            return {"status": "currency_mismatch"}
        payment, _created = await svc.record_payment(
            db,
            invoice,
            provider="paystack",
            reference=reference,
            amount_minor=int(data.get("amount") or 0),
            currency=currency,
            status="success",
            channel=data.get("channel"),
            gateway_id=str(data.get("id") or "") or None,
            payer_email=(data.get("customer") or {}).get("email"),
            paid_at=paid_at,
            raw=data,
        )
        invoice.status = invoice.status if invoice.status not in ("draft",) else "sent"
        await db.flush()
        return {"status": "ok"}
    if event_type in ("charge.failed", "transfer.failed"):
        if payment is not None:
            payment.status = "failed"
            payment.raw_json = data
            await db.flush()
        return {"status": "ok"}
    return {"status": "ignored"}


async def _public_invoice(db: AsyncSession, token: str) -> Invoice:
    invoice = (
        await db.execute(select(Invoice).where(Invoice.public_token == token))
    ).scalar_one_or_none()
    if invoice is None:
        raise HTTPException(404, "This invoice link is not valid")
    return invoice


@router.get("/public/{token}")
async def public_invoice(token: str, db: AsyncSession = Depends(get_db)):
    invoice = await _public_invoice(db, token)
    cfg = await svc.get_invoice_settings(db)
    await svc.mark_viewed(db, invoice)
    return {
        **svc.serialize_invoice(invoice, public=True, business=cfg),
        "paystack": {
            "configured": bool(cfg.get("paystack_public_key") and cfg.get("paystack_secret_key")),
            "public_key": cfg.get("paystack_public_key") or "",
        },
    }


@router.post("/public/{token}/pay")
async def public_pay(
    token: str,
    body: PayIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Initialise a Paystack checkout for the public invoice page."""
    invoice = await _public_invoice(db, token)
    status = svc.effective_status(invoice)
    if status == "void":
        raise HTTPException(400, "This invoice has been voided.")
    if status == "paid" or invoice.balance_minor <= 0:
        raise HTTPException(400, "This invoice is already paid in full.")
    cfg = await svc.get_invoice_settings(db)
    ok, reason = svc.paystack_ready(cfg)
    if not ok:
        raise HTTPException(400, reason)
    email = (body.email or invoice.customer_email or "").strip()
    if "@" not in email:
        raise HTTPException(422, "Enter a valid email address for your receipt")
    reference = f"INV-{invoice.id}-{uuid4().hex[:10].upper()}"
    base = (public_base_url() or "").rstrip("/")
    callback = f"{base}/i/{invoice.public_token}?reference={reference}" if base else None
    try:
        result = await svc.initialize_transaction(
            cfg["paystack_secret_key"],
            email=email,
            amount_minor=invoice.balance_minor,
            currency=invoice.currency,
            reference=reference,
            callback_url=callback,
            metadata={
                "invoice_id": invoice.id,
                "invoice_number": invoice.number,
                "invoice_token": invoice.public_token,
                "customer_name": invoice.customer_name,
            },
            channels=cfg.get("paystack_channels") or None,
        )
    except svc.PaystackError as exc:
        raise HTTPException(502, str(exc))
    invoice.paystack_reference = reference
    invoice.payment_link = invoice.payment_link or svc.public_link(invoice.public_token)
    await svc.record_payment(
        db,
        invoice,
        provider="paystack",
        reference=reference,
        amount_minor=invoice.balance_minor,
        currency=invoice.currency,
        status="pending",
        payer_email=email,
    )
    svc.log_event(
        db, invoice, "payment_started", "Customer started a Paystack checkout",
        f"Reference {reference}",
    )
    await db.flush()
    return {
        "success": True,
        "authorization_url": result["authorization_url"],
        "access_code": result.get("access_code"),
        "reference": result["reference"],
    }


@router.get("/public/{token}/verify")
async def public_verify(
    token: str,
    reference: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Confirm a payment when Paystack sends the customer back.

    The webhook is the authoritative confirmation; this endpoint exists so the
    page can show "Paid" instantly, and is safe to call repeatedly because
    :func:`record_payment` is idempotent.
    """
    invoice = await _public_invoice(db, token)
    cfg = await svc.get_invoice_settings(db)
    ok, reason = svc.paystack_ready(cfg)
    if not ok:
        raise HTTPException(400, reason)
    try:
        data = await svc.verify_transaction(cfg["paystack_secret_key"], reference)
    except svc.PaystackError as exc:
        raise HTTPException(502, str(exc))
    if str(data.get("status")) == "success":
        paid_at = None
        if data.get("paid_at"):
            try:
                paid_at = datetime.fromisoformat(str(data["paid_at"]).replace("Z", "+00:00"))
            except ValueError:
                paid_at = None
        await svc.record_payment(
            db,
            invoice,
            provider="paystack",
            reference=reference,
            amount_minor=int(data.get("amount") or 0),
            currency=str(data.get("currency") or invoice.currency).upper(),
            status="success",
            channel=data.get("channel"),
            gateway_id=str(data.get("id") or "") or None,
            payer_email=(data.get("customer") or {}).get("email"),
            paid_at=paid_at,
            raw=data,
        )
        await db.flush()
    return {
        "success": str(data.get("status")) == "success",
        "gateway_status": data.get("status"),
        "invoice": svc.serialize_invoice(invoice, public=True, business=cfg),
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.get("")
async def list_invoices(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    currency: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    contact_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    query = select(Invoice)
    if status:
        clause = _status_clause(status)
        if clause is None and status not in (
            "draft", "sent", "viewed", "partial", "paid", "overdue", "void"
        ):
            raise HTTPException(422, "Unknown invoice status")
        if clause is not None:
            query = query.where(clause)
    if currency:
        code = currency.upper()
        if code not in INVOICE_CURRENCIES:
            raise HTTPException(422, "Unsupported currency")
        query = query.where(Invoice.currency == code)
    if contact_id:
        query = query.where(Invoice.contact_id == contact_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(
            or_(
                Invoice.number.ilike(like),
                Invoice.customer_name.ilike(like),
                Invoice.customer_email.ilike(like),
                Invoice.customer_phone.ilike(like),
                Invoice.customer_business.ilike(like),
            )
        )
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    rows = (
        await db.execute(
            query.order_by(Invoice.created_at.desc(), Invoice.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).scalars().all()

    # Money summary for the filter's currency. "All currencies" cannot be
    # summed meaningfully, so the panel simply hides it when none is chosen.
    summary = None
    if currency:
        all_rows = (
            await db.execute(
                select(Invoice)
                .where(Invoice.currency == currency.upper())
                .options(noload(Invoice.items), noload(Invoice.payments), noload(Invoice.events))
            )
        ).scalars().all()
        summary = _summary(list(all_rows))
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [svc.serialize_invoice(invoice) for invoice in rows],
        "summary": summary,
    }


@router.post("", status_code=201)
async def create_invoice(
    body: InvoiceIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    cfg = await svc.get_invoice_settings(db)
    currency = (body.currency or cfg["default_currency"]).upper()
    if currency not in INVOICE_CURRENCIES:
        raise HTTPException(422, f"Currency must be one of {', '.join(INVOICE_CURRENCIES)}")
    items = body.items or []
    if len(items) > MAX_ITEMS:
        raise HTTPException(422, f"An invoice can have at most {MAX_ITEMS} lines")
    if not items:
        raise HTTPException(422, "Add at least one line item")
    totals = svc.calculate_totals(
        [item.model_dump() for item in items],
        discount_minor=body.discount_minor,
        tax_rate=body.tax_rate,
    )
    if totals["total_minor"] <= 0:
        raise HTTPException(422, "An invoice total must be greater than zero")

    issue = _utc(body.issue_date) or datetime.now(timezone.utc)
    due = _utc(body.due_date) or (issue + timedelta(days=14))
    if due < issue:
        raise HTTPException(422, "The due date cannot be before the issue date")

    contact_id = body.contact_id
    if contact_id:
        exists = (
            await db.execute(select(Contact.id).where(Contact.id == contact_id))
        ).scalar_one_or_none()
        if exists is None:
            contact_id = None

    number = await svc.next_invoice_number(db, cfg.get("prefix"))
    invoice = Invoice(
        number=number,
        contact_id=contact_id,
        customer_name=body.customer_name.strip(),
        customer_email=_clean_email(body.customer_email),
        customer_phone=(body.customer_phone or "").strip() or None,
        customer_address=(body.customer_address or "").strip() or None,
        customer_business=(body.customer_business or "").strip() or None,
        currency=currency,
        issue_date=issue,
        due_date=due,
        discount_minor=totals["discount_minor"],
        tax_rate=totals["tax_rate"],
        notes=body.notes or cfg.get("default_notes") or None,
        terms=body.terms or cfg.get("default_terms") or None,
        footer=body.footer or cfg.get("footer") or None,
        status="draft",
        created_by=(cu.display_name or cu.username or "admin")[:150],
    )
    _apply_totals(invoice, totals)
    # Append the children while the parent is still pending: touching an
    # unloaded collection after flush would emit a synchronous lazy SELECT from
    # async code. Appending first also means the response carries the items.
    for item in totals["items"]:
        invoice.items.append(InvoiceItem(**item))
    svc.log_event(db, invoice, "created", f"Invoice {invoice.number} created")
    db.add(invoice)
    await db.flush()
    invoice.payment_link = svc.public_link(invoice.public_token)
    await db.flush()

    result: dict[str, Any] = {
        "success": True,
        "invoice": svc.serialize_invoice(invoice),
        "message": "Invoice saved as a draft.",
    }
    if body.send:
        send_result = await _send_invoice(db, invoice, cfg, email_customer=body.send_email)
        result["invoice"] = svc.serialize_invoice(invoice)
        result.update(send_result)
    return result


async def _send_invoice(
    db: AsyncSession,
    invoice: Invoice,
    cfg: dict[str, Any],
    *,
    email_customer: bool = True,
) -> dict[str, Any]:
    """Shared by create(send), POST /{id}/send and reminders."""
    if svc.effective_status(invoice) == "void":
        raise HTTPException(400, "A void invoice cannot be sent")
    invoice.payment_link = invoice.payment_link or svc.public_link(invoice.public_token)
    invoice.status = "sent" if invoice.status in ("draft", "viewed") else invoice.status
    invoice.sent_at = invoice.sent_at or datetime.now(timezone.utc)
    email_result = (
        await svc.email_invoice(db, invoice, business=cfg)
        if email_customer
        else {"sent": False, "reason": "Email delivery skipped."}
    )
    svc.log_event(
        db,
        invoice,
        "email_sent" if email_result.get("sent") else "sent",
        f"Invoice {invoice.number} shared with the customer"
        if not email_result.get("sent")
        else f"Invoice {invoice.number} emailed to {invoice.customer_email}",
        email_result.get("reason"),
    )
    await db.flush()
    return {
        "email_sent": bool(email_result.get("sent")),
        "email_message": email_result.get("reason"),
        "mailto_url": _mailto(invoice, cfg),
        "public_url": invoice.payment_link,
        "message": (
            "Invoice emailed to the customer."
            if email_result.get("sent")
            else "Invoice marked as sent. Share the payment link or your mail app opened with it ready."
        ),
    }


@router.get("/{invoice_id}")
async def get_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    invoice = await _get_invoice(db, invoice_id)
    return svc.serialize_invoice(invoice)


@router.patch("/{invoice_id}")
async def update_invoice(
    invoice_id: int,
    body: InvoicePatch,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    invoice = await _get_invoice(db, invoice_id)
    data = body.model_dump(exclude_unset=True)
    if not data:
        return svc.serialize_invoice(invoice)

    if "customer_name" in data and data["customer_name"]:
        invoice.customer_name = str(data["customer_name"]).strip()[:255]
    if "customer_email" in data:
        invoice.customer_email = _clean_email(data["customer_email"])
    if "customer_phone" in data:
        invoice.customer_phone = (data["customer_phone"] or "").strip() or None
    if "customer_address" in data:
        invoice.customer_address = (data["customer_address"] or "").strip() or None
    if "customer_business" in data:
        invoice.customer_business = (data["customer_business"] or "").strip() or None
    if "contact_id" in data:
        invoice.contact_id = data["contact_id"] or None
    if "notes" in data:
        invoice.notes = data["notes"]
    if "terms" in data:
        invoice.terms = data["terms"]
    if "footer" in data:
        invoice.footer = data["footer"]

    if "currency" in data and data["currency"]:
        currency = str(data["currency"]).upper()
        if currency not in INVOICE_CURRENCIES:
            raise HTTPException(422, "Unsupported currency")
        if int(invoice.amount_paid_minor or 0) > 0 and currency != invoice.currency:
            raise HTTPException(400, "The currency cannot change after a payment has been recorded")
        invoice.currency = currency

    issue = _utc(data.get("issue_date")) if data.get("issue_date") else invoice.issue_date
    due = _utc(data.get("due_date")) if data.get("due_date") else invoice.due_date
    if issue and due and due < issue:
        raise HTTPException(422, "The due date cannot be before the issue date")
    if issue:
        invoice.issue_date = issue
    if due:
        invoice.due_date = due

    if "items" in data and data["items"] is not None:
        items = data["items"]
        if not items:
            raise HTTPException(422, "An invoice needs at least one line item")
        if len(items) > MAX_ITEMS:
            raise HTTPException(422, f"An invoice can have at most {MAX_ITEMS} lines")
        discount = data.get("discount_minor", invoice.discount_minor)
        rate = data.get("tax_rate", invoice.tax_rate)
        totals = svc.calculate_totals(
            [item.model_dump() if hasattr(item, "model_dump") else item for item in items],
            discount_minor=discount,
            tax_rate=rate,
        )
        if totals["total_minor"] <= 0:
            raise HTTPException(422, "An invoice total must be greater than zero")
        invoice.items.clear()
        for item in totals["items"]:
            invoice.items.append(InvoiceItem(**item))
        _apply_totals(invoice, totals)
        await db.flush()
    elif "discount_minor" in data or "tax_rate" in data:
        current_items = [
            {
                "description": item.description,
                "quantity": item.quantity,
                "unit_price_minor": item.unit_price_minor,
            }
            for item in invoice.items
        ]
        totals = svc.calculate_totals(
            current_items,
            discount_minor=data.get("discount_minor", invoice.discount_minor),
            tax_rate=data.get("tax_rate", invoice.tax_rate),
        )
        _apply_totals(invoice, totals)

    if "status" in data and data["status"]:
        wanted = str(data["status"]).lower()
        if wanted not in ("draft", "sent", "void", "paid"):
            raise HTTPException(422, "Status must be draft, sent, void or paid")
        if wanted == "paid" and invoice.balance_minor > 0:
            raise HTTPException(
                400, "Record a payment to mark this invoice paid — status alone must not create money."
            )
        if wanted == "void" and svc.effective_status(invoice) == "paid":
            raise HTTPException(400, "A paid invoice cannot be voided. Record a refund instead.")
        invoice.status = wanted
        if wanted == "void":
            svc.log_event(db, invoice, "voided", f"Invoice {invoice.number} voided")
        elif wanted == "paid":
            invoice.paid_at = invoice.paid_at or datetime.now(timezone.utc)
    await svc._recalculate_paid(db, invoice)
    svc.log_event(db, invoice, "updated", f"Invoice {invoice.number} updated")
    await db.flush()
    return svc.serialize_invoice(invoice)


@router.delete("/{invoice_id}")
async def delete_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    invoice = await _get_invoice(db, invoice_id)
    number = invoice.number
    await db.delete(invoice)
    await db.flush()
    return {"success": True, "deleted": invoice_id, "number": number}


@router.post("/{invoice_id}/duplicate", status_code=201)
async def duplicate_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    invoice = await _get_invoice(db, invoice_id)
    cfg = await svc.get_invoice_settings(db)
    number = await svc.next_invoice_number(db, cfg.get("prefix"))
    issue = datetime.now(timezone.utc)
    copy = Invoice(
        number=number,
        contact_id=invoice.contact_id,
        customer_name=invoice.customer_name,
        customer_email=invoice.customer_email,
        customer_phone=invoice.customer_phone,
        customer_address=invoice.customer_address,
        customer_business=invoice.customer_business,
        currency=invoice.currency,
        issue_date=issue,
        due_date=issue + timedelta(days=14),
        discount_minor=invoice.discount_minor,
        tax_rate=invoice.tax_rate,
        notes=invoice.notes,
        terms=invoice.terms,
        footer=invoice.footer,
        status="draft",
        created_by=(cu.display_name or cu.username or "admin")[:150],
    )
    _apply_totals(
        copy,
        {
            "subtotal_minor": invoice.subtotal_minor,
            "discount_minor": invoice.discount_minor,
            "tax_rate": invoice.tax_rate,
            "tax_minor": invoice.tax_minor,
            "total_minor": invoice.total_minor,
        },
    )
    for item in invoice.items:
        copy.items.append(
            InvoiceItem(
                description=item.description,
                quantity=item.quantity,
                unit_price_minor=item.unit_price_minor,
                amount_minor=item.amount_minor,
                position=item.position,
            )
        )
    svc.log_event(db, copy, "duplicated", f"Created from invoice {invoice.number}")
    db.add(copy)
    await db.flush()
    copy.payment_link = svc.public_link(copy.public_token)
    await db.flush()
    return {"success": True, "invoice": svc.serialize_invoice(copy)}


@router.post("/{invoice_id}/send")
async def send_invoice(
    invoice_id: int,
    email_customer: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    invoice = await _get_invoice(db, invoice_id)
    cfg = await svc.get_invoice_settings(db)
    result = await _send_invoice(db, invoice, cfg, email_customer=email_customer)
    return {"success": True, **result, "invoice": svc.serialize_invoice(invoice)}


@router.post("/{invoice_id}/remind")
async def remind_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    invoice = await _get_invoice(db, invoice_id)
    status = svc.effective_status(invoice)
    if status == "paid":
        raise HTTPException(400, "This invoice is already paid")
    if status == "void":
        raise HTTPException(400, "A void invoice cannot be reminded")
    cfg = await svc.get_invoice_settings(db)
    email_result = await svc.email_invoice(db, invoice, business=cfg)
    invoice.reminders_sent = int(invoice.reminders_sent or 0) + 1
    invoice.last_reminder_at = datetime.now(timezone.utc)
    svc.log_event(
        db,
        invoice,
        "reminder",
        f"Payment reminder #{invoice.reminders_sent} sent"
        if email_result.get("sent")
        else f"Payment reminder #{invoice.reminders_sent} prepared",
        email_result.get("reason"),
    )
    await db.flush()
    return {
        "success": True,
        "email_sent": bool(email_result.get("sent")),
        "message": email_result.get("reason"),
        "mailto_url": _mailto(invoice, cfg),
        "invoice": svc.serialize_invoice(invoice),
    }


@router.post("/{invoice_id}/void")
async def void_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    invoice = await _get_invoice(db, invoice_id)
    if svc.effective_status(invoice) == "paid":
        raise HTTPException(400, "A paid invoice cannot be voided. Record a refund instead.")
    invoice.status = "void"
    svc.log_event(db, invoice, "voided", f"Invoice {invoice.number} voided")
    await db.flush()
    return {"success": True, "invoice": svc.serialize_invoice(invoice)}


@router.post("/{invoice_id}/pay")
async def pay_invoice(
    invoice_id: int,
    body: PayIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Create a Paystack checkout link for the outstanding balance."""
    invoice = await _get_invoice(db, invoice_id)
    status = svc.effective_status(invoice)
    if status == "void":
        raise HTTPException(400, "This invoice has been voided")
    if status == "paid" or invoice.balance_minor <= 0:
        raise HTTPException(400, "This invoice is already paid in full")
    cfg = await svc.get_invoice_settings(db)
    ok, reason = svc.paystack_ready(cfg)
    if not ok:
        raise HTTPException(400, reason)
    email = (body.email or invoice.customer_email or "").strip()
    if "@" not in email:
        raise HTTPException(422, "Add the customer's email address before collecting payment")
    reference = f"INV-{invoice.id}-{uuid4().hex[:10].upper()}"
    base = (public_base_url() or "").rstrip("/")
    callback = f"{base}/i/{invoice.public_token}?reference={reference}" if base else None
    try:
        result = await svc.initialize_transaction(
            cfg["paystack_secret_key"],
            email=email,
            amount_minor=invoice.balance_minor,
            currency=invoice.currency,
            reference=reference,
            callback_url=callback,
            metadata={
                "invoice_id": invoice.id,
                "invoice_number": invoice.number,
                "invoice_token": invoice.public_token,
                "customer_name": invoice.customer_name,
            },
            channels=cfg.get("paystack_channels") or None,
        )
    except svc.PaystackError as exc:
        raise HTTPException(502, str(exc))
    invoice.paystack_reference = reference
    invoice.payment_link = invoice.payment_link or svc.public_link(invoice.public_token)
    await svc.record_payment(
        db,
        invoice,
        provider="paystack",
        reference=reference,
        amount_minor=invoice.balance_minor,
        currency=invoice.currency,
        status="pending",
        payer_email=email,
    )
    svc.log_event(
        db,
        invoice,
        "payment_started",
        "Paystack checkout created",
        f"Reference {reference}",
    )
    await db.flush()
    return {
        "success": True,
        "authorization_url": result["authorization_url"],
        "access_code": result.get("access_code"),
        "reference": result["reference"],
        "invoice": svc.serialize_invoice(invoice),
    }


@router.post("/{invoice_id}/payments", status_code=201)
async def record_manual_payment(
    invoice_id: int,
    body: PaymentIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    invoice = await _get_invoice(db, invoice_id)
    if svc.effective_status(invoice) == "void":
        raise HTTPException(400, "A void invoice cannot receive payments")
    if svc.effective_status(invoice) == "paid":
        raise HTTPException(400, "This invoice is already paid in full")
    if body.amount_minor > invoice.balance_minor:
        raise HTTPException(
            422,
            f"The balance is {svc.format_money(invoice.balance_minor, invoice.currency)}; "
            "a payment cannot be larger than the balance.",
        )
    provider = body.provider if body.provider in (
        "paystack", "manual", "bank_transfer", "cash", "other"
    ) else "manual"
    if body.status not in ("pending", "success", "failed", "reversed"):
        raise HTTPException(422, "Invalid payment status")
    payment, _created = await svc.record_payment(
        db,
        invoice,
        provider=provider,
        reference=body.reference or f"MAN-{uuid4().hex[:12].upper()}",
        amount_minor=body.amount_minor,
        currency=invoice.currency,
        status=body.status,
        channel=provider,
        note=body.note,
        paid_at=_utc(body.paid_at) if body.paid_at else None,
    )
    if body.status == "success":
        svc.log_event(
            db,
            invoice,
            "payment_recorded",
            f"{svc.format_money(body.amount_minor, invoice.currency)} recorded via {provider}",
            body.note,
        )
    await db.flush()
    return {
        "success": True,
        "payment": svc._payment_out(payment),
        "invoice": svc.serialize_invoice(invoice),
    }


@router.get("/{invoice_id}/payments")
async def list_payments(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    invoice = await _get_invoice(db, invoice_id)
    return {"items": [svc._payment_out(p) for p in invoice.payments]}


@router.get("/{invoice_id}/events")
async def list_events(
    invoice_id: int,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    invoice = await _get_invoice(db, invoice_id)
    events = (
        await db.execute(
            select(InvoiceEvent)
            .where(InvoiceEvent.invoice_id == invoice.id)
            .order_by(InvoiceEvent.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return {"items": [svc._event_out(e) for e in events]}


@router.post("/{invoice_id}/rotate-link")
async def rotate_public_link(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Revoke every copy of the old link and issue a new one."""
    invoice = await _get_invoice(db, invoice_id)
    invoice.public_token = uuid4().hex
    invoice.payment_link = svc.public_link(invoice.public_token)
    svc.log_event(db, invoice, "updated", "Public payment link rotated")
    await db.flush()
    return {"success": True, "public_url": invoice.payment_link, "invoice": svc.serialize_invoice(invoice)}
