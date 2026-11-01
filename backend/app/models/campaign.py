"""
Campaign and CampaignContact models.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Status: draft, scheduled, running, paused, completed, stopped, failed.
    # The legal moves between them live in CampaignService.TRANSITIONS and every
    # change goes through CampaignService.transition().
    status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False, index=True)

    #: Where a paused campaign came from ("running" is stored as NULL, which is
    #: what every row paused before this column existed means). A campaign paused
    #: while ``scheduled`` has never sent anything and holds no contact rows, so
    #: resuming it must put the schedule back rather than start sending.
    paused_from: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: When it was paused, and why. Only people pause campaigns: the bounce
    #: watchdog reports a high bounce rate as a notification instead of stopping
    #: anything, so every value here was written by an operator.
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: "sms" or "email" — which channel this campaign sends on. Existing rows
    #: default to "sms", so nothing about the current behaviour changes.
    channel: Mapped[str] = mapped_column(String(10), default="sms", nullable=False, index=True)

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

    # ---- Email channel -------------------------------------------------
    #: Unified Brevo (+) or connected-mailbox (-) sender id; NULL uses the active default.
    email_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    #: Optional unified sender tried automatically if the primary provider rejects the send.
    fallback_email_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: OPTIONAL multi-sender pool: unified sender ids (positive Brevo,
    #: negative Gmail mailbox) across which contacts are distributed so one
    #: From address does not take the whole campaign's volume/reputation hit.
    #: NULL/empty = legacy single-sender behaviour (``email_account_id``).
    email_account_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    #: round_robin | weighted | random — how the pool above is distributed.
    sender_distribution: Mapped[str] = mapped_column(
        String(20), default="round_robin", nullable=False
    )
    #: Optional {unified_sender_id: weight} map for "weighted", mirroring
    #: creative allocations. NULL = equal weights.
    email_sender_weights: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Rich body for email campaigns (template_id / message_body still work).
    html_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    track_opens: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    track_clicks: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: "Plain personal email" mode: text/plain part + minimal HTML (no page
    #: background, no 600px card, no tracking pixel, no link wrapping), a tiny
    #: gray plain-text "unsubscribe" link line, and the standard
    #: List-Unsubscribe headers. Classic campaigns default OFF (existing look);
    #: Email Manager campaigns default ON.
    plain_personal_email: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Optional {unified_sender_id: daily_cap} map — a per-sender daily cap on
    #: top of the campaign's own daily_limit. NULL = no per-sender cap.
    sender_daily_limits: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: IANA zone the send window is evaluated in (recipient/operator local
    #: time). NULL = settings.DEFAULT_TIMEZONE.
    timezone_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: CSV of weekday numbers allowed for sending (0=Mon .. 6=Sun), e.g.
    #: "0,1,2,3,4". NULL/empty = the legacy allow_weekends flag decides.
    allowed_weekdays: Mapped[str | None] = mapped_column(String(30), nullable=True)
    #: JSON list of attachments (base64) sent with every email in this campaign.
    attachments: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional campaign-scoped sending controls; NULL means unrestricted.
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
    #: Sender this contact was pinned to from the campaign's multi-sender
    #: pool (unified id), assigned like a creative so the whole thread —
    #: initial send and follow-ups — stays on one From address. NULL = the
    #: campaign's single/default sender decides at send time.
    email_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    # Message tracking
    messages_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Why this contact left the sending flow (filter reason, gateway error…).
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
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
