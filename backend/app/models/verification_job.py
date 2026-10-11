"""A queued, resumable run of the email verifier over a set of contacts."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EmailVerificationJob(Base):
    __tablename__ = "email_verification_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    #: queued -> running -> done | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False, index=True)
    #: Ask the mailbox (SMTP) as well as DNS. The job falls back to a DNS-only run when
    #: this server cannot confirm mailboxes, and records why (``smtp_reason``).
    deep: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    recheck: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: The contacts to check, snapshotted when the job was created. ``processed`` is the
    #: index into it, so a restart resumes exactly where the job stopped.
    contact_ids_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invalid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    risky: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unknown: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    engine: Mapped[str | None] = mapped_column(String(20), nullable=True)
    smtp_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    smtp_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
