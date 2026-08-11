"""
Campaign and CampaignContact models.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Status: draft, scheduled, running, paused, completed, stopped, failed
    status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False, index=True)

    # References
    list_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("contact_lists.id"), nullable=True)
    template_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("templates.id"), nullable=True)
    sequence_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("sequences.id"), nullable=True)
    # Ad-hoc message written directly on the campaign, as an alternative to
    # picking a saved Template. Takes precedence over template_id when set, so
    # a one-off blast does not require creating a throwaway template first.
    # Supports the same {{first_name}} style placeholders as templates.
    message_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gateway_setting_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("gateway_settings.id"), nullable=True)

    # Sending rules
    daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hourly_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    per_minute_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_delay: Mapped[int | None] = mapped_column(Integer, nullable=True)  # seconds
    max_delay: Mapped[int | None] = mapped_column(Integer, nullable=True)  # seconds
    send_start_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-23
    send_end_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-23
    allow_weekends: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Stats
    total_contacts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    messages_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    messages_delivered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    messages_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    replies: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    interested: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Timestamps
    # When the campaign was moved into the "scheduled" state (bookkeeping).
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The future time the user asked the campaign to launch at. Distinct from
    # scheduled_at, which only records when validation happened: a campaign can
    # be validated today and set to go out next Monday. NULL means "send as
    # soon as it is started", which is the pre-existing behaviour.
    scheduled_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    contacts: Mapped[list["CampaignContact"]] = relationship(
        "CampaignContact", back_populates="campaign", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Campaign(id={self.id}, name={self.name}, status={self.status})>"


class CampaignContact(Base):
    __tablename__ = "campaign_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    contact_id: Mapped[int] = mapped_column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True)

    # Status for this contact in the campaign
    status: Mapped[str] = mapped_column(
        String(50), default="pending", nullable=False, index=True
    )  # pending, queued, sent, delivered, failed, replied, opted_out
    sequence_step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    # Message tracking
    messages_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="contacts")
