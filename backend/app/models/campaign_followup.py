"""Campaign follow-up rules and their per-contact delivery log.

A campaign sends one message to a list. A *follow-up rule* says: "if this
contact has not replied N hours after the previous message, send this text".
Rules are ordered (follow-up 1, follow-up 2, ...) and each carries its own
stop conditions, so an operator can chain reminders and have the chain end the
moment a contact replies, opts out or reaches a chosen lead status.

``CampaignFollowUpLog`` records exactly one row per (rule, contact) so a
follow-up can never be sent twice, and so the UI can show why a chain stopped.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CampaignFollowUp(Base):
    __tablename__ = "campaign_followups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: 1-based position in the chain. Step 2 waits for step 1 to have been sent.
    step_order: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    #: The message. Supports the same {{shortcode}} variables as everything
    #: else. A template can be used instead.
    message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("templates.id"), nullable=True
    )

    #: Wait time measured from the previous message to this contact.
    delay_minutes: Mapped[int] = mapped_column(Integer, default=1440, nullable=False)

    # --- Stop conditions -------------------------------------------------
    #: Do not send (and end the chain) if the contact replied.
    stop_on_reply: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Always respected for compliance, kept explicit for clarity in the UI.
    stop_on_opt_out: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Comma-separated lead statuses that end the chain, e.g. "interested,customer".
    stop_on_lead_status: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: Only send inside this local-hour window (optional, 0-23).
    send_start_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    send_end_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stopped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def stop_statuses(self) -> list[str]:
        raw = self.stop_on_lead_status or ""
        return [part.strip().lower() for part in raw.split(",") if part.strip()]

    def __repr__(self):  # pragma: no cover - debug helper
        return f"<CampaignFollowUp(campaign={self.campaign_id}, step={self.step_order})>"


class CampaignFollowUpLog(Base):
    __tablename__ = "campaign_followup_logs"
    __table_args__ = (UniqueConstraint("followup_id", "contact_id", name="uq_followup_contact"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    followup_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("campaign_followups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campaign_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    #: sent | failed | stopped | skipped
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_preview: Mapped[str | None] = mapped_column(String(300), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
