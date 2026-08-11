"""Auto-reply rules.

A rule answers an inbound SMS automatically. Rules are entirely user-defined:
the operator sets the keywords, the match mode and the reply text. Nothing is
hard-coded, and with no rules configured the feature is inert -- inbound
handling behaves exactly as it did before.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AutoReplyRule(Base):
    __tablename__ = "auto_reply_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # Comma-separated triggers, e.g. "price, pricing, how much".
    # Ignored when match_type is "any" (a catch-all rule).
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)

    # How `keywords` is compared against the inbound text:
    #   contains  - the message contains any keyword anywhere
    #   exact     - the whole message equals a keyword (after trimming)
    #   starts    - the message begins with a keyword
    #   any       - catch-all; matches every inbound message
    # Matching is always case-insensitive.
    match_type: Mapped[str] = mapped_column(String(20), default="contains", nullable=False)

    # The reply. Supports the same {{first_name}} placeholders as campaigns.
    reply_body: Mapped[str] = mapped_column(Text, nullable=False)

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Lower runs first. Ties break on id, so rule order is always stable.
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    # Do not answer the same contact more often than this. 0 disables the
    # cooldown (reply every single time), which risks a loop against another
    # autoresponder -- hence the non-zero default.
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=240, nullable=False)

    # Stop after the first match (normal) or keep evaluating later rules.
    stop_on_match: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    times_triggered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def keyword_list(self) -> list[str]:
        """Triggers as a clean lowercase list."""
        if not self.keywords:
            return []
        return [k.strip().lower() for k in self.keywords.split(",") if k.strip()]

    def __repr__(self):
        return f"<AutoReplyRule(id={self.id}, name={self.name}, enabled={self.is_enabled})>"
