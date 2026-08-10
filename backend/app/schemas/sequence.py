"""
Pydantic schemas for sequences.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SequenceStepCreate(BaseModel):
    step_order: int
    step_type: str  # send_sms, wait, condition, stop
    config: Optional[str] = None
    wait_duration_hours: Optional[int] = None
    template_id: Optional[int] = None
    condition_type: Optional[str] = None
    condition_value: Optional[str] = None
    true_branch_step_order: Optional[int] = None
    false_branch_step_order: Optional[int] = None


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

    model_config = {"from_attributes": True}


class SequenceCreate(BaseModel):
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    steps: list[SequenceStepCreate] = []


class SequenceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    steps: Optional[list[SequenceStepCreate]] = None


class SequenceOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    current_version: int
    is_active: bool
    steps: list[SequenceStepOut] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
