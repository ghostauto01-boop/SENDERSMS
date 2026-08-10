"""
Pydantic schemas for templates.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TemplateCreate(BaseModel):
    name: str = Field(..., max_length=255)
    category: Optional[str] = None
    body: str


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    body: Optional[str] = None
    is_active: Optional[bool] = None


class TemplateOut(BaseModel):
    id: int
    name: str
    category: Optional[str]
    body: str
    char_count: int
    segment_count: int
    is_active: bool
    use_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
