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
    #: Optional: a contact is identified by a phone OR an email. Both may be
    #: given. A request with neither is rejected in the route, not here, so the
    #: error message can explain which one is missing.
    phone_number: Optional[str] = Field(default=None, max_length=20)
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
    #: Settable, so a phone-only contact imported from an email file can be
    #: given its number later without a re-import.
    phone_number: Optional[str] = None
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
    # Email channel consent/state (separate from the SMS opt-out above).
    is_email_opted_out: Optional[bool] = None
    email_status: Optional[str] = None


class ContactOut(BaseModel):
    id: int
    first_name: Optional[str]
    last_name: Optional[str]
    business_name: Optional[str]
    #: Null for an email-only contact — the UI shows the address instead.
    phone_number: Optional[str] = None
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
    is_undeliverable: bool = False
    undeliverable_reason: Optional[str] = None
    delivery_fail_count: int = 0
    # Email channel state.
    is_email_opted_out: bool = False
    email_status: str = "active"
    email_opted_out_at: Optional[datetime] = None
    email_opt_out_reason: Optional[str] = None
    is_email_undeliverable: bool = False
    emails_sent: int = 0
    emails_received: int = 0
    last_emailed_at: Optional[datetime] = None
    # Email enrichment provenance. The UI needs all four to answer "can I
    # safely email this?" and "why does this address look odd?" — and to show
    # an unverified guess as an unverified guess.
    email_source: Optional[str] = None
    email_verified: bool = False
    email_verified_at: Optional[datetime] = None
    email_confidence: Optional[int] = None
    email_enriched_at: Optional[datetime] = None
    email_enrichment_note: Optional[str] = None
    # Opt-out audit trail. These were recorded in the DB but never returned by
    # the API, so the UI could not show WHY or WHEN a contact opted out.
    opt_out_reason: Optional[str] = None
    opted_out_at: Optional[datetime] = None
    notes: Optional[str]
    custom_fields: Optional[str]
    # NOTE: `tags` is attached by the contacts API (not read off the ORM
    # relationship) so serialization never triggers an async lazy load.
    tags: list[str] = []
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
    contact_ids: list[int] = Field(default_factory=list)
    action: str  # tag, status, delete
    value: Optional[str] = None  # tag name or status value
    # "ids" (default) applies the action to contact_ids; "all" applies it to
    # every contact matching the search/status/tag filters instead (the
    # Contacts page "select all N matching" mass-delete). Only delete supports
    # scope="all" — status/tag updates still need explicit ids.
    scope: str = "ids"
    # Filters that select the target set when scope == "all". They mirror the
    # GET /contacts/ list filters so what the user sees is exactly what is
    # deleted.
    search: Optional[str] = None
    lead_status: Optional[str] = None
    tag: Optional[str] = None


class CSVImportRequest(BaseModel):
    column_mapping: dict[str, str]
    list_id: Optional[int] = None
    skip_duplicates: bool = True
