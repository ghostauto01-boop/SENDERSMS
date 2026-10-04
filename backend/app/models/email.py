"""Email channel models — Brevo senders, email suppression and the event log.

The email channel mirrors the SMS channel:

* **EmailAccount** is the equivalent of ``GatewaySetting``: one saved provider
  connection (a Brevo API key + the From name/address it may send as). The app
  deliberately supports MANY of these at once -- if one sender/domain gets
  burned, banned or rate-limited you add another account and point the next
  campaign at it. A campaign (or template, or creative) selects the account it
  sends through, and there is an optional fallback account per campaign.

* **EmailSuppression** is the email twin of ``SuppressionEntry``. An SMS STOP
  must not silently unsubscribe somebody from email and vice versa, so the two
  suppression lists are separate tables with separate semantics.

* **EmailEvent** records every Brevo webhook event (delivered / opened /
  clicked / bounced / blocked / spam / unsubscribe) so the activity log and the
  analytics pages have raw evidence, and so a delivery-status regression can
  always be explained.

Nothing here replaces an existing table. ``messages``/``conversations`` gained a
``channel`` column instead of being duplicated, so the inbox, attribution,
analytics and campaign engines all keep working for both channels.
"""

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EmailAccount(Base):
    """One Brevo sender: API key + identity, selectable per campaign."""

    __tablename__ = "email_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: Friendly label shown in pickers, e.g. "Main Brevo" / "Backup domain".
    name: Mapped[str] = mapped_column(String(150), nullable=False)

    #: Only "brevo" today. Stored so a second provider could be added without
    #: a schema change.
    provider: Mapped[str] = mapped_column(String(30), default="brevo", nullable=False)

    from_name: Mapped[str] = mapped_column(String(150), nullable=False)
    from_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    reply_to: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: Fernet-encrypted Brevo API key (never returned to the client).
    api_key_encrypted: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: The account used when a campaign does not pick one explicitly.
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: Optional daily ceiling enforced by this app (Brevo has its own quota;
    #: this one stops a runaway campaign before the provider starts rejecting).
    daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sent_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_reset_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: Brevo open/click tracking switches applied to every send through this
    #: account unless a campaign overrides them.
    track_opens: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    track_clicks: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    #: Shared secret appended to the inbound webhook URL so an anonymous
    #: caller cannot inject fake inbound email. Generated on first use.
    webhook_token: Mapped[str | None] = mapped_column(String(120), nullable=True)

    connection_status: Mapped[str] = mapped_column(String(30), default="unknown", nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    def __repr__(self):  # pragma: no cover - debug helper
        return f"<EmailAccount(id={self.id}, name={self.name}, from={self.from_email})>"


class EmailSuppression(Base):
    """An address that must never receive email from this app again."""

    __tablename__ = "email_suppressions"
    __table_args__ = (Index("ix_email_suppressions_email", "email_address"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: Stored lower-cased so the unique check and every lookup agree.
    email_address: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: manual | unsubscribe | bounce | complaint | automation | keyword
    source: Mapped[str] = mapped_column(String(40), default="manual", nullable=False)
    opt_out_keyword: Mapped[str | None] = mapped_column(String(80), nullable=True)

    #: Set when Brevo reported the bounce/complaint, so the UI can explain it.
    hard_bounce: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    suppressed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def __repr__(self):  # pragma: no cover - debug helper
        return f"<EmailSuppression(id={self.id}, email={self.email_address})>"


class EmailEvent(Base):
    """Raw Brevo webhook event, kept for the activity log and analytics."""

    __tablename__ = "email_events"
    __table_args__ = (
        Index("ix_email_events_type_created", "event_type", "created_at"),
        Index("ix_email_events_message", "message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: delivered | opened | clicked | bounce | blocked | spam | unsubscribed
    #: | sent | error | inbound
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ads_campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    email_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Brevo's message id / event id, retained for de-duplication.
    provider_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    link: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: The raw webhook body (JSON string) — capped at 8 KB by the writer.
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def __repr__(self):  # pragma: no cover - debug helper
        return f"<EmailEvent(id={self.id}, type={self.event_type})>"
