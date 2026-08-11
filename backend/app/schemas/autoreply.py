"""Pydantic schemas for auto-reply rules."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

MatchType = Literal["contains", "exact", "starts", "any"]


class AutoReplyRuleCreate(BaseModel):
    name: str = Field(..., max_length=120)
    keywords: Optional[str] = None
    match_type: MatchType = "contains"
    reply_body: str = Field(..., min_length=1)
    is_enabled: bool = True
    priority: int = 100
    cooldown_minutes: int = Field(default=240, ge=0, le=100000)
    stop_on_match: bool = True

    @field_validator("reply_body")
    @classmethod
    def _body_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reply_body cannot be blank")
        return v


class AutoReplyRuleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    keywords: Optional[str] = None
    match_type: Optional[MatchType] = None
    reply_body: Optional[str] = None
    is_enabled: Optional[bool] = None
    priority: Optional[int] = None
    cooldown_minutes: Optional[int] = Field(default=None, ge=0, le=100000)
    stop_on_match: Optional[bool] = None


class AutoReplyRuleOut(BaseModel):
    id: int
    name: str
    keywords: Optional[str]
    match_type: str
    reply_body: str
    is_enabled: bool
    priority: int
    cooldown_minutes: int
    stop_on_match: bool
    times_triggered: int
    last_triggered_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AutoReplyTestRequest(BaseModel):
    """Dry-run: which rule would answer this text, and what would it say?"""

    body: str


class AutoReplyTestResponse(BaseModel):
    matched: bool
    rule_id: Optional[int] = None
    rule_name: Optional[str] = None
    reply_body: Optional[str] = None
