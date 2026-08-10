"""
Webhook event log model.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # inbound_sms, delivery_status
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)

    # Request/Response
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    headers: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Processing
    status: Mapped[str] = mapped_column(String(50), default="received", nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )

    def __repr__(self):
        return f"<WebhookEvent(id={self.id}, type={self.event_type})>"
