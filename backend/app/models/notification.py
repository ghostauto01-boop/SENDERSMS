"""
Notification provider and event models.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NotificationProvider(Base):
    __tablename__ = "notification_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)  # onesignal, pushover
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Configuration
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # Encrypted JSON config

    # Event toggles
    notify_new_reply: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_campaign_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notify_campaign_failed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_gateway_offline: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_followup_due: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_system_error: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Status
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class NotificationEvent(Base):
    __tablename__ = "notification_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )
