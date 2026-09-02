"""Automation model — GoHighLevel-style If/Then reply automation.

An automation watches inbound replies and, when its trigger conditions match,
runs an ordered list of actions. This is what lets an operator automate
"if they say NO / wrong number -> remove them and never send the 2nd message"
versus "if they say YES -> send the follow-up message".

Conditions and actions are stored as JSON lists so new field/action types can
be added without schema migrations.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Automation(Base):
    __tablename__ = "automations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Lower runs first. Ties break on id for a stable order.
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    # Trigger type. "inbound_reply" is the only trigger today.
    trigger_type: Mapped[str] = mapped_column(String(40), default="inbound_reply", nullable=False)

    # JSON list of conditions. All must pass when match_all is true (AND);
    # otherwise any one passing fires the automation (OR).
    conditions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_all: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # JSON list of actions, executed in order.
    actions_json: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    def __repr__(self):
        return f"<Automation(id={self.id}, name={self.name}, enabled={self.is_enabled})>"
