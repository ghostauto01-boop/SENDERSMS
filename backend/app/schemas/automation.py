"""Pydantic schemas for reply automations."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

CONDITION_FIELDS = {"sentiment", "intent", "label", "keyword", "has_tag", "any"}
ACTION_TYPES = {
    "send_sms", "send_email", "stop_sequence", "opt_out", "add_tag", "remove_tag",
    "set_status", "delete_contact",
}


class AutomationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    description: Optional[str] = None
    is_enabled: bool = True
    priority: int = 100
    trigger_type: str = "inbound_reply"
    #: "sms" (default) or "email" — automations only fire on their own channel.
    channel: str = "sms"
    email_account_id: Optional[int] = None
    conditions: list[dict[str, Any]] = []
    match_all: bool = True
    actions: list[dict[str, Any]] = []

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name cannot be blank")
        return v

    @field_validator("conditions")
    @classmethod
    def _conditions_valid(cls, v: list) -> list:
        for c in v:
            field = c.get("field")
            if field not in CONDITION_FIELDS:
                raise ValueError(f"Unknown condition field: {field}")
        return v

    @field_validator("actions")
    @classmethod
    def _actions_valid(cls, v: list) -> list:
        for a in v:
            if a.get("type") not in ACTION_TYPES:
                raise ValueError(f"Unknown action type: {a.get('type')}")
            if a.get("type") in ("send_sms", "send_email") and not (a.get("body") or "").strip():
                raise ValueError(f"{a.get('type')} action needs a message body")
        return v


class AutomationUpdate(BaseModel):
    name: Optional[str] = None
    channel: Optional[str] = None
    email_account_id: Optional[int] = None
    description: Optional[str] = None
    is_enabled: Optional[bool] = None
    priority: Optional[int] = None
    trigger_type: Optional[str] = None
    conditions: Optional[list[dict[str, Any]]] = None
    match_all: Optional[bool] = None
    actions: Optional[list[dict[str, Any]]] = None


class AutomationOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    is_enabled: bool
    priority: int
    trigger_type: str
    channel: str = "sms"
    email_account_id: Optional[int] = None
    conditions: list[dict[str, Any]]
    match_all: bool
    actions: list[dict[str, Any]]
    times_triggered: int
    last_triggered_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
