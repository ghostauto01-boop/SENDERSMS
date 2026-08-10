"""
FollowUp model for scheduled follow-up actions.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FollowUp(Base):
    __tablename__ = "followups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(Integer, ForeignKey("contacts.id"), nullable=False, index=True)
    campaign_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("campaigns.id"), nullable=True, index=True)
    campaign_contact_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("campaign_contacts.id"), nullable=True)
    sequence_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("sequences.id"), nullable=True)
    sequence_step_order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Status: pending, sending, sent, delivered, failed, skipped, cancelled
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False, index=True)

    # Timing
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Message
    message_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("messages.id"), nullable=True)
    template_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("templates.id"), nullable=True)
    message_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Tracking
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Notification
    notify_on_due: Mapped[bool] = mapped_column(Integer, default=False, nullable=False)
    is_overdue: Mapped[bool] = mapped_column(Integer, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self):
        return f"<FollowUp(id={self.id}, status={self.status}, scheduled={self.scheduled_at})>"
