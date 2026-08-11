"""
Pydantic schemas for campaigns.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


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
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

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
