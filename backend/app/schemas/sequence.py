"""Pydantic schemas for sequences."""

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


StepType = Literal["send_sms", "send_email", "wait", "condition", "stop"]
#: Channel a sequence runs on. One channel per sequence, like a campaign.
SequenceChannel = Literal["sms", "email"]
ConditionType = Literal[
    "contact_replied",
    "contact_did_not_reply",
    "message_delivered",
    "message_failed",
    "contact_opted_out",
]


class SequenceStepCreate(BaseModel):
    step_order: int = Field(..., ge=0, le=49)
    step_type: StepType
    config: Optional[str] = Field(default=None, max_length=10000)
    wait_duration_hours: Optional[int] = Field(default=None, ge=1, le=8760)
    template_id: Optional[int] = Field(default=None, gt=0)
    condition_type: Optional[ConditionType] = None
    condition_value: Optional[str] = Field(default=None, max_length=255)
    true_branch_step_order: Optional[int] = Field(default=None, ge=0, le=49)
    false_branch_step_order: Optional[int] = Field(default=None, ge=0, le=49)

    # ---- Email steps -----------------------------------------------------
    #: Subject line for a send_email step (required by the service layer).
    subject: Optional[str] = Field(default=None, max_length=500)
    #: Optional sender: Brevo account id (positive) or Gmail mailbox id (negative).
    email_account_id: Optional[int] = None

    @model_validator(mode="after")
    def validate_type_fields(self):
        if self.step_type == "wait" and self.wait_duration_hours is None:
            raise ValueError("Wait steps require wait_duration_hours")
        if self.step_type == "condition" and self.condition_type is None:
            raise ValueError("Condition steps require condition_type")
        if self.step_type == "send_email" and not (self.subject or "").strip():
            raise ValueError("Send email steps require a subject")
        return self


class SequenceStepOut(BaseModel):
    id: int
    step_order: int
    step_type: str
    config: Optional[str]
    wait_duration_hours: Optional[int]
    template_id: Optional[int]
    condition_type: Optional[str]
    condition_value: Optional[str]
    true_branch_step_order: Optional[int]
    false_branch_step_order: Optional[int]
    subject: Optional[str] = None
    email_account_id: Optional[int] = None

    model_config = {"from_attributes": True}


class SequenceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    #: sms (default, and what every existing sequence is) or email.
    channel: SequenceChannel = "sms"
    steps: list[SequenceStepCreate] = Field(default_factory=list, min_length=1, max_length=50)


class SequenceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    channel: Optional[SequenceChannel] = None
    steps: Optional[list[SequenceStepCreate]] = Field(default=None, min_length=1, max_length=50)


class SequenceOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    channel: str = "sms"
    current_version: int
    is_active: bool
    steps: list[SequenceStepOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def timestamps_as_utc(self):
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=timezone.utc)
        if self.updated_at.tzinfo is None:
            self.updated_at = self.updated_at.replace(tzinfo=timezone.utc)
        return self

    model_config = {"from_attributes": True}
