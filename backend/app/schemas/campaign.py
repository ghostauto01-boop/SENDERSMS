"""
Pydantic schemas for campaigns.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator


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
    list_id: Optional[int] = None
    template_id: Optional[int] = None
    # Write a message inline instead of selecting a saved template. When both
    # are provided this wins (see CampaignService.resolve_body).
    message_body: Optional[str] = None
    sequence_id: Optional[int] = None
    gateway_setting_id: Optional[int] = None
    # Optional future launch time. NULL = send when started manually.
    scheduled_start_at: Optional[datetime] = None

    _check_future = field_validator("scheduled_start_at")(_require_future)


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    message_body: Optional[str] = None
    template_id: Optional[int] = None
    list_id: Optional[int] = None
    # Editable after creation too, otherwise the UI silently drops them on PUT.
    sequence_id: Optional[int] = None
    gateway_setting_id: Optional[int] = None
    daily_limit: Optional[int] = None
    hourly_limit: Optional[int] = None
    per_minute_limit: Optional[int] = None
    min_delay: Optional[int] = None
    max_delay: Optional[int] = None
    send_start_hour: Optional[int] = None
    send_end_hour: Optional[int] = None
    allow_weekends: Optional[bool] = None
    scheduled_start_at: Optional[datetime] = None

    _check_future = field_validator("scheduled_start_at")(_require_future)


class CampaignOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    status: str
    list_id: Optional[int]
    template_id: Optional[int]
    message_body: Optional[str]
    sequence_id: Optional[int]
    sequence_version_id: Optional[int]
    gateway_setting_id: Optional[int]
    daily_limit: Optional[int]
    hourly_limit: Optional[int]
    per_minute_limit: Optional[int]
    min_delay: Optional[int]
    max_delay: Optional[int]
    send_start_hour: Optional[int]
    send_end_hour: Optional[int]
    allow_weekends: bool
    total_contacts: int
    messages_sent: int
    messages_delivered: int
    messages_failed: int
    replies: int
    interested: int
    scheduled_at: Optional[datetime]
    scheduled_start_at: Optional[datetime]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    # Every timestamp we store is UTC, but some drivers (SQLite, and any column
    # created without a timezone) hand back a naive datetime. Serialised naive,
    # the browser's `new Date(...)` reads it as *local* time and a campaign set
    # for 15:00 UTC is drawn as 15:00 in the user's zone. Stamp UTC on the way
    # out so the offset is always explicit.
    @field_validator(
        "scheduled_at", "scheduled_start_at", "started_at",
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
