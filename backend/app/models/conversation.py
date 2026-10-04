"""
Conversation and Message models for the SMS inbox.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # One thread per contact PER CHANNEL. The whole codebase looks
    # conversations up with scalar_one_or_none(), so a duplicate row would make
    # every send and every inbound message for that contact raise
    # MultipleResultsFound forever -- the unique key therefore covers
    # (contact_id, channel), letting a contact have one SMS thread and one
    # email thread as separate inbox items.
    __table_args__ = (
        UniqueConstraint("contact_id", "channel", name="uq_conversation_contact_channel"),
    )

    contact_id: Mapped[int] = mapped_column(Integer, ForeignKey("contacts.id"), nullable=False, index=True)

    #: "sms" or "email".
    channel: Mapped[str] = mapped_column(String(10), default="sms", nullable=False, index=True)
    #: Email threads remember which sender they started with, so a reply goes
    #: out from the same address it arrived on.
    email_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Email only: the subject of the most recent message in the thread.
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ---- Campaign attribution -------------------------------------------
    # "Which campaign is this lead from?" is answered by the FIRST campaign
    # that ever messaged the contact (campaign_id for a classic campaign,
    # ads_campaign_id for an SMS Ads Manager campaign -- exactly one of the
    # two is set). The last_* pair tracks the most recent campaign to touch
    # the thread, which is what a reply should be credited to.
    #
    # Stamped by app.services.attribution at send time, and self-healed for
    # historical threads when the inbox reads them, so the badge is correct
    # for data created before this existed.
    campaign_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("campaigns.id"), nullable=True, index=True)
    ads_campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    last_campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    last_ads_campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    # Status: active, unread, read, interested, not_interested, closed
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False, index=True)

    # Counters
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unread_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Sequence paused by reply
    sequence_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    last_message_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    def __repr__(self):
        return f"<Conversation(id={self.id}, status={self.status})>"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[int] = mapped_column(Integer, ForeignKey("contacts.id"), nullable=False, index=True)
    campaign_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("campaigns.id"), nullable=True)
    # Set when the SMS Ads Manager sent this message. Kept separate from
    # campaign_id because the two live in different tables (campaigns /
    # ads_campaigns) and a foreign key cannot point at both.
    ads_campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    # Direction
    direction: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # incoming, outgoing

    #: "sms" or "email". Defaults to sms so every existing row keeps its
    #: meaning and every existing query keeps working.
    channel: Mapped[str] = mapped_column(String(10), default="sms", nullable=False, index=True)

    # Message content
    body: Mapped[str] = mapped_column(Text, nullable=False)
    segment_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ---- Email channel fields -----------------------------------------
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Rendered HTML actually sent (or received, for inbound mail).
    html_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Envelope addresses, kept on the message itself so an address change on
    #: the contact never rewrites history.
    from_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_address: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    email_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    #: The RFC 5322 Message-ID we put on the wire (``<uuid@domain>``). Inbound
    #: replies carry ``In-Reply-To``/``References`` pointing at it, which is how
    #: a reply is welded to the exact message it answers — and how our own
    #: follow-ups stay in the same mail thread in Gmail/Outlook.
    rfc_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    #: ``In-Reply-To`` from an inbound message, kept so the thread can be
    #: reconstructed even if the subject changed.
    in_reply_to: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    #: JSON list of attachment metadata (name, mime, size). The base64 payload
    #: is stored too (so a failed send can be retried) up to a hard size cap.
    attachments: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: True for campaign/audience mail (which gets List-Unsubscribe headers,
    #: required by Gmail/Yahoo for bulk senders) and False for one-to-one mail.
    bulk_send: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Comma-separated CC / BCC lists, kept on the row so a queued or scheduled
    #: send still copies the same people hours later.
    cc_addresses: Mapped[str | None] = mapped_column(Text, nullable=True)
    bcc_addresses: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Filled in by the Brevo webhook: opens/clicks are what "engagement"
    #: means for an email campaign.
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    open_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    click_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bounced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bounced_hard: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Status: queued, sending, sent, delivered, failed, unknown
    status: Mapped[str] = mapped_column(String(50), default="queued", nullable=False, index=True)

    # Provider tracking
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    provider_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Retry
    # Marks a message the autoresponder generated. Needed to enforce the
    # per-contact cooldown, and to make automated traffic distinguishable from
    # something a human actually typed.
    is_auto_reply: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Keyless AI classification of inbound replies (positive/negative/neutral,
    # intent such as wrong_number / interested / opt_out). Stored at receive
    # time so the inbox and analytics can surface it without re-classifying.
    ai_sentiment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ai_intent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ai_confidence: Mapped[float | None] = mapped_column(nullable=True)

    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Delivered/failed timestamps
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")

    def __repr__(self):
        return f"<Message(id={self.id}, direction={self.direction}, status={self.status})>"
