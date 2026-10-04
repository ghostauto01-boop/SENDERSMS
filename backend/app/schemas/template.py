"""
Pydantic schemas for templates (SMS and email).
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TemplateCreate(BaseModel):
    name: str = Field(..., max_length=255)
    category: Optional[str] = None
    body: str = ""
    #: "sms" (default) or "email".
    channel: str = "sms"
    # Email-only fields.
    subject: Optional[str] = Field(default=None, max_length=500)
    html_body: Optional[str] = None
    preheader: Optional[str] = Field(default=None, max_length=500)
    email_account_id: Optional[int] = None
    #: Files sent with every use of this template: [{"name", "content_base64"}].
    attachments: Optional[list[dict]] = None
    #: Add the List-Unsubscribe header (required by Gmail/Yahoo bulk rules).
    include_unsubscribe: bool = True


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    body: Optional[str] = None
    is_active: Optional[bool] = None
    subject: Optional[str] = Field(default=None, max_length=500)
    html_body: Optional[str] = None
    preheader: Optional[str] = Field(default=None, max_length=500)
    email_account_id: Optional[int] = None
    attachments: Optional[list[dict]] = None
    include_unsubscribe: Optional[bool] = None


class TemplateOut(BaseModel):
    id: int
    name: str
    category: Optional[str]
    channel: str = "sms"
    body: str
    subject: Optional[str] = None
    html_body: Optional[str] = None
    preheader: Optional[str] = None
    email_account_id: Optional[int] = None
    #: Attachment metadata only — the base64 payload is never sent to the UI.
    attachments: list[dict] = []
    include_unsubscribe: bool = True
    char_count: int
    segment_count: int
    is_active: bool
    use_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
