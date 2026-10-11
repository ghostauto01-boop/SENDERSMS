"""The domain mail is sent FROM, the DNS records it needs, and where it is in its warm-up."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SendingDomain(Base):
    __tablename__ = "sending_domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: Lower-case, unique. Never a free-mail domain.
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    #: pending (records published or not, Brevo has not authenticated it) | authenticated
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    #: The planned DNS records (a JSON list of {type, host, value, purpose, ...}). The DMARC entry is
    #: always exactly one record, merged with whatever the domain already publishes.
    records_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    #: Address that should receive DMARC aggregate reports (merged into the DMARC ``rua``).
    dmarc_rua: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The Brevo account this domain was registered with.
    email_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: When the domain first became authenticated: day 1 of the warm-up ramp. NULL = not ramping.
    warmup_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc), nullable=False,
    )
