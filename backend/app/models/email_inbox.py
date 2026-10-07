"""
Connected mailboxes — how a prospect's reply gets back into this app.

WHY THIS EXISTS
---------------
Brevo can only hand us mail addressed to a domain whose MX records point at
Brevo. A reply to ``you@gmail.com`` never reaches Brevo at all, so with a Gmail
sender there was no path from "prospect pressed reply" to "the reply is in the
app's inbox" — and if Gmail filed that reply under Spam, nobody saw it anywhere.

``EmailMailbox`` is that missing path: a mailbox this app is allowed to read
(and, optionally, send through). Two kinds are supported, because they cost the
operator different amounts of setup:

* ``imap`` — an address plus an app password. No Google Cloud project, no OAuth
  consent screen, works in five minutes. Read from INBOX and ``[Gmail]/Spam``,
  move a reply out of Spam, and send through Gmail's own SMTP so the reply is
  DMARC-aligned and lands in Sent.
* ``gmail_api`` — OAuth against the Gmail API with a stored refresh token. Adds
  the things IMAP cannot do: precise label changes
  (``users.messages.modify``), *creating* a Gmail filter whose action is
  "never send it to Spam" (``users.settings.filters``), and push notifications.

Both feed the same ingestion pipeline as Brevo's inbound webhook, so threading,
campaign attribution, auto-replies, automations and the inbox UI behave
identically no matter which door the reply came through.

Credentials are Fernet-encrypted with the same helper the Brevo keys use; the
plaintext is never returned to the client and never logged.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EmailMailbox(Base):
    """One mailbox this app reads replies from (and may send through)."""

    __tablename__ = "email_mailboxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: Friendly label, e.g. "Replies — my Gmail".
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    #: imap | gmail_api
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="imap")
    #: The mailbox address. Replies addressed here are the ones we want.
    email_address: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    #: Fernet-encrypted: an IMAP app password, or a Gmail API refresh token.
    credential_encrypted: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    #: Only for gmail_api, when the operator brings their own Google project.
    #: Falls back to GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET from the environment.
    client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_secret_encrypted: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    #: Folders to read. Gmail's Spam folder is included on purpose: a reply that
    #: landed there is exactly the one the operator was missing.
    folders: Mapped[str] = mapped_column(
        String(500), nullable=False, default="INBOX,[Gmail]/Spam"
    )
    #: Pull the whole mailbox instead of only recognised replies? Off by default:
    #: importing every newsletter into a CRM inbox is not what anyone wants.
    import_all: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Move a recognised reply out of Spam and back into the Inbox.
    rescue_from_spam: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Ask Gmail for a "never send it to Spam" filter (gmail_api only).
    never_spam_filter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Send one-to-one replies through this mailbox instead of Brevo. This is the
    #: fix for the root cause: a reply that leaves from the same authenticated
    #: mailbox the thread started in is aligned, and stops dragging the thread
    #: through Gmail's spoofing filter.
    send_replies: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    poll_interval: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    #: Watermarks so a sync is incremental: IMAP UIDNEXT per folder, or the
    #: Gmail historyId. JSON: {"INBOX": 4311, "[Gmail]/Spam": 88} / {"historyId": "..."}
    cursor_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str] = mapped_column(String(20), nullable=False, default="never")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Lifetime counters, shown on the card so the operator can see it working.
    total_synced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_replies: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_rescued: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: The Gmail filter ids this app created, so it can remove them on delete.
    filter_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Gmail label id used to tag rescued/imported replies.
    label_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    def folder_list(self) -> list[str]:
        return [f.strip() for f in (self.folders or "").split(",") if f.strip()]

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<EmailMailbox(id={self.id}, {self.email_address}, provider={self.provider})>"


class EmailContactAddress(Base):
    """An alternate sender address that belongs to an existing CRM contact.

    A contact's primary ``Contact.email`` is never replaced when a reply arrives
    from a personal/changed mailbox. This row links that address back to the same
    conversation and lets the inbox make subsequent replies to it.
    """

    __tablename__ = "email_contact_addresses"
    __table_args__ = (
        UniqueConstraint("email_address", name="uq_email_contact_addresses_address"),
        Index("ix_email_contact_addresses_contact_id", "contact_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    email_address: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    contact: Mapped["Contact"] = relationship("Contact", back_populates="email_aliases")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<EmailContactAddress(id={self.id}, contact_id={self.contact_id}, {self.email_address})>"
