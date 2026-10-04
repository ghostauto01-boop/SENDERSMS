"""Pydantic schemas for the email channel (senders, sends, templates)."""

from typing import Optional

from pydantic import BaseModel, Field


class EmailAccountIn(BaseModel):
    name: str = Field(..., max_length=150)
    from_name: str = Field(..., max_length=150)
    from_email: str = Field(..., max_length=255)
    api_key: Optional[str] = Field(default=None, max_length=500)
    reply_to: Optional[str] = Field(default=None, max_length=255)
    daily_limit: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    is_active: bool = True
    is_default: bool = False
    track_opens: bool = True
    track_clicks: bool = True


class EmailAccountPatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=150)
    from_name: Optional[str] = Field(default=None, max_length=150)
    from_email: Optional[str] = Field(default=None, max_length=255)
    #: Omit to keep the stored key; send a new value to replace it.
    api_key: Optional[str] = Field(default=None, max_length=500)
    reply_to: Optional[str] = Field(default=None, max_length=255)
    daily_limit: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None
    track_opens: Optional[bool] = None
    track_clicks: Optional[bool] = None


class EmailTestSendIn(BaseModel):
    to: str = Field(..., max_length=255)
    subject: Optional[str] = Field(default=None, max_length=500)


class EmailComposerTestIn(BaseModel):
    """Send the email currently in the composer to one address (a test).

    Brevo's editor has the same button: it mails the real thing — variables
    rendered, HTML, attachments — to you instead of to the list.
    """

    to: str = Field(..., max_length=255)
    subject: Optional[str] = Field(default=None, max_length=500)
    body: Optional[str] = None
    html_body: Optional[str] = None
    attachments: Optional[list[dict]] = None
    email_account_id: Optional[int] = None
    template_id: Optional[int] = None
    #: When given, ``{{variables}}`` are rendered with that contact's data.
    contact_id: Optional[int] = None


class EmailSendIn(BaseModel):
    """One-off email send from the Send page / Email Manager compose."""

    contact_id: Optional[int] = None
    #: Send to a raw address (a contact is created/found by that address).
    email: Optional[str] = Field(default=None, max_length=255)
    list_id: Optional[int] = None
    subject: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=200_000)
    html_body: Optional[str] = Field(default=None, max_length=500_000)
    template_id: Optional[int] = None
    email_account_id: Optional[int] = None
    #: Files to attach: [{"name": "...", "content_base64": "..."}].
    attachments: Optional[list[dict]] = None
    #: Copied recipients, like any mail client.
    cc: Optional[list[str]] = None
    bcc: Optional[list[str]] = None
    #: ISO-8601; when set the email is scheduled instead of sent now.
    schedule_at: Optional[str] = None


class EmailReplyIn(BaseModel):
    body: str = Field(default="", max_length=200_000)
    html_body: Optional[str] = Field(default=None, max_length=500_000)
    subject: Optional[str] = Field(default=None, max_length=500)
    template_id: Optional[int] = None
    email_account_id: Optional[int] = None
    #: Attach files to the reply, exactly like a normal mail client.
    attachments: Optional[list[dict]] = None
    cc: Optional[list[str]] = None
    bcc: Optional[list[str]] = None


class EmailSuppressionIn(BaseModel):
    email_address: str = Field(..., max_length=255)
    reason: Optional[str] = Field(default=None, max_length=500)
    source: str = Field(default="manual", max_length=40)


class EmailPreviewIn(BaseModel):
    subject: Optional[str] = Field(default=None, max_length=500)
    #: Sent so the preview shows the attachment list the recipient will see.
    attachments: Optional[list[dict]] = None
    body: Optional[str] = Field(default=None, max_length=200_000)
    html_body: Optional[str] = Field(default=None, max_length=500_000)
    contact_id: Optional[int] = None
    template_id: Optional[int] = None
