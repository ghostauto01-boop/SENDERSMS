"""Inbound SMS that is not a recognised lead reply.

This table is deliberately separate from contacts, conversations and messages:
quarantined traffic never affects inbox, reply, campaign, or unread counts.
Sensitive financial and one-time-code traffic is discarded before a row is
created here.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class QuarantinedSMS(Base):
    __tablename__ = "quarantined_sms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sender: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )
