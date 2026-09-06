"""Pydantic schemas for the contact variable registry."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.utils.templating import normalize_key


def _valid_shortcode(value: str) -> str:
    normalized = normalize_key(value)
    if not normalized:
        raise ValueError("Short code must contain at least one letter or number")
    if len(normalized) > 150:
        raise ValueError("Short code is too long")
    return normalized


class VariableUpdate(BaseModel):
    """Everything an operator may change about a discovered variable."""

    label: Optional[str] = Field(default=None, max_length=200)
    shortcode: Optional[str] = Field(default=None, max_length=150)
    fallback_text: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("shortcode")
    @classmethod
    def check_shortcode(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _valid_shortcode(value)


class VariableCreate(BaseModel):
    """Manually declare a variable before any CSV has introduced it."""

    field_key: str = Field(..., max_length=150)
    label: Optional[str] = Field(default=None, max_length=200)
    shortcode: Optional[str] = Field(default=None, max_length=150)
    fallback_text: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = None

    @field_validator("field_key")
    @classmethod
    def check_field_key(cls, value: str) -> str:
        return _valid_shortcode(value)

    @field_validator("shortcode")
    @classmethod
    def check_shortcode(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _valid_shortcode(value)


class VariableOut(BaseModel):
    id: int
    field_key: str
    label: str
    shortcode: str
    fallback_text: Optional[str]
    description: Optional[str]
    source: str
    is_active: bool
    contact_count: int
    sample_value: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
