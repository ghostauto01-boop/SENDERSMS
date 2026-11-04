"""
Pydantic schemas for campaigns.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator


SENDER_DISTRIBUTIONS = ("round_robin", "weighted", "random")


def _clean_sender_distribution(value):
    if value is None:
        return "round_robin"
    mode = str(value).strip().lower()
    if mode not in SENDER_DISTRIBUTIONS:
        raise ValueError(
            f"sender_distribution must be one of: {', '.join(SENDER_DISTRIBUTIONS)}"
        )
    return mode


def _require_future(value: Optional[datetime]) -> Optional[datetime]:
    """Reject launch times in the past.

    A naive datetime is read as UTC: the frontend sends an ISO string with an
    offset, but a hand-written API call might not, and silently treating it as
    server-local time would fire the campaign at the wrong hour.
    """
    if value is None:
        return None
    dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    # Small grace window so "now" from a clock a few seconds behind is accepted.
    if dt < datetime.now(timezone.utc) - timedelta(minutes=1):
        raise ValueError("scheduled_start_at must be in the future")
    return dt


class CampaignCreate(BaseModel):
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    #: "sms" (default) or "email" — an email campaign can use a Brevo sender
    #: or any connected Gmail mailbox.
    channel: str = "sms"
    list_id: Optional[int] = None
    template_id: Optional[int] = None
    # Write a message inline instead of selecting a saved template. When both
    # are provided this wins (see CampaignService.resolve_body).
    message_body: Optional[str] = None
    sequence_id: Optional[int] = None
    gateway_setting_id: Optional[int] = None
    # Optional future launch time. NULL = send when started manually.
    scheduled_start_at: Optional[datetime] = None

    # --- Optional campaign-scoped pacing (NULL means unrestricted) -------
    daily_limit: Optional[int] = Field(default=None, ge=1)
    hourly_limit: Optional[int] = Field(default=None, ge=1)
    per_minute_limit: Optional[int] = Field(default=None, ge=1)
    min_delay: Optional[int] = Field(default=None, ge=0)
    max_delay: Optional[int] = Field(default=None, ge=0)
    send_start_hour: Optional[int] = Field(default=None, ge=0, le=23)
    send_end_hour: Optional[int] = Field(default=None, ge=0, le=23)
    allow_weekends: bool = True
    timezone_name: Optional[str] = Field(default=None, max_length=120)
    allowed_weekdays: Optional[str] = Field(default=None, max_length=30)

    # --- Email channel -------------------------------------------------
    #: Unified sender id: positive Brevo, negative connected mailbox (NULL = default).
    email_account_id: Optional[int] = None
    #: Used automatically when the primary sender rejects a send.
    fallback_email_account_id: Optional[int] = None
    email_account_ids: Optional[list[int]] = None
    sender_distribution: str = "round_robin"
    email_sender_weights: Optional[dict] = None
    sender_daily_limits: Optional[dict] = None
    subject: Optional[str] = Field(default=None, max_length=500)
    html_body: Optional[str] = None
    #: [{"name": "...", "content_base64": "..."}] attached to every email.
    attachments: Optional[list[dict]] = None
    track_opens: bool = True
    track_clicks: bool = True
    plain_personal_email: bool = False

    _check_future = field_validator("scheduled_start_at")(_require_future)
    _check_distribution = field_validator("sender_distribution")(_clean_sender_distribution)


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    message_body: Optional[str] = None
    template_id: Optional[int] = None
    list_id: Optional[int] = None
    # Editable after creation too, otherwise the UI silently drops them on PUT.
    sequence_id: Optional[int] = None
    gateway_setting_id: Optional[int] = None
    daily_limit: Optional[int] = Field(default=None, ge=1)
    hourly_limit: Optional[int] = Field(default=None, ge=1)
    per_minute_limit: Optional[int] = Field(default=None, ge=1)
    min_delay: Optional[int] = Field(default=None, ge=0)
    max_delay: Optional[int] = Field(default=None, ge=0)
    send_start_hour: Optional[int] = Field(default=None, ge=0, le=23)
    send_end_hour: Optional[int] = Field(default=None, ge=0, le=23)
    allow_weekends: Optional[bool] = None
    timezone_name: Optional[str] = Field(default=None, max_length=120)
    allowed_weekdays: Optional[str] = Field(default=None, max_length=30)
    scheduled_start_at: Optional[datetime] = None
    #: Only changeable while the campaign has no messages queued.
    channel: Optional[str] = None
    email_account_id: Optional[int] = None
    fallback_email_account_id: Optional[int] = None
    email_account_ids: Optional[list[int]] = None
    sender_distribution: Optional[str] = None
    email_sender_weights: Optional[dict] = None
    sender_daily_limits: Optional[dict] = None
    subject: Optional[str] = Field(default=None, max_length=500)
    html_body: Optional[str] = None
    attachments: Optional[list[dict]] = None
    track_opens: Optional[bool] = None
    track_clicks: Optional[bool] = None
    plain_personal_email: Optional[bool] = None

    _check_future = field_validator("scheduled_start_at")(_require_future)
    _check_distribution = field_validator("sender_distribution")(_clean_sender_distribution)


class CampaignOut(BaseModel):
    id: int
    #: Which campaign system this id belongs to. The classic campaigns and the Ads
    #: Manager number independently, so an id is only meaningful with its kind.
    kind: str = "campaign"
    name: str
    description: Optional[str] = None
    status: str = "draft"
    #: Set while ``status == "paused"``: "scheduled" if it was paused before it
    #: ever started (resume restores the schedule), otherwise null.
    paused_from: Optional[str] = None
    paused_at: Optional[datetime] = None
    #: Why it is paused. Only operators pause campaigns, so this always says why.
    paused_reason: Optional[str] = None
    channel: str = "sms"
    email_account_id: Optional[int] = None
    fallback_email_account_id: Optional[int] = None
    email_account_ids: Optional[list[int]] = None
    sender_distribution: str = "round_robin"
    email_sender_weights: Optional[dict] = None
    sender_daily_limits: Optional[dict] = None
    subject: Optional[str] = None
    html_body: Optional[str] = None
    attachments: Optional[list[dict]] = None
    track_opens: bool = True
    track_clicks: bool = True
    plain_personal_email: bool = False
    list_id: Optional[int] = None
    template_id: Optional[int] = None
    message_body: Optional[str] = None
    sequence_id: Optional[int] = None
    sequence_version_id: Optional[int] = None
    gateway_setting_id: Optional[int] = None
    daily_limit: Optional[int] = None
    hourly_limit: Optional[int] = None
    per_minute_limit: Optional[int] = None
    min_delay: Optional[int] = None
    max_delay: Optional[int] = None
    send_start_hour: Optional[int] = None
    send_end_hour: Optional[int] = None
    allow_weekends: bool = True
    timezone_name: Optional[str] = None
    allowed_weekdays: Optional[str] = None
    total_contacts: int = 0
    messages_sent: int = 0
    messages_delivered: int = 0
    messages_failed: int = 0
    replies: int = 0
    interested: int = 0
    scheduled_at: Optional[datetime] = None
    scheduled_start_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    @field_validator("track_opens", "track_clicks", "allow_weekends", mode="before")
    @classmethod
    def _coerce_null_true(cls, v):
        return True if v is None else v

    @field_validator("plain_personal_email", mode="before")
    @classmethod
    def _coerce_null_false(cls, v):
        return False if v is None else v

    @field_validator(
        "total_contacts",
        "messages_sent",
        "messages_delivered",
        "messages_failed",
        "replies",
        "interested",
        mode="before",
    )
    @classmethod
    def _coerce_null_zero(cls, v):
        return 0 if v is None else v

    @field_validator("channel", mode="before")
    @classmethod
    def _coerce_null_channel(cls, v):
        return "sms" if v is None else v

    @field_validator("sender_distribution", mode="before")
    @classmethod
    def _coerce_null_dist(cls, v):
        return "round_robin" if v is None else v

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_null_status(cls, v):
        return "draft" if v is None else v

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _coerce_null_timestamps(cls, v):
        return datetime.now(timezone.utc) if v is None else v

    # Every timestamp we store is UTC, but some drivers (SQLite, and any column
    # created without a timezone) hand back a naive datetime. Serialised naive,
    # the browser's `new Date(...)` reads it as *local* time and a campaign set
    # for 15:00 UTC is drawn as 15:00 in the user's zone. Stamp UTC on the way
    # out so the offset is always explicit.
    @field_validator(
        "scheduled_at", "scheduled_start_at", "started_at", "paused_at",
        "completed_at", "created_at", "updated_at",
        mode="after",
    )
    @classmethod
    def _as_utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    model_config = {"from_attributes": True}


class CampaignStats(BaseModel):
    id: int
    name: str
    status: str
    total_contacts: int
    messages_sent: int
    messages_delivered: int
    messages_failed: int
    replies: int
    interested: int
    delivery_rate: float
    reply_rate: float
    started_at: Optional[str]
    completed_at: Optional[str]


class CampaignScheduleRequest(BaseModel):
    """Set (or clear) a campaign's automatic launch time."""

    scheduled_start_at: Optional[datetime] = None

    _check_future = field_validator("scheduled_start_at")(_require_future)
