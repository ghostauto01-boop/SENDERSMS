"""
Meeting / calendar models.

A Meeting is the calendar's core object: a scheduled appointment with one or
more contacts (attendees), optional SMS invite + reminders rendered through the
same template/short-code engine as everything else, and an optional link back
to the inbox conversation it was booked from.

Reminders are stored as a JSON list of "minutes before start" (e.g. [1440, 60,
15]) and delivered by the same inline poller that drives scheduled sends, so
they fire even on deployments with no awake Celery worker.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# Active states the calendar shows by default; finished/cancelled ones are
# reachable through the status filter.
MEETING_STATUSES = ("scheduled", "confirmed", "completed", "cancelled", "no_show")

MEETING_TYPES = ("meeting", "call", "reminder", "task", "follow_up", "other")


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # meeting | call | reminder | task | follow_up | other
    event_type: Mapped[str] = mapped_column(String(30), default="meeting", nullable=False, index=True)

    # scheduled | confirmed | completed | cancelled | no_show
    status: Mapped[str] = mapped_column(String(30), default="scheduled", nullable=False, index=True)

    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    all_day: Mapped[bool] = mapped_column(Integer, default=False, nullable=False)

    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    meeting_link: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Primary contact (first attendee). The full guest list lives in
    # MeetingAttendee so a meeting can have any number of contacts.
    contact_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("contacts.id"), nullable=True, index=True
    )
    # Inbox conversation this was booked from (book-a-meeting while chatting).
    conversation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("conversations.id"), nullable=True, index=True
    )
    campaign_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("campaigns.id"), nullable=True, index=True
    )

    # JSON list of tag names, e.g. ["vip", " lagos "]. Stored inline (like
    # contact custom fields) so meetings can be tagged without a join table.
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- SMS invite (sent on booking, per attendee, personalized) ----
    send_invite_sms: Mapped[bool] = mapped_column(Integer, default=True, nullable=False)
    invite_template_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("templates.id"), nullable=True
    )
    invite_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    invite_sent: Mapped[bool] = mapped_column(Integer, default=False, nullable=False)

    # ---- SMS reminders ----
    send_sms_reminder: Mapped[bool] = mapped_column(Integer, default=True, nullable=False)
    # JSON list of minutes-before-start, e.g. [1440, 60, 15]
    reminder_minutes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reminder_template_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("templates.id"), nullable=True
    )
    reminder_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON dict {minutes_before: iso_timestamp_sent} so each reminder fires once.
    reminders_sent_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    outcome_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(150), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    attendees: Mapped[list["MeetingAttendee"]] = relationship(
        "MeetingAttendee", back_populates="meeting", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self):
        return f"<Meeting(id={self.id}, title={self.title!r}, starts_at={self.starts_at})>"


class MeetingAttendee(Base):
    __tablename__ = "meeting_attendees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # invited | confirmed | declined
    status: Mapped[str] = mapped_column(String(30), default="invited", nullable=False)
    notified: Mapped[bool] = mapped_column(Integer, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    meeting: Mapped["Meeting"] = relationship("Meeting", back_populates="attendees")
    contact: Mapped["Contact"] = relationship("Contact", lazy="selectin")

    def __repr__(self):
        return f"<MeetingAttendee(meeting={self.meeting_id}, contact={self.contact_id})>"
