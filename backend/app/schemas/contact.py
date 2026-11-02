"""Pydantic schemas for contacts."""

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.utils.contact_identity import normalize_email


def _tag_list(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        raise ValueError("tags must be an array of tag names or a comma-separated string")
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("each tag must be a string")
        tag = item.strip()
        if not tag:
            continue
        if len(tag) > 100:
            raise ValueError("tag names must be 100 characters or fewer")
        if tag.lower() not in {name.lower() for name in cleaned}:
            cleaned.append(tag)
    return cleaned


class ContactCreate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    business_name: Optional[str] = None
    phone_number: Optional[str] = Field(default=None, max_length=20)
    email: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = Field(default=None, max_length=100)
    website: Optional[str] = None
    industry: Optional[str] = None
    source: Optional[str] = None
    lead_status: str = "new"
    notes: Optional[str] = None
    custom_fields: Optional[str] = None
    tags: Optional[list[str] | str] = None

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if value not in (None, "") and not normalize_email(value):
            raise ValueError("Enter one valid email address")
        return normalize_email(value) if value else None

    @field_validator("tags", mode="before")
    @classmethod
    def parse_tags(cls, value):
        return _tag_list(value)


class ContactUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    business_name: Optional[str] = None
    phone_number: Optional[str] = Field(default=None, max_length=20)
    email: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = Field(default=None, max_length=100)
    website: Optional[str] = None
    industry: Optional[str] = None
    source: Optional[str] = None
    lead_status: Optional[str] = None
    notes: Optional[str] = None
    custom_fields: Optional[str] = None
    has_consented: Optional[bool] = None
    is_opted_out: Optional[bool] = None
    is_email_opted_out: Optional[bool] = None
    email_status: Optional[str] = None
    tags: Optional[list[str] | str] = None
    tag_operation: Literal["replace", "add", "remove"] = "replace"

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if value not in (None, "") and not normalize_email(value):
            raise ValueError("Enter one valid email address")
        return normalize_email(value) if value else None

    @field_validator("tags", mode="before")
    @classmethod
    def parse_tags(cls, value):
        return _tag_list(value)


class ContactOut(BaseModel):
    id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    business_name: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    source: Optional[str] = None
    lead_status: str = "new"
    consent_status: str = "unknown"
    has_consented: bool = False
    is_opted_out: bool = False
    is_undeliverable: bool = False
    undeliverable_reason: Optional[str] = None
    delivery_fail_count: int = 0
    is_email_opted_out: bool = False
    email_status: str = "active"
    email_opted_out_at: Optional[datetime] = None
    email_opt_out_reason: Optional[str] = None
    is_email_undeliverable: bool = False
    emails_sent: int = 0
    emails_received: int = 0
    last_emailed_at: Optional[datetime] = None
    email_source: Optional[str] = None
    email_verified: bool = False
    email_verified_at: Optional[datetime] = None
    email_verdict: Optional[str] = None
    email_confidence: Optional[int] = None
    email_enriched_at: Optional[datetime] = None
    email_enrichment_note: Optional[str] = None
    opt_out_reason: Optional[str] = None
    opted_out_at: Optional[datetime] = None
    notes: Optional[str] = None
    custom_fields: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    messages_sent: int = 0
    messages_received: int = 0
    last_contacted_at: Optional[datetime] = None
    last_reply_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    @field_validator(
        "has_consented",
        "is_opted_out",
        "is_undeliverable",
        "is_email_opted_out",
        "is_email_undeliverable",
        "email_verified",
        mode="before",
    )
    @classmethod
    def _coerce_null_bool(cls, v):
        return False if v is None else v

    @field_validator(
        "delivery_fail_count",
        "emails_sent",
        "emails_received",
        "messages_sent",
        "messages_received",
        mode="before",
    )
    @classmethod
    def _coerce_null_int(cls, v):
        return 0 if v is None else v

    @field_validator("lead_status", mode="before")
    @classmethod
    def _coerce_null_lead_status(cls, v):
        return "new" if v is None else v

    @field_validator("consent_status", mode="before")
    @classmethod
    def _coerce_null_consent_status(cls, v):
        return "unknown" if v is None else v

    @field_validator("email_status", mode="before")
    @classmethod
    def _coerce_null_email_status(cls, v):
        return "active" if v is None else v

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_null_tags(cls, v):
        return [] if v is None else v

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _coerce_null_timestamps(cls, v):
        return datetime.now(timezone.utc) if v is None else v

    model_config = {"from_attributes": True}


class ContactListOut(BaseModel):
    total: int
    items: list[ContactOut]
    # Where this page sits, so a client never has to guess whether there is more.
    page: Optional[int] = None
    per_page: Optional[int] = None
    next_page: Optional[int] = None


class BulkAction(BaseModel):
    contact_ids: list[int] = Field(default_factory=list)
    action: str
    value: Optional[str] = None
    scope: str = "ids"
    search: Optional[str] = None
    lead_status: Optional[str] = None
    tag: Optional[str] = None
    #: sms | email — mirror the Contacts page channel view in scope="all"
    channel: Optional[str] = None
    #: mirror the Contacts page list selector in scope="all"
    list_id: Optional[int] = None
    #: mirror the Contacts page email-eligibility filter in scope="all"
    email_state: Optional[str] = None


class CSVImportRequest(BaseModel):
    column_mapping: dict[str, str]
    list_id: Optional[int] = None
    skip_duplicates: bool = True


class ContactImportURLRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    file_name: Optional[str] = None
    list_id: Optional[int] = None
    new_list_name: Optional[str] = None
    skip_duplicates: bool = True
    column_mapping: Optional[dict[str, str]] = None
    tags: Optional[list[str] | str] = None
    auto_tag_rules: list[dict] = Field(default_factory=list)
    model_config = {"extra": "forbid"}

    @field_validator("tags", mode="before")
    @classmethod
    def parse_tags(cls, value):
        return _tag_list(value)


class ContactEnrichFilters(BaseModel):
    """Supported contact filters for a typed enrichment selection."""

    search: Optional[str] = Field(default=None, max_length=255)
    lead_status: Optional[str] = Field(default=None, max_length=50)
    tag: Optional[str] = Field(default=None, max_length=100)
    list_id: Optional[int] = Field(default=None, ge=1)
    model_config = {"extra": "forbid"}


class ContactEnrichRequest(BaseModel):
    """Typed selection accepted by the JSON enrichment API."""

    contact_ids: Optional[list[int]] = None
    ids: Optional[list[int]] = None
    list_id: Optional[int] = Field(default=None, ge=1)
    tag: Optional[str] = Field(default=None, max_length=100)
    filter: Optional[ContactEnrichFilters] = None
    filters: Optional[ContactEnrichFilters] = None
    scope: Optional[Literal["ids", "all", "no_email", "unverified", "list"]] = None
    all: Optional[bool] = None
    all_matching: Optional[bool] = None
    search: Optional[str] = Field(default=None, max_length=255)
    lead_status: Optional[str] = Field(default=None, max_length=50)
    allow_inferred: Optional[bool] = None
    limit: int = Field(default=500, ge=1, le=5000)
    model_config = {"extra": "forbid"}

    @field_validator("contact_ids", "ids")
    @classmethod
    def validate_contact_ids(cls, value):
        if value is None:
            return value
        if len(value) > 5000:
            raise ValueError("At most 5000 contact IDs may be selected")
        if any(contact_id < 1 for contact_id in value):
            raise ValueError("contact IDs must be positive integers")
        return list(dict.fromkeys(value))
