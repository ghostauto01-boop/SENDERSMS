"""SMS Ads Manager models.

An ADDITIVE layer on top of the existing application. Nothing here replaces the
existing ``campaigns`` / ``campaign_contacts`` tables, the bulk-send path, the
contact / list / tag tables or the suppression list -- all of those are reused
by reference.

Hierarchy
---------
    AdsCampaign -> AdsSet -> AdsCreative -> AdsCreativeVersion
                       \\-> AdsAssignment (one contact, one creative)
                              \\-> AdsQueueItem -> messages (existing table)

Every table is prefixed ``ads_`` so it can never collide with anything that
already exists, and so a DBA can see at a glance which tables belong to the new
feature.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Campaign
# --------------------------------------------------------------------------

CAMPAIGN_STATUSES = (
    "draft",
    "scheduled",
    "active",
    "paused",
    "completed",
    "archived",
    "error",
)

#: Transient, system-computed states surfaced to the UI. They are NOT stored in
#: ``status`` -- a campaign is "active" and merely *waiting*; storing these
#: would lose the user's intent when the condition clears.
SYSTEM_STATES = (
    "preparing_audience",
    "waiting_for_schedule",
    "sending",
    "daily_limit_reached",
    "outside_sending_window",
    "insufficient_balance",
    "no_eligible_contacts",
    "needs_attention",
)

OBJECTIVES = (
    "replies",
    "leads",
    "meetings",
    "website_visits",
    "promotion",
    "reengagement",
    "followup",
    "retention",
    "review",
    "custom",
)


class AdsCampaign(Base):
    __tablename__ = "ads_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    objective: Mapped[str] = mapped_column(String(40), default="replies", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False, index=True)

    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Budget (SMS volume, not money) ---
    daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Schedule ---
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    send_start_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    send_end_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: CSV of weekday numbers (0=Mon .. 6=Sun). Empty/NULL means every day.
    send_days: Mapped[str | None] = mapped_column(String(30), nullable=True)
    timezone_name: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # --- Drip / pacing ---
    #: off | interval | batch | daily | smart
    drip_mode: Mapped[str] = mapped_column(String(20), default="off", nullable=False)
    drip_batch_size: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    drip_interval_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: even | front | back | random  (smart pacing distribution)
    pacing: Mapped[str] = mapped_column(String(20), default="even", nullable=False)

    # --- Continuous / always-on ---
    continuous: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    always_on: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Frequency guards (campaign level; never loosens global rules) ---
    max_per_contact_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_per_contact_per_week: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Optimization ---
    #: manual | recommend | auto
    optimization_mode: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)

    # --- Andromeda auto-optimization (OPT-IN master switch) ---
    #: OFF by default: nothing automatic ever happens until the user turns it
    #: on. When ON, winning creatives automatically receive the remaining
    #: contacts (spend) and losers are deprioritised, per the strategy below.
    auto_optimize: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: score | reply_rate | positive_reply_rate | conversion_rate
    optimize_metric: Mapped[str] = mapped_column(String(30), default="score", nullable=False)
    #: Minimum sends a creative needs before it can win or lose automatically.
    optimize_min_sends: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    #: Winner must beat the loser by at least this percent on the metric.
    optimize_min_gap_pct: Mapped[float] = mapped_column(Float, default=25.0, nullable=False)
    #: shift | shift_and_pause -- move pending contacts to the winner, and
    #: optionally pause the losing creatives as well.
    optimize_action: Mapped[str] = mapped_column(String(20), default="shift", nullable=False)
    optimize_last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Safety / behaviour switches ---
    test_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: keep | update -- what happens to already-queued messages when a creative
    #: is edited. Default keeps the version the contact was assigned.
    queued_edit_policy: Mapped[str] = mapped_column(String(10), default="keep", nullable=False)
    #: high | normal | low
    priority: Mapped[str] = mapped_column(String(10), default="normal", nullable=False)

    # --- Cached counters (authoritative numbers come from the queue) ---
    sent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_state_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    sets: Mapped[list["AdsSet"]] = relationship(
        "AdsSet", back_populates="campaign", cascade="all, delete-orphan"
    )

    def weekday_allowed(self, weekday: int) -> bool:
        if not self.send_days:
            return True
        allowed = {int(x) for x in self.send_days.split(",") if x.strip().isdigit()}
        return not allowed or weekday in allowed

    def __repr__(self):  # pragma: no cover
        return f"<AdsCampaign(id={self.id}, name={self.name}, status={self.status})>"


# --------------------------------------------------------------------------
# SMS Set (audience + delivery configuration)
# --------------------------------------------------------------------------


class AdsSet(Base):
    __tablename__ = "ads_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ads_campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: draft | active | paused | archived
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, index=True)

    # --- Targeting. All reference EXISTING contact/list/tag tables. ---
    #: CSV of contact_lists.id. Empty selects NOTHING -- a set with no lists,
    #: no contacts, no saved audience and no filters matches zero contacts.
    #: (This used to mean "all contacts", which pulled entire databases into
    #: campaigns by accident.)
    list_ids: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    #: CSV of explicitly picked contact ids. Unioned with the list members,
    #: then filtered by the rules below.
    contact_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Optional saved (Meta-style) audience this set is built from. The
    #: audience's lists/contacts/filters are merged with the set's own.
    audience_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ads_audiences.id", ondelete="SET NULL"), nullable=True, index=True
    )
    include_tags: Mapped[str | None] = mapped_column(Text, nullable=True)  # CSV of tag names
    exclude_tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    include_statuses: Mapped[str | None] = mapped_column(String(500), nullable=True)
    exclude_statuses: Mapped[str | None] = mapped_column(String(500), nullable=True)
    city: Mapped[str | None] = mapped_column(String(150), nullable=True)
    state: Mapped[str | None] = mapped_column(String(150), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: never_contacted | contacted | replied | not_replied | any
    activity_filter: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: Exclude anyone who is already in these ads campaigns (CSV of ids).
    exclude_campaign_ids: Mapped[str | None] = mapped_column(String(500), nullable=True)

    daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: equal | percentage | weighted | random | quota
    #: "quota" = each creative sends exactly its send_quota SMS (uncapped
    #: creatives split the remainder equally).
    split_mode: Mapped[str] = mapped_column(String(20), default="equal", nullable=False)

    #: Per-set placement toggle for Andromeda auto-optimization. Only takes
    #: effect when the campaign's master ``auto_optimize`` switch is ON, so a
    #: set can be excluded from automatic traffic shifts while the rest of the
    #: campaign keeps optimising.
    auto_optimize: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    campaign: Mapped["AdsCampaign"] = relationship("AdsCampaign", back_populates="sets")
    creatives: Mapped[list["AdsCreative"]] = relationship(
        "AdsCreative", back_populates="ads_set", cascade="all, delete-orphan"
    )

    def csv(self, field: str) -> list[str]:
        raw = getattr(self, field) or ""
        return [p.strip() for p in raw.split(",") if p.strip()]


# --------------------------------------------------------------------------
# Saved Audience (Meta-style reusable audience)
# --------------------------------------------------------------------------


class AdsAudience(Base):
    """A named, reusable audience: lists + contacts + filters in one place.

    Created on the Audiences page, then attached to any campaign's SMS set.
    Attaching copies the definition onto the set (and links it via
    ``AdsSet.audience_id``), so later edits of the saved audience can be
    re-attached without rebuilding the targeting by hand.
    """

    __tablename__ = "ads_audiences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: CSV of contact_lists.id.
    list_ids: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    #: CSV of explicitly picked contact ids.
    contact_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    include_tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    exclude_tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    include_statuses: Mapped[str | None] = mapped_column(String(500), nullable=True)
    exclude_statuses: Mapped[str | None] = mapped_column(String(500), nullable=True)
    city: Mapped[str | None] = mapped_column(String(150), nullable=True)
    state: Mapped[str | None] = mapped_column(String(150), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: never_contacted | contacted | replied | not_replied | any
    activity_filter: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: Exclude anyone who is already in these ads campaigns (CSV of ids).
    exclude_campaign_ids: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    def csv(self, field: str) -> list[str]:
        raw = getattr(self, field) or ""
        return [p.strip() for p in raw.split(",") if p.strip()]

    def __repr__(self):  # pragma: no cover
        return f"<AdsAudience(id={self.id}, name={self.name})>"


# --------------------------------------------------------------------------
# Creative + immutable versions
# --------------------------------------------------------------------------


class AdsCreative(Base):
    __tablename__ = "ads_creatives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    set_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ads_sets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campaign_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: draft | active | paused | archived  (+ soft delete flag below)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cta: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tracking_link: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: Optional saved template this creative was started from. It records where
    #: the text came from so the composer can show "synced to template" and
    #: offer a one-click re-sync when the template later changes. It is a
    #: pointer only -- the creative's own text/versions stay the source of
    #: truth for what actually gets sent.
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    #: Relative share of the split. With split_mode="percentage" this is a
    #: percent (must total 100); with "weighted" it is an arbitrary weight.
    allocation: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    #: Exact SMS quota for this creative (split_mode="quota"): "send exactly N
    #: SMS with this creative". NULL/0 means no cap — the creative shares
    #: whatever is left after capped creatives take their quota.
    send_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)

    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    ads_set: Mapped["AdsSet"] = relationship("AdsSet", back_populates="creatives")
    versions: Mapped[list["AdsCreativeVersion"]] = relationship(
        "AdsCreativeVersion", back_populates="creative", cascade="all, delete-orphan"
    )


class AdsCreativeVersion(Base):
    """Immutable snapshot of a creative's text.

    Editing a creative NEVER rewrites history: a new version row is written and
    future assignments point at it. Messages already sent keep the version they
    were sent with, so analytics stay attributable.
    """

    __tablename__ = "ads_creative_versions"
    __table_args__ = (UniqueConstraint("creative_id", "version", name="uq_ads_creative_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    creative_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ads_creatives.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    cta: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    creative: Mapped["AdsCreative"] = relationship("AdsCreative", back_populates="versions")


# --------------------------------------------------------------------------
# Assignment: one contact <-> one creative, for the life of the experiment
# --------------------------------------------------------------------------


class AdsAssignment(Base):
    __tablename__ = "ads_assignments"
    __table_args__ = (
        # THE duplicate-protection constraint: a contact can exist at most once
        # per campaign, enforced by the database, not by application logic.
        UniqueConstraint("campaign_id", "contact_id", name="uq_ads_assignment_contact"),
        Index("ix_ads_assignment_dispatch", "campaign_id", "send_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ads_campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    set_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    creative_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    creative_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Normalized phone captured at assignment time (dedupe + audit trail).
    phone_number: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)

    #: equal | percentage | weighted | random | manual
    assignment_method: Mapped[str] = mapped_column(String(20), default="equal", nullable=False)
    experiment_group: Mapped[str | None] = mapped_column(String(60), nullable=True)

    #: pending | ready | sending | sent | delivered | failed | skipped | cancelled | blocked
    send_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    skip_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    delivery_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reply_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    followup_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    conversion_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------
# Follow-up automation
# --------------------------------------------------------------------------


class AdsFollowUpStep(Base):
    """One step of a campaign's follow-up workflow."""

    __tablename__ = "ads_followup_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ads_campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_order: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    wait_hours: Mapped[int] = mapped_column(Integer, default=48, nullable=False)
    #: no_reply | replied | positive | negative | interested | not_interested |
    #: link_clicked | link_not_clicked | meeting_scheduled | converted | always
    condition: Mapped[str] = mapped_column(String(30), default="no_reply", nullable=False)
    #: send_sms | add_tag | remove_tag | change_status | create_task | stop | suppress | notify
    action: Mapped[str] = mapped_column(String(30), default="send_sms", nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class AdsFollowUpTask(Base):
    """A pending follow-up for one contact -- the Follow-Up Center's rows."""

    __tablename__ = "ads_followup_tasks"
    __table_args__ = (
        UniqueConstraint("step_id", "contact_id", name="uq_ads_followup_step_contact"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    step_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    assignment_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False, index=True)
    #: pending | waiting_reply | completed | cancelled | skipped
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(10), default="normal", nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------


class AdsCalendarEvent(Base):
    __tablename__ = "ads_calendar_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    #: meeting | call | followup | task | reminder
    event_type: Mapped[str] = mapped_column(String(20), default="meeting", nullable=False, index=True)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(10), default="normal", nullable=False)
    reminder_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: scheduled | done | cancelled
    status: Mapped[str] = mapped_column(String(20), default="scheduled", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


# --------------------------------------------------------------------------
# Events + activity log
# --------------------------------------------------------------------------


class AdsEvent(Base):
    """Append-only analytics event stream (MESSAGE_SENT, REPLY_RECEIVED, ...)."""

    __tablename__ = "ads_events"
    __table_args__ = (Index("ix_ads_events_campaign_type", "campaign_id", "event_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    set_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    creative_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    creative_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    assignment_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False, index=True
    )


class AdsActivityLog(Base):
    """Human-readable audit trail: who did what, when."""

    __tablename__ = "ads_activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String(120), default="system", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False, index=True
    )
