"""Scheduled message model."""
from datetime import datetime, timezone
from sqlalchemy import DateTime, Integer, String, Text, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class ScheduledMessage(Base):
    __tablename__ = "scheduled_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("contacts.id"), nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    list_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("contact_lists.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    schedule_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending, sent, failed, cancelled
    sim_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    message_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("messages.id"), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
