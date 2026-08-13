"""Pydantic schemas for manually scheduled follow-ups."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class FollowUpCreate(BaseModel):
    """A follow-up SMS created by an operator.

    Sequence-generated follow-ups do not use this schema because their message
    and contact come from the campaign sequence.
    """

    contact_id: int = Field(..., gt=0)
    scheduled_at: datetime
    message_text: str = Field(..., min_length=1, max_length=5000)
    notify_on_due: bool = False
    max_attempts: int = Field(default=3, ge=1, le=10)

    @field_validator("scheduled_at")
    @classmethod
    def scheduled_at_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_at must include a timezone")
        return value

    @field_validator("message_text")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message_text must not be blank")
        return value
