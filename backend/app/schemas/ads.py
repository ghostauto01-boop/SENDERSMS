"""Pydantic schemas for the SMS Ads Manager."""

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _utc(v: Optional[datetime]) -> Optional[datetime]:
    if v is not None and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v


class CampaignIn(BaseModel):
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    objective: str = "replies"
    daily_limit: Optional[int] = None
    total_limit: Optional[int] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    send_start_hour: Optional[int] = Field(default=None, ge=0, le=23)
    send_end_hour: Optional[int] = Field(default=None, ge=0, le=23)
    send_days: Optional[str] = None
    timezone_name: Optional[str] = None
    drip_mode: str = "off"
    drip_batch_size: int = 1
    drip_interval_minutes: int = 0
    pacing: str = "even"
    continuous: bool = True
    always_on: bool = False
    max_per_contact_per_day: Optional[int] = None
    max_per_contact_per_week: Optional[int] = None
    optimization_mode: str = "manual"
    queued_edit_policy: str = "keep"
    priority: str = "normal"
    test_mode: bool = False


class CampaignPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    objective: Optional[str] = None
    status: Optional[str] = None
    daily_limit: Optional[int] = None
    total_limit: Optional[int] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    send_start_hour: Optional[int] = None
    send_end_hour: Optional[int] = None
    send_days: Optional[str] = None
    timezone_name: Optional[str] = None
    drip_mode: Optional[str] = None
    drip_batch_size: Optional[int] = None
    drip_interval_minutes: Optional[int] = None
    pacing: Optional[str] = None
    continuous: Optional[bool] = None
    always_on: Optional[bool] = None
    max_per_contact_per_day: Optional[int] = None
    max_per_contact_per_week: Optional[int] = None
    optimization_mode: Optional[str] = None
    queued_edit_policy: Optional[str] = None
    priority: Optional[str] = None
    test_mode: Optional[bool] = None


class CampaignOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    objective: str
    status: str
    daily_limit: Optional[int]
    total_limit: Optional[int]
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    send_start_hour: Optional[int]
    send_end_hour: Optional[int]
    send_days: Optional[str]
    timezone_name: Optional[str]
    drip_mode: str
    drip_batch_size: int
    drip_interval_minutes: int
    pacing: str
    continuous: bool
    always_on: bool
    max_per_contact_per_day: Optional[int]
    max_per_contact_per_week: Optional[int]
    optimization_mode: str
    queued_edit_policy: str
    priority: str
    test_mode: bool
    sent_count: int
    last_state: Optional[str]
    last_activity_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    _tz = field_validator(
        "start_date", "end_date", "last_activity_at", "created_at", "updated_at", mode="after"
    )(lambda v: _utc(v))

    model_config = {"from_attributes": True}


class SetIn(BaseModel):
    name: str = Field(..., max_length=255)
    status: str = "active"
    list_ids: Optional[str] = None
    include_tags: Optional[str] = None
    exclude_tags: Optional[str] = None
    include_statuses: Optional[str] = None
    exclude_statuses: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    industry: Optional[str] = None
    activity_filter: Optional[str] = None
    exclude_campaign_ids: Optional[str] = None
    daily_limit: Optional[int] = None
    split_mode: str = "equal"


class SetPatch(SetIn):
    name: Optional[str] = None
    status: Optional[str] = None
    split_mode: Optional[str] = None


class SetOut(BaseModel):
    id: int
    campaign_id: int
    name: str
    status: str
    list_ids: Optional[str]
    include_tags: Optional[str]
    exclude_tags: Optional[str]
    include_statuses: Optional[str]
    exclude_statuses: Optional[str]
    city: Optional[str]
    state: Optional[str]
    industry: Optional[str]
    activity_filter: Optional[str]
    exclude_campaign_ids: Optional[str]
    daily_limit: Optional[int]
    split_mode: str
    created_at: datetime

    model_config = {"from_attributes": True}


class CreativeIn(BaseModel):
    name: str = Field(..., max_length=255)
    body: str = ""
    cta: Optional[str] = None
    tracking_link: Optional[str] = None
    allocation: float = 0.0
    status: str = "active"


class CreativePatch(BaseModel):
    name: Optional[str] = None
    body: Optional[str] = None
    cta: Optional[str] = None
    tracking_link: Optional[str] = None
    allocation: Optional[float] = None
    status: Optional[str] = None


class CreativeOut(BaseModel):
    id: int
    set_id: int
    campaign_id: int
    name: str
    status: str
    body: str
    cta: Optional[str]
    tracking_link: Optional[str]
    allocation: float
    current_version: int
    is_deleted: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class FollowUpStepIn(BaseModel):
    step_order: int = 1
    name: Optional[str] = None
    wait_hours: int = 48
    condition: str = "no_reply"
    action: str = "send_sms"
    body: Optional[str] = None
    action_value: Optional[str] = None
    is_active: bool = True


class AddContactsIn(BaseModel):
    set_id: Optional[int] = None
    list_ids: list[int] = []
    contact_ids: list[int] = []
    tags: list[str] = []


class CalendarEventIn(BaseModel):
    title: str
    event_type: str = "meeting"
    contact_id: Optional[int] = None
    campaign_id: Optional[int] = None
    starts_at: datetime
    duration_minutes: int = 30
    notes: Optional[str] = None
    priority: str = "normal"
    reminder_minutes: Optional[int] = None


class SuppressionIn(BaseModel):
    phone_number: Optional[str] = None
    contact_id: Optional[int] = None
    reason: str = "Manual suppression"
    notes: Optional[str] = None


class ContactActionIn(BaseModel):
    action: str
    value: Optional[str] = None
    note: Optional[str] = None
    stop_campaigns: bool = False
    suppress: bool = False


class FollowUpTaskIn(BaseModel):
    contact_id: int
    campaign_id: Optional[int] = None
    due_at: Optional[datetime] = None
    note: Optional[str] = None
    body: Optional[str] = None
    priority: str = "normal"


class BulkActionIn(BaseModel):
    ids: list[int]
    action: str
    value: Optional[str] = None
