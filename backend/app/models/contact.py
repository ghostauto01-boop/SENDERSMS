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
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True, unique=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
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

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_fields: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string

    # Counters
    messages_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    messages_received: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
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
    tags: Mapped[list["ContactTag"]] = relationship("ContactTag", back_populates="contact", cascade="all, delete-orphan")
    list_memberships: Mapped[list["ContactListMember"]] = relationship(
        "ContactListMember", back_populates="contact", cascade="all, delete-orphan"
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
    tag: Mapped["Tag"] = relationship("Tag", back_populates="contacts")
