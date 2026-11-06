"""A mirror of every message in a connected mailbox — the mail app's data.

WHY THIS TABLE EXISTS
---------------------
Before this, the app stored only what it recognised as *a reply to something it
sent*: a message that did not thread to an outbound Message-ID was skipped, so
the inbox could never show Sent, Spam, Drafts or the ordinary mail that fills a
real mailbox. The operator's ask — "select each Gmail account and see the
replies it got, the sent, the spam, even the drafts, and the entire inbox from
all the accounts, like Gmail" — needs every message, not just the recognised
ones.

``MailboxMessage`` is that mirror: one row per Gmail message per folder, keyed
by ``(mailbox_id, provider_message_id)`` so a re-sync updates a row instead of
duplicating it. It is a *cache*, not the source of truth — Gmail still owns the
message — which is why:

* bodies are truncated (``is_truncated`` says so) and the full copy is fetched
  on demand straight from Gmail by :func:`app.services.mailbox_message_service`;
* deleting the row never deletes the mail;
* a folder move updates ``folder`` from the labels Gmail reports, so the app's
  view cannot drift from Gmail's.

Conversation linkage is best-effort and one-directional: ``conversation_id`` is
set when a message threads to a CRM conversation, which is what lets the mail
app open the CRM thread from a message and show a "replied in SENDERSMS" badge.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MailboxMessage(Base):
    """One message in one connected mailbox, as the mail app shows it."""

    __tablename__ = "mailbox_messages"
    __table_args__ = (
        UniqueConstraint("mailbox_id", "provider_message_id",
                         name="uq_mailbox_messages_mailbox_provider"),
        Index("ix_mailbox_messages_mailbox_folder_date", "mailbox_id", "folder", "date"),
        Index("ix_mailbox_messages_thread", "mailbox_id", "thread_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox_id: Mapped[int] = mapped_column(
        ForeignKey("email_mailboxes.id", ondelete="CASCADE"), nullable=False
    )

    #: Gmail's own message id. Stable across syncs; the natural key.
    provider_message_id: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Gmail's thread id — what groups messages in the reading pane.
    thread_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    #: INBOX | SENT | SPAM | DRAFTS | TRASH | OTHER
    folder: Mapped[str] = mapped_column(String(20), nullable=False, default="INBOX")
    #: Every Gmail label, JSON-encoded, so a custom label survives a sync.
    labels: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    subject: Mapped[str | None] = mapped_column(String(600), nullable=True)
    from_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: JSON list of addresses, so "To: a, b" is searchable and displayable.
    to_addresses: Mapped[str | None] = mapped_column(Text, nullable=True)
    cc_addresses: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: The message's own Date header, i.e. when it was written/sent.
    date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    #: When this app first saw it — used for "new since last visit".
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    snippet: Mapped[str | None] = mapped_column(String(600), nullable=True)
    #: Truncated copies. A 2 MB newsletter is not worth two megabytes per row.
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_starred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attachment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    rfc_message_id: Mapped[str | None] = mapped_column(String(400), nullable=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(400), nullable=True)
    references: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Set when the message threads to a CRM conversation (a real reply).
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<MailboxMessage(id={self.id}, {self.folder}, {self.from_address})>"
