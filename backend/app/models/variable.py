"""Contact variable registry.

Every CSV import discovers columns ("Pain Point", "Niche", "States", ...).
Those values are stored on ``Contact.custom_fields`` as JSON, but until now
there was no catalogue of *which* variables exist, and no way for a user to
choose the short code they want to type inside a message.

``ContactVariable`` is that catalogue: one row per known variable, with the
short code the operator types between ``{{ }}``. The renderer resolves a short
code to its ``field_key`` before looking the value up on the contact.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ContactVariable(Base):
    __tablename__ = "contact_variables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: Canonical storage key. For standard variables this is the Contact column
    #: name (``first_name``); for imported ones it is the normalized CSV header
    #: (``pain_point``) exactly as ``csv_service.custom_field_key`` produces it.
    field_key: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)

    #: Human label shown in the UI, e.g. "Pain Point".
    label: Mapped[str] = mapped_column(String(200), nullable=False)

    #: What the user types between the braces. Defaults to ``field_key`` and is
    #: matched case-insensitively and whitespace-insensitively, so a short code
    #: of ``pain_point`` also answers to ``{{Pain Point}}``.
    shortcode: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)

    #: Optional text used when a contact has no value for this variable.
    #: Empty means "remove the placeholder from the message".
    fallback_text: Mapped[str | None] = mapped_column(String(500), nullable=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: "standard" (a real Contact column) or "imported" (a custom CSV field).
    source: Mapped[str] = mapped_column(String(30), default="imported", nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    #: Bookkeeping so the page can show "how many contacts have this".
    contact_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sample_value: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self):  # pragma: no cover - debug helper
        return f"<ContactVariable({self.field_key} -> {{{{{self.shortcode}}}}})>"
