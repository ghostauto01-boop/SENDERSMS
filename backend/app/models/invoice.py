"""Invoicing, payments and Paystack collection.

The invoice feature is deliberately self-contained: its tables are prefixed
``invoices`` / ``invoice_`` so they can never collide with the CRM tables, and
every link back into the rest of the app (a contact, a currency, the operator's
business details) is optional. An invoice can therefore be written in ten
seconds from the Invoice page, or raised against an existing contact.

Money is stored in **minor units** (kobo / cents) as integers. Floats are never
used for money in this app: 0.1 + 0.2 is not a price. The frontend formats the
minor units for display and every payment compares integers.

Status is a small, explicit state machine::

    draft ──send──> sent ──view──> viewed
                      │               │
                      └─────pay───────┴──> partial ──pay──> paid
                      │
                   overdue  (derived while unpaid and due_date has passed)
                      │
    any ──void──> void

``overdue`` and ``partial`` are *derived* from money and dates at read time, so
they cannot go stale. ``paid``, ``sent``, ``viewed``, ``draft`` and ``void`` are
stored because they express an action the operator took.
"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token() -> str:
    # 32 hex characters: unguessable enough to be the public invoice link,
    # short enough to paste into an SMS.
    return uuid4().hex


#: Currencies Paystack can charge in. Stored uppercase, ISO-4217 style.
INVOICE_CURRENCIES = ("NGN", "USD", "GHS", "ZAR", "KES", "XOF", "EGP")

#: A payment can never be made after this point without the invoice being
#: re-issued; retained for validation messages.
INVOICE_STATUSES = (
    "draft",
    "sent",
    "viewed",
    "partial",
    "paid",
    "overdue",
    "void",
)

PAYMENT_STATUSES = ("pending", "success", "failed", "reversed")

PAYMENT_PROVIDERS = ("paystack", "manual", "bank_transfer", "cash", "other")


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("number", name="uq_invoices_number"),
        UniqueConstraint("public_token", name="uq_invoices_public_token"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: Human-facing number, e.g. ``INV-2026-0042``. Generated from the prefix and
    #: a per-year sequence; kept unique even if two requests race.
    number: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    #: The token in the public payment link (``/i/<token>``). Rotating it
    #: revokes every link ever shared.
    public_token: Mapped[str] = mapped_column(String(64), nullable=False, default=_token, index=True)

    #: ``invoice`` — a full document with line items and terms.
    #: ``link``    — a quick payment link: a product/amount and a Pay button.
    #: The two share every money, status and Paystack code path, so a link is
    #: reported in the same analytics and can still be opened as an invoice.
    kind: Mapped[str] = mapped_column(String(20), default="invoice", nullable=False, index=True)
    #: The headline of a payment link, e.g. ``Logo design — 50% deposit``.
    link_title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ---- Customer -------------------------------------------------------
    contact_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    customer_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    customer_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_business: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ---- Money ----------------------------------------------------------
    #: ISO-4217 code; must be one of INVOICE_CURRENCIES.
    currency: Mapped[str] = mapped_column(String(3), default="NGN", nullable=False, index=True)
    subtotal_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discount_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Percentage applied to (subtotal - discount), 0..100. Stored as float
    #: because a tax rate is a rate, not money; the resulting tax is an int.
    tax_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tax_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    amount_paid_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ---- Lifecycle ------------------------------------------------------
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)
    issue_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False, index=True)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminders_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ---- Presentation ---------------------------------------------------
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Optional per-invoice footer overriding the saved business signature.
    footer: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- Payment gateway ------------------------------------------------
    #: Latest transaction reference handed to Paystack (a retry creates a new
    #: one, and every attempt is also stored in ``invoice_payments``).
    paystack_reference: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    payment_link: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    payment_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: Optional free-form JSON (e.g. the Paystack authorization URL returned by
    #: the last initialise call) kept for support/debugging.
    meta_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(150), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    items: Mapped[list["InvoiceItem"]] = relationship(
        "InvoiceItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceItem.position",
        lazy="selectin",
    )
    payments: Mapped[list["InvoicePayment"]] = relationship(
        "InvoicePayment",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoicePayment.id.desc()",
        lazy="selectin",
    )
    events: Mapped[list["InvoiceEvent"]] = relationship(
        "InvoiceEvent",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceEvent.id.desc()",
        lazy="selectin",
    )

    @property
    def balance_minor(self) -> int:
        return max(0, int(self.total_minor or 0) - int(self.amount_paid_minor or 0))

    def __repr__(self):  # pragma: no cover - debug helper
        return f"<Invoice(id={self.id}, number={self.number!r}, status={self.status!r})>"


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    #: Quantity is numeric (hours, months, units) but is rounded to 3 decimals
    #: on input; amounts are always integer minor units.
    quantity: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="items")

    def __repr__(self):  # pragma: no cover - debug helper
        return f"<InvoiceItem(id={self.id}, description={self.description!r})>"


class InvoicePayment(Base):
    """One money movement against an invoice.

    A Paystack webhook and the browser callback can both arrive for the same
    reference; ``reference`` is unique per provider so the second one is a
    no-op. Manual payments (cash / bank transfer) use a generated reference.
    """

    __tablename__ = "invoice_payments"
    __table_args__ = (
        UniqueConstraint("provider", "reference", name="uq_invoice_payments_provider_reference"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(30), default="paystack", nullable=False, index=True)
    reference: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    amount_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="NGN", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    channel: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: Paystack's transaction id / gateway id, when there is one.
    gateway_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    payer_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="payments")

    def __repr__(self):  # pragma: no cover - debug helper
        return f"<InvoicePayment(id={self.id}, reference={self.reference!r}, status={self.status!r})>"


class InvoiceEvent(Base):
    """The invoice's own activity log — the history the detail screen shows."""

    __tablename__ = "invoice_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: created | updated | sent | viewed | payment_started | paid | partial |
    #: reminder | voided | duplicated | deleted | note | email_sent
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False, index=True
    )

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="events")

    def __repr__(self):  # pragma: no cover - debug helper
        return f"<InvoiceEvent(id={self.id}, type={self.event_type!r})>"
