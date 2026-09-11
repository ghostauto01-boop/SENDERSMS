"""Call logs for the CallGate (phone-call) integration."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CallLog(Base):
    """One phone call placed (or received) through the CallGate handset app.

    Statuses: initiated -> ringing -> started -> ended | failed | no_answer |
    busy | cancelled. Webhooks (call:ringing/started/ended) advance the row;
    rows created by direct tel: dialling stay ``direct_dial``.
    """

    __tablename__ = "call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    phone_number: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(10), default="outgoing", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="initiated", nullable=False, index=True)

    provider_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    device_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    contact: Mapped["Contact | None"] = relationship("Contact", lazy="selectin")

    def __repr__(self):
        return f"<CallLog(id={self.id}, phone={self.phone_number}, status={self.status})>"
