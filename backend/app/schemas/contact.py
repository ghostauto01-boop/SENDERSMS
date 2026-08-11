"""
Pydantic schemas for contacts.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ContactCreate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    business_name: Optional[str] = None
    phone_number: str = Field(..., max_length=20)
    email: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: str = "Nigeria"
    website: Optional[str] = None
    industry: Optional[str] = None
    source: Optional[str] = None
    lead_status: str = "new"
    notes: Optional[str] = None
    custom_fields: Optional[str] = None


class ContactUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    business_name: Optional[str] = None
    email: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    source: Optional[str] = None
    lead_status: Optional[str] = None
    notes: Optional[str] = None
    custom_fields: Optional[str] = None
    has_consented: Optional[bool] = None
    is_opted_out: Optional[bool] = None


class ContactOut(BaseModel):
    id: int
    first_name: Optional[str]
    last_name: Optional[str]
    business_name: Optional[str]
    phone_number: str
    email: Optional[str]
    city: Optional[str]
    state: Optional[str]
    country: str
    website: Optional[str]
    industry: Optional[str]
    source: Optional[str]
    lead_status: str
    consent_status: str
    has_consented: bool
    is_opted_out: bool
    # Opt-out audit trail. These were recorded in the DB but never returned by
    # the API, so the UI could not show WHY or WHEN a contact opted out.
    opt_out_reason: Optional[str] = None
    opted_out_at: Optional[datetime] = None
    notes: Optional[str]
    custom_fields: Optional[str]
    messages_sent: int
    messages_received: int
    last_contacted_at: Optional[datetime]
    last_reply_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ContactListOut(BaseModel):
    total: int
    items: list[ContactOut]


class BulkAction(BaseModel):
    contact_ids: list[int]
    action: str  # tag, status, delete
    value: Optional[str] = None  # tag name or status value


class CSVImportRequest(BaseModel):
    column_mapping: dict[str, str]
    list_id: Optional[int] = None
    skip_duplicates: bool = True
