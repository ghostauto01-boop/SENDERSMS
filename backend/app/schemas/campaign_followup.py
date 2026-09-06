"""Schemas for campaign follow-up rules."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class CampaignFollowUpBase(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    message_text: Optional[str] = Field(default=None, max_length=5000)
    template_id: Optional[int] = None
    delay_minutes: int = Field(default=1440, ge=1, le=60 * 24 * 90)
    stop_on_reply: bool = True
    stop_on_opt_out: bool = True
    stop_on_lead_status: Optional[str] = Field(default=None, max_length=500)
    send_start_hour: Optional[int] = Field(default=None, ge=0, le=23)
    send_end_hour: Optional[int] = Field(default=None, ge=0, le=23)
    is_active: bool = True

    @field_validator("message_text")
    @classmethod
    def strip_message(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None


class CampaignFollowUpCreate(CampaignFollowUpBase):
    step_order: Optional[int] = Field(default=None, ge=1, le=50)


class CampaignFollowUpUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    message_text: Optional[str] = Field(default=None, max_length=5000)
    template_id: Optional[int] = None
    delay_minutes: Optional[int] = Field(default=None, ge=1, le=60 * 24 * 90)
    step_order: Optional[int] = Field(default=None, ge=1, le=50)
    stop_on_reply: Optional[bool] = None
    stop_on_opt_out: Optional[bool] = None
    stop_on_lead_status: Optional[str] = Field(default=None, max_length=500)
    send_start_hour: Optional[int] = Field(default=None, ge=0, le=23)
    send_end_hour: Optional[int] = Field(default=None, ge=0, le=23)
    is_active: Optional[bool] = None


class CampaignFollowUpOut(BaseModel):
    id: int
    campaign_id: int
    step_order: int
    name: Optional[str]
    message_text: Optional[str]
    template_id: Optional[int]
    delay_minutes: int
    stop_on_reply: bool
    stop_on_opt_out: bool
    stop_on_lead_status: Optional[str]
    send_start_hour: Optional[int]
    send_end_hour: Optional[int]
    is_active: bool
    sent_count: int
    stopped_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
