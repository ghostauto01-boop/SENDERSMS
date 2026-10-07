"""
Contact, Tag, and ContactTag models.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    first_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    business_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Either channel identifiers the contact on its own. A row is valid with a
    # phone, with an email, or with both — an email-only contact (a restaurant
    # that publishes hello@ but no number) is a first-class record, not a
    # rejected import row. NULL phone is allowed and, because SQL treats NULLs
    # as distinct in a unique index, any number of email-only contacts can
    # coexist without colliding on the phone column.
    phone_number: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True, unique=True
    )
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    city: Mapped[str | None] = mapped_column(String(150), nullable=True)
    state: Mapped[str | None] = mapped_column(String(150), nullable=True)
    country: Mapped[str] = mapped_column(String(100), default="Nigeria", nullable=False)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Lead status
    lead_status: Mapped[str] = mapped_column(
        String(50),
        default="new",
        nullable=False,
        index=True,
    )

    # Consent and opt-out
    consent_status: Mapped[str] = mapped_column(String(50), default="unknown", nullable=False)
    has_consented: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_opted_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opt_out_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Numbers that bounced / failed delivery. Sending again still bills the
    # carrier, so we skip these until an operator unblocks them.
    is_undeliverable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    undeliverable_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    delivery_fail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ---- Email channel state ------------------------------------------
    # Deliberately separate from the SMS opt-out flags: an SMS STOP must not
    # unsubscribe somebody from email, and an email unsubscribe must not stop
    # their texts. ``email_status`` is the coarse state the UI shows:
    # active | unsubscribed | bounced | complained | invalid.
    is_email_opted_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_opt_out_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    email_status: Mapped[str] = mapped_column(String(30), default="active", nullable=False, index=True)
    #: Hard-bounced / provider-blocked addresses are skipped like undeliverable
    #: numbers, because sending again damages the sender reputation.
    is_email_undeliverable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_fail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    email_last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ---- Email enrichment --------------------------------------------
    # Where the address came from and how much it can be trusted.
    #
    # ``email_source`` is one of:
    #   csv / manual  — the address was supplied by a human, never a guess
    #   website       — harvested from a page the business publishes itself
    #   hunter        — returned by an email-finder provider
    #   inferred      — pattern-guessed (first.last@domain …). NOT verified:
    #                   the mailbox was never proven to exist.
    # And ``email_verified`` is True only after a real verification call
    # (ZeroBounce / NeverBounce / Hunter) said the mailbox exists. Guesses are
    # stored — with email_verified False — so they can be reviewed, while every
    # send path can filter them out. Storing a guess as if it were as good as a
    # typed-in address is what silently wrecks sender reputation.
    email_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 0-100. Provider verdicts map to a score; guesses are capped low.
    email_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    email_enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    email_enrichment_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Dedupe key for email-only rows. ``contacts.email`` cannot carry a UNIQUE
    #: constraint on an existing database (the ALTER would fail on pre-existing
    #: duplicates), so uniqueness is enforced by the application against
    #: ``func.lower(email)`` instead.
    email_lower: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_fields: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string

    # Counters
    messages_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    messages_received: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    emails_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    emails_received: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_email_reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    # `selectin` loads tags eagerly so serializing a contact (ContactOut.tags)
    # never triggers an async lazy-load (which raises MissingGreenlet).
    tags: Mapped[list["ContactTag"]] = relationship(
        "ContactTag", back_populates="contact", cascade="all, delete-orphan", lazy="selectin"
    )
    list_memberships: Mapped[list["ContactListMember"]] = relationship(
        "ContactListMember", back_populates="contact", cascade="all, delete-orphan"
    )
    email_aliases: Mapped[list["EmailContactAddress"]] = relationship(
        "EmailContactAddress", back_populates="contact", cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<Contact(id={self.id}, phone={self.phone_number})>"


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    contacts: Mapped[list["ContactTag"]] = relationship("ContactTag", back_populates="tag", cascade="all, delete-orphan")


class ContactTag(Base):
    __tablename__ = "contact_tags"
    __table_args__ = (UniqueConstraint("contact_id", "tag_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    tag_id: Mapped[int] = mapped_column(Integer, ForeignKey("tags.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    contact: Mapped["Contact"] = relationship("Contact", back_populates="tags")
    tag: Mapped["Tag"] = relationship("Tag", back_populates="contacts", lazy="selectin")
