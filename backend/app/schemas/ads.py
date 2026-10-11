"""Pydantic schemas for the SMS Ads Manager."""

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import (
    BaseModel,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.models.ads import (
    DEFAULT_FOLLOWUP_WAIT_HOURS,
    MAX_FOLLOWUP_WAIT_HOURS,
)


def _utc(v: Optional[datetime]) -> Optional[datetime]:
    if v is not None and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v


#: Defaults used when a legacy database row predates a column. The startup
#: schema repair backfills most new columns with their defaults, but a column
#: it had to add as nullable (or a repair that has not run yet) reads back as
#: NULL -- coercing here keeps every response valid instead of 500ing.
_OPTIMIZE_DEFAULTS = {
    "auto_optimize": False,
    "optimize_metric": "score",
    "optimize_min_sends": 30,
    "optimize_min_gap_pct": 25.0,
    "optimize_action": "shift",
}


def _fill_optimize_default(v, info: ValidationInfo):
    if v is None:
        return _OPTIMIZE_DEFAULTS.get(info.field_name)
    return v


#: The drip modes the dispatcher knows. Anything else would fall through to
#: "send as fast as possible", so the API rejects it instead of silently
#: blasting a list.
DRIP_MODES = ("off", "interval", "batch", "daily", "smart")


def _clean_drip_mode(v):
    if v is None:
        return v
    mode = str(v).strip().lower()
    if mode not in DRIP_MODES:
        raise ValueError(f"drip_mode must be one of: {', '.join(DRIP_MODES)}")
    return mode


class CampaignIn(BaseModel):
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    objective: str = "replies"
    #: "sms" (default) or "email" — decides the delivery provider AND which
    #: audience rules apply (phone validation vs email validation).
    channel: str = "sms"
    daily_limit: Optional[int] = None
    total_limit: Optional[int] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    send_start_hour: Optional[int] = Field(default=None, ge=0, le=23)
    send_end_hour: Optional[int] = Field(default=None, ge=0, le=23)
    send_days: Optional[str] = None
    timezone_name: Optional[str] = None
    drip_mode: str = "off"
    drip_batch_size: int = 1
    drip_interval_minutes: int = 0
    pacing: str = "even"
    continuous: bool = True
    always_on: bool = False
    max_per_contact_per_day: Optional[int] = None
    max_per_contact_per_week: Optional[int] = None
    # --- Email sender selection (multi-API) -----------------------------
    email_account_id: Optional[int] = None
    fallback_email_account_id: Optional[int] = None
    subject: Optional[str] = Field(default=None, max_length=500)
    track_opens: bool = True
    track_clicks: bool = True
    optimization_mode: str = "manual"
    queued_edit_policy: str = "keep"
    priority: str = "normal"
    test_mode: bool = False
    auto_optimize: bool = False
    optimize_metric: str = "score"
    optimize_min_sends: int = 30
    optimize_min_gap_pct: float = 25.0
    optimize_action: str = "shift"

    @field_validator("drip_mode")
    @classmethod
    def _validate_drip_mode(cls, v):
        return _clean_drip_mode(v)


class CampaignPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    objective: Optional[str] = None
    status: Optional[str] = None
    channel: Optional[str] = None
    daily_limit: Optional[int] = None
    total_limit: Optional[int] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    send_start_hour: Optional[int] = None
    send_end_hour: Optional[int] = None
    send_days: Optional[str] = None
    timezone_name: Optional[str] = None
    drip_mode: Optional[str] = None
    drip_batch_size: Optional[int] = None
    drip_interval_minutes: Optional[int] = None
    pacing: Optional[str] = None
    continuous: Optional[bool] = None
    always_on: Optional[bool] = None
    max_per_contact_per_day: Optional[int] = None
    max_per_contact_per_week: Optional[int] = None
    # --- Email sender selection (multi-API) -----------------------------
    email_account_id: Optional[int] = None
    fallback_email_account_id: Optional[int] = None
    subject: Optional[str] = None
    track_opens: Optional[bool] = None
    track_clicks: Optional[bool] = None
    optimization_mode: Optional[str] = None
    queued_edit_policy: Optional[str] = None
    priority: Optional[str] = None
    test_mode: Optional[bool] = None
    auto_optimize: Optional[bool] = None
    optimize_metric: Optional[str] = None
    optimize_min_sends: Optional[int] = None
    optimize_min_gap_pct: Optional[float] = None
    optimize_action: Optional[str] = None

    @field_validator("drip_mode")
    @classmethod
    def _validate_drip_mode(cls, v):
        return _clean_drip_mode(v)


class CampaignOut(BaseModel):
    id: int
    #: Which campaign system this id belongs to. The classic campaigns and the Ads
    #: Manager number independently, so an id is only meaningful with its kind.
    kind: str = "ads"
    name: str
    description: Optional[str]
    objective: str
    status: str
    #: Why it is paused (an automatic pause by the circuit breaker always says why).
    paused_reason: Optional[str] = None
    paused_at: Optional[datetime] = None
    channel: str = "sms"
    email_account_id: Optional[int] = None
    fallback_email_account_id: Optional[int] = None
    subject: Optional[str] = None
    track_opens: bool = True
    track_clicks: bool = True
    daily_limit: Optional[int]
    total_limit: Optional[int]
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    send_start_hour: Optional[int]
    send_end_hour: Optional[int]
    send_days: Optional[str]
    timezone_name: Optional[str]
    drip_mode: str
    drip_batch_size: int
    drip_interval_minutes: int
    pacing: str
    continuous: bool
    always_on: bool
    max_per_contact_per_day: Optional[int]
    max_per_contact_per_week: Optional[int]
    optimization_mode: str
    queued_edit_policy: str
    priority: str
    test_mode: bool
    auto_optimize: bool = False
    optimize_metric: str = "score"
    optimize_min_sends: int = 30
    optimize_min_gap_pct: float = 25.0
    optimize_action: str = "shift"
    optimize_last_run_at: Optional[datetime] = None
    sent_count: int
    last_state: Optional[str]
    last_activity_at: Optional[datetime]
    last_error: Optional[str] = None
    state_changed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    _tz = field_validator(
        "start_date", "end_date", "last_activity_at", "state_changed_at", "created_at", "updated_at",
        "optimize_last_run_at", "paused_at", mode="after"
    )(lambda v: _utc(v))

    _fill = field_validator(
        "auto_optimize", "optimize_metric", "optimize_min_sends", "optimize_min_gap_pct",
        "optimize_action", mode="before",
    )(_fill_optimize_default)

    model_config = {"from_attributes": True}


class SetIn(BaseModel):
    name: str = Field(..., max_length=255)
    status: str = "active"
    list_ids: Optional[str] = None
    contact_ids: Optional[str] = None
    audience_id: Optional[int] = None
    include_tags: Optional[str] = None
    exclude_tags: Optional[str] = None
    include_statuses: Optional[str] = None
    exclude_statuses: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    industry: Optional[str] = None
    activity_filter: Optional[str] = None
    exclude_campaign_ids: Optional[str] = None
    daily_limit: Optional[int] = None
    split_mode: str = "equal"
    auto_optimize: bool = True


class SetPatch(SetIn):
    name: Optional[str] = None  # type: ignore[assignment]
    status: Optional[str] = None  # type: ignore[assignment]
    split_mode: Optional[str] = None  # type: ignore[assignment]
    auto_optimize: Optional[bool] = None  # type: ignore[assignment]


class SetOut(BaseModel):
    id: int
    campaign_id: int
    name: str
    status: str
    list_ids: Optional[str]
    contact_ids: Optional[str] = None
    audience_id: Optional[int] = None
    include_tags: Optional[str]
    exclude_tags: Optional[str]
    include_statuses: Optional[str]
    exclude_statuses: Optional[str]
    city: Optional[str]
    state: Optional[str]
    industry: Optional[str]
    activity_filter: Optional[str]
    exclude_campaign_ids: Optional[str]
    daily_limit: Optional[int]
    split_mode: str
    auto_optimize: bool = True
    created_at: datetime

    _fill = field_validator("auto_optimize", mode="before")(
        lambda v: True if v is None else v
    )

    model_config = {"from_attributes": True}


class AudienceIn(BaseModel):
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    list_ids: Optional[str] = None
    contact_ids: Optional[str] = None
    include_tags: Optional[str] = None
    exclude_tags: Optional[str] = None
    include_statuses: Optional[str] = None
    exclude_statuses: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    industry: Optional[str] = None
    activity_filter: Optional[str] = None
    exclude_campaign_ids: Optional[str] = None


class AudiencePatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    list_ids: Optional[str] = None
    contact_ids: Optional[str] = None
    include_tags: Optional[str] = None
    exclude_tags: Optional[str] = None
    include_statuses: Optional[str] = None
    exclude_statuses: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    industry: Optional[str] = None
    activity_filter: Optional[str] = None
    exclude_campaign_ids: Optional[str] = None


class AudienceOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    list_ids: Optional[str]
    contact_ids: Optional[str]
    include_tags: Optional[str]
    exclude_tags: Optional[str]
    include_statuses: Optional[str]
    exclude_statuses: Optional[str]
    city: Optional[str]
    state: Optional[str]
    industry: Optional[str]
    activity_filter: Optional[str]
    exclude_campaign_ids: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TargetingPreviewIn(BaseModel):
    """Unsaved targeting filters for a live size estimate (set/audience editors)."""

    list_ids: Optional[str] = None
    contact_ids: Optional[str] = None
    audience_id: Optional[int] = None
    include_tags: Optional[str] = None
    exclude_tags: Optional[str] = None
    include_statuses: Optional[str] = None
    exclude_statuses: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    industry: Optional[str] = None
    activity_filter: Optional[str] = None
    exclude_campaign_ids: Optional[str] = None


class AttachAudienceIn(BaseModel):
    campaign_id: int
    set_id: Optional[int] = None
    new_set_name: Optional[str] = None


class OptimizeIn(BaseModel):
    dry_run: bool = False


class TrackClickIn(BaseModel):
    campaign_id: int
    creative_id: int
    contact_id: int
    set_id: Optional[int] = None
    assignment_id: Optional[int] = None


class CreativeIn(BaseModel):
    name: str = Field(..., max_length=255)
    body: str = ""
    cta: Optional[str] = None
    tracking_link: Optional[str] = None
    allocation: float = 0.0
    send_quota: Optional[int] = Field(None, ge=0, description="Exact SMS count for quota split mode")
    status: str = "active"
    template_id: Optional[int] = None
    # Email channel: subject + HTML variant + optional per-creative sender
    # (so an A/B test can compare two Brevo accounts/domains).
    subject: Optional[str] = Field(default=None, max_length=500)
    html_body: Optional[str] = None
    email_account_id: Optional[int] = None


class CreativePatch(BaseModel):
    name: Optional[str] = None
    body: Optional[str] = None
    cta: Optional[str] = None
    tracking_link: Optional[str] = None
    allocation: Optional[float] = None
    send_quota: Optional[int] = Field(None, ge=0)
    status: Optional[str] = None
    template_id: Optional[int] = None
    subject: Optional[str] = Field(default=None, max_length=500)
    html_body: Optional[str] = None
    email_account_id: Optional[int] = None


class CreativeOut(BaseModel):
    id: int
    set_id: int
    campaign_id: int
    name: str
    status: str
    body: str
    subject: Optional[str] = None
    html_body: Optional[str] = None
    email_account_id: Optional[int] = None
    cta: Optional[str]
    tracking_link: Optional[str]
    allocation: float
    send_quota: Optional[int] = None
    current_version: int
    is_deleted: bool
    template_id: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------
# Follow-up steps
# --------------------------------------------------------------------------
#
# The API's documented spelling for the wait is ``delay_hours`` (what the
# product spec, the UI and every other follow-up surface use). ``wait_hours``
# is the column name and stays accepted as an alias so older clients keep
# working. Both are validated against the same bounds, and the response echoes
# BOTH names so neither client has to guess.

FollowUpAction = Literal[
    "send_sms",
    "send_email",
    "add_tag",
    "remove_tag",
    "change_status",
    "create_task",
    "stop",
    "suppress",
    "notify",
]

FollowUpCondition = Literal[
    "no_reply",
    "replied",
    "positive",
    "negative",
    "positive_reply",
    "negative_reply",
    "interested",
    "not_interested",
    "converted",
    "opened",
    "not_opened",
    "opened_no_click",
    "link_clicked",
    "link_not_clicked",
    "clicked_no_reply",
    "meeting_scheduled",
    "always",
]


class FollowUpStepIn(BaseModel):
    """Create payload for a campaign follow-up step.

    Every field here is honoured: ``delay_hours`` sets the wait, ``action``
    picks the channel (``send_email`` -> Brevo, ``send_sms`` -> the gateway),
    ``subject``/``body`` carry the message (with ``{{shortcode}}``
    personalization) and ``condition`` decides whether it sends at all.
    """

    #: 1-based position in the chain. Omitted -> appended after the last step.
    step_order: Optional[int] = Field(default=None, ge=1, le=50)
    name: Optional[str] = Field(default=None, max_length=200)
    #: Hours to wait before this step (from the previous message). The
    #: documented spelling.
    delay_hours: Optional[int] = Field(
        default=None, ge=0, le=MAX_FOLLOWUP_WAIT_HOURS
    )
    #: Legacy alias of ``delay_hours``. Sending both with different values is a
    #: 422 rather than a silent coin toss.
    wait_hours: Optional[int] = Field(default=None, ge=0, le=MAX_FOLLOWUP_WAIT_HOURS)
    condition: FollowUpCondition = "no_reply"
    #: send_sms | send_email | add_tag | remove_tag | change_status |
    #: create_task | stop | suppress | notify. Omitted on an email campaign ->
    #: send_email, otherwise send_sms.
    action: Optional[FollowUpAction] = None
    body: Optional[str] = Field(default=None, max_length=20000)
    #: Email only (falls back to the campaign subject when empty).
    subject: Optional[str] = Field(default=None, max_length=500)
    action_value: Optional[str] = Field(default=None, max_length=255)
    #: Create this step from a saved follow-up template.
    template_id: Optional[int] = None
    is_active: bool = True

    @model_validator(mode="after")
    def _one_wait_value(self) -> "FollowUpStepIn":
        if (
            self.delay_hours is not None
            and self.wait_hours is not None
            and self.delay_hours != self.wait_hours
        ):
            raise ValueError(
                "delay_hours and wait_hours disagree "
                f"({self.delay_hours} vs {self.wait_hours}); send only one of them"
            )
        return self

    @property
    def resolved_wait_hours(self) -> Optional[int]:
        """The wait the client asked for, whichever spelling it used."""
        if self.delay_hours is not None:
            return self.delay_hours
        return self.wait_hours


class FollowUpStepPatch(BaseModel):
    """Any subset of a follow-up step, for PATCH."""

    step_order: Optional[int] = Field(default=None, ge=1, le=50)
    name: Optional[str] = Field(default=None, max_length=200)
    delay_hours: Optional[int] = Field(default=None, ge=0, le=MAX_FOLLOWUP_WAIT_HOURS)
    wait_hours: Optional[int] = Field(default=None, ge=0, le=MAX_FOLLOWUP_WAIT_HOURS)
    condition: Optional[FollowUpCondition] = None
    action: Optional[FollowUpAction] = None
    body: Optional[str] = Field(default=None, max_length=20000)
    subject: Optional[str] = Field(default=None, max_length=500)
    action_value: Optional[str] = Field(default=None, max_length=255)
    template_id: Optional[int] = None
    is_active: Optional[bool] = None

    @model_validator(mode="after")
    def _one_wait_value(self) -> "FollowUpStepPatch":
        if (
            self.delay_hours is not None
            and self.wait_hours is not None
            and self.delay_hours != self.wait_hours
        ):
            raise ValueError(
                "delay_hours and wait_hours disagree "
                f"({self.delay_hours} vs {self.wait_hours}); send only one of them"
            )
        return self

    @property
    def resolved_wait_hours(self) -> Optional[int]:
        if self.delay_hours is not None:
            return self.delay_hours
        return self.wait_hours


class FollowUpStepOut(BaseModel):
    """A follow-up step as returned by every endpoint that touches one.

    Carries the wait under both names: ``wait_hours`` (the column, what the
    existing UI reads) and ``delay_hours`` (the documented API field).
    """

    id: int
    campaign_id: int
    step_order: int
    name: Optional[str] = None
    wait_hours: int
    delay_hours: int
    condition: str
    action: str
    subject: Optional[str] = None
    body: Optional[str] = None
    template_id: Optional[int] = None
    action_value: Optional[str] = None
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


def followup_step_out(step) -> dict:
    """Serialise an ``AdsFollowUpStep`` row for the API.

    One helper so create / update / list / campaign-detail can never disagree
    about the shape (that disagreement is exactly how the delay silently
    changed under the client before).
    """
    return {
        "id": step.id,
        "campaign_id": step.campaign_id,
        "step_order": step.step_order,
        "name": step.name,
        "wait_hours": step.wait_hours,
        "delay_hours": step.wait_hours,
        "condition": step.condition,
        "action": step.action,
        "subject": step.subject,
        "body": step.body,
        "template_id": getattr(step, "template_id", None),
        "action_value": step.action_value,
        "is_active": bool(step.is_active) and getattr(step, "deleted_at", None) is None,
        "created_at": step.created_at,
        "updated_at": getattr(step, "updated_at", None),
        "deleted_at": getattr(step, "deleted_at", None),
    }


# --------------------------------------------------------------------------
# Follow-up template library
# --------------------------------------------------------------------------


class FollowUpTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=500)
    #: sms | email | any
    channel: Literal["sms", "email", "any"] = "any"
    subject: Optional[str] = Field(default=None, max_length=500)
    body: str = Field(min_length=1, max_length=20000)
    condition: FollowUpCondition = "no_reply"
    action: FollowUpAction = "send_sms"
    delay_hours: int = Field(
        default=DEFAULT_FOLLOWUP_WAIT_HOURS, ge=0, le=MAX_FOLLOWUP_WAIT_HOURS
    )

    @field_validator("name", "body")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class FollowUpTemplatePatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=500)
    channel: Optional[Literal["sms", "email", "any"]] = None
    subject: Optional[str] = Field(default=None, max_length=500)
    body: Optional[str] = Field(default=None, min_length=1, max_length=20000)
    condition: Optional[FollowUpCondition] = None
    action: Optional[FollowUpAction] = None
    delay_hours: Optional[int] = Field(
        default=None, ge=0, le=MAX_FOLLOWUP_WAIT_HOURS
    )
    is_active: Optional[bool] = None


class FollowUpTemplateOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    channel: str
    subject: Optional[str] = None
    body: str
    condition: str
    action: str
    delay_hours: int
    is_active: bool
    usage_count: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


def followup_template_out(template) -> dict:
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "channel": template.channel,
        "subject": template.subject,
        "body": template.body,
        "condition": template.condition,
        "action": template.action,
        "delay_hours": template.delay_hours,
        "is_active": bool(template.is_active),
        "usage_count": template.usage_count or 0,
        "created_at": template.created_at,
        "updated_at": getattr(template, "updated_at", None),
    }


class AddContactsIn(BaseModel):
    set_id: Optional[int] = None
    list_ids: list[int] = []
    contact_ids: list[int] = []
    tags: list[str] = []


class CalendarEventIn(BaseModel):
    title: str
    event_type: str = "meeting"
    contact_id: Optional[int] = None
    campaign_id: Optional[int] = None
    starts_at: datetime
    duration_minutes: int = 30
    notes: Optional[str] = None
    priority: str = "normal"
    reminder_minutes: Optional[int] = None


class SuppressionIn(BaseModel):
    phone_number: Optional[str] = None
    contact_id: Optional[int] = None
    reason: str = "Manual suppression"
    notes: Optional[str] = None


class ContactActionIn(BaseModel):
    action: str
    value: Optional[str] = None
    note: Optional[str] = None
    stop_campaigns: bool = False
    suppress: bool = False


class FollowUpTaskIn(BaseModel):
    contact_id: int
    campaign_id: Optional[int] = None
    due_at: Optional[datetime] = None
    note: Optional[str] = None
    body: Optional[str] = None
    priority: str = "normal"


class BulkActionIn(BaseModel):
    ids: list[int] = Field(default_factory=list)
    action: str
    value: Optional[str] = None
    # "ids" (default) targets the explicit ids. "all" makes the audience
    # bulk-remove act on every removable (unsent) row of the campaign, so the
    # UI can offer "remove all unsent contacts" without enumerating every id.
    scope: str = "ids"
    # When scope == "all", restricts the removal to rows whose send_status
    # matches, so the remove-all matches the Audience tab's status filter.
    status: Optional[str] = None
