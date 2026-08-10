"""Gateway settings model — SMS-Gate.app credentials (encrypted at rest)."""
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class GatewaySetting(Base):
    __tablename__ = "gateway_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="SMS-Gate.app")
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="smsgate")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sim_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(String(500), nullable=True)

    timeout: Mapped[int] = mapped_column(Integer, default=45, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    polling_interval: Mapped[int] = mapped_column(Integer, default=60, nullable=False)

    connection_status: Mapped[str] = mapped_column(String(50), default="unknown", nullable=False)
    last_successful_connection: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
