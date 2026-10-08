"""Pydantic schemas for the email channel (senders, sends, templates)."""

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.utils.contact_identity import normalize_email


def _normalise_reply_to(value):
    """Accept one address, a comma-delimited string, or an address array.

    Values are validated before any ORM mutation, so malformed input can never
    clear an existing reply route as a side effect of failed parsing.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        values = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        values = value
        if not values:
            return None
    else:
        raise ValueError("reply_to must be an email address, comma-separated addresses, or an array")
    if any(not isinstance(item, str) for item in values):
        raise ValueError("reply_to must contain one or more email addresses")
    addresses = []
    for item in values:
        address = normalize_email(item)
        if not address:
            raise ValueError(f"Invalid reply-to email address: {item!r}")
        if address not in addresses:
            addresses.append(address)
    if len(addresses) > 10:
        raise ValueError("At most 10 reply-to addresses are supported")
    return addresses or None


class EmailAccountIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    from_name: str = Field(..., min_length=1, max_length=150)
    from_email: str = Field(..., max_length=255)
    api_key: Optional[str] = Field(default=None, max_length=500)
    reply_to: Optional[list[str]] = None
    daily_limit: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    is_active: bool = True
    is_default: bool = False
    track_opens: bool = True
    track_clicks: bool = True

    @field_validator("reply_to", mode="before")
    @classmethod
    def parse_reply_to(cls, value):
        return _normalise_reply_to(value)



class EmailAccountPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    from_name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    from_email: Optional[str] = Field(default=None, max_length=255)
    #: Omit to keep the stored key; send a new value to replace it.
    api_key: Optional[str] = Field(default=None, max_length=500)
    #: The API accepts a single address, comma-separated addresses, or an array.
    reply_to: Optional[list[str]] = None
    daily_limit: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None
    track_opens: Optional[bool] = None
    track_clicks: Optional[bool] = None

    @field_validator("reply_to", mode="before")
    @classmethod
    def parse_reply_to(cls, value):
        return _normalise_reply_to(value)

    @field_validator("from_email")
    @classmethod
    def valid_from_email(cls, value):
        if value is not None and not normalize_email(value):
            raise ValueError("Enter a valid From email address")
        return value



class EmailTestSendIn(BaseModel):
    to: str = Field(..., max_length=255)
    subject: Optional[str] = Field(default=None, max_length=500)


class EmailComposerTestIn(BaseModel):
    """Send the email currently in the composer to one address (a test)."""

    to: str = Field(..., max_length=255)
    subject: Optional[str] = Field(default=None, max_length=500)
    body: Optional[str] = None
    html_body: Optional[str] = None
    attachments: Optional[list[dict]] = None
    email_account_id: Optional[int] = None
    template_id: Optional[int] = None
    contact_id: Optional[int] = None


class EmailSendIn(BaseModel):
    """One-off email send from the Send page / Email Manager compose."""

    contact_id: Optional[int] = None
    email: Optional[str] = Field(default=None, max_length=255)
    list_id: Optional[int] = None
    subject: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=200_000)
    html_body: Optional[str] = Field(default=None, max_length=500_000)
    template_id: Optional[int] = None
    email_account_id: Optional[int] = None
    attachments: Optional[list[dict]] = None
    cc: Optional[list[str]] = None
    bcc: Optional[list[str]] = None
    schedule_at: Optional[str] = None


class EmailReplyIn(BaseModel):
    body: str = Field(default="", max_length=200_000)
    html_body: Optional[str] = Field(default=None, max_length=500_000)
    subject: Optional[str] = Field(default=None, max_length=500)
    template_id: Optional[int] = None
    email_account_id: Optional[int] = None
    attachments: Optional[list[dict]] = None
    cc: Optional[list[str]] = None
    bcc: Optional[list[str]] = None


class EmailSuppressionIn(BaseModel):
    email_address: str = Field(..., max_length=255)
    reason: Optional[str] = Field(default=None, max_length=500)
    source: str = Field(default="manual", max_length=40)


class EmailPreviewIn(BaseModel):
    subject: Optional[str] = Field(default=None, max_length=500)
    attachments: Optional[list[dict]] = None
    body: Optional[str] = Field(default=None, max_length=200_000)
    html_body: Optional[str] = Field(default=None, max_length=500_000)
    contact_id: Optional[int] = None
    template_id: Optional[int] = None
