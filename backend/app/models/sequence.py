"""
Sequence, SequenceStep, and SequenceVersion models.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Sequence(Base):
    __tablename__ = "sequences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Integer, default=True, nullable=False)

    #: "sms" or "email". A sequence belongs to one channel, exactly like a
    #: campaign, an automation and an auto-reply rule: an email sequence can
    #: never text anybody, and an SMS sequence can never mail anybody.
    #: Existing rows are SMS (that was the only channel when they were built).
    channel: Mapped[str] = mapped_column(String(10), default="sms", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    steps: Mapped[list["SequenceStep"]] = relationship(
        "SequenceStep", back_populates="sequence", cascade="all, delete-orphan",
        order_by="SequenceStep.step_order",
    )
    versions: Mapped[list["SequenceVersion"]] = relationship(
        "SequenceVersion", back_populates="sequence", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Sequence(id={self.id}, name={self.name})>"


class SequenceStep(Base):
    __tablename__ = "sequence_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sequence_id: Mapped[int] = mapped_column(Integer, ForeignKey("sequences.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Step ordering
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)

    # Type: send_sms, send_email, wait, condition, stop
    step_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Configuration as JSON string
    config: Mapped[str | None] = mapped_column(Text, nullable=True)

    # For wait steps: duration in hours
    wait_duration_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # For send steps: template id
    template_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("templates.id"), nullable=True)

    # ---- Email channel (send_email steps) --------------------------------
    #: Subject line for a send_email step. Required: an email with no subject
    #: is treated as spam by every provider.
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Optional sender override. Positive ids are Brevo accounts, negative ids
    #: are connected Gmail mailboxes (same convention as the rest of email).
    email_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # For condition steps: condition type and target
    condition_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    condition_value: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Branching: step to go to if condition is true/false
    true_branch_step_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    false_branch_step_order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    sequence: Mapped["Sequence"] = relationship("Sequence", back_populates="steps")

    def __repr__(self):
        return f"<SequenceStep(id={self.id}, type={self.step_type}, order={self.step_order})>"


class SequenceVersion(Base):
    """Snapshot of a sequence at a specific version, used for running campaigns."""
    __tablename__ = "sequence_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sequence_id: Mapped[int] = mapped_column(Integer, ForeignKey("sequences.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[str] = mapped_column(Text, nullable=False)  # JSON snapshot of all steps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    sequence: Mapped["Sequence"] = relationship("Sequence", back_populates="versions")
