"""Shared validation and serialization helpers for sequences (SMS and email)."""

import json
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.template import Template


#: Both send types are first-class: which one a sequence uses is decided by the
#: sequence's own ``channel``, never by silently reinterpreting a step.
ALLOWED_STEP_TYPES = {"send_sms", "send_email", "wait", "condition", "stop"}
SEND_STEP_TYPES = {"send_sms", "send_email"}
ALLOWED_CHANNELS = {"sms", "email"}
ALLOWED_CONDITIONS = {
    "contact_replied",
    "contact_did_not_reply",
    "message_delivered",
    "message_failed",
    "contact_opted_out",
}
MAX_SEQUENCE_STEPS = 50
MAX_STEP_MESSAGE_LENGTH = 5000
#: Email bodies may be far longer than an SMS; the HTML part is stored with it.
MAX_STEP_EMAIL_LENGTH = 100000


def step_message_body(config: str | None) -> str | None:
    """Read a per-step written message from the step's JSON config.

    Older sequences may have null config and rely on the campaign message. A
    plain string is also accepted as a backward-compatible fallback.
    """
    if not config:
        return None
    try:
        parsed = json.loads(config)
    except (TypeError, ValueError):
        parsed = config
    if isinstance(parsed, dict):
        value = parsed.get("message_body")
    elif isinstance(parsed, str):
        value = parsed
    else:
        value = None
    body = str(value or "").strip()
    return body or None


def message_config(body: str | None) -> str | None:
    body = (body or "").strip()
    if not body:
        return None
    return json.dumps({"message_body": body}, ensure_ascii=False)


def snapshot_steps(steps: Iterable[Any]) -> str:
    """Serialize model/schema step objects into the immutable campaign format."""
    return json.dumps(
        [
            {
                "step_order": step.step_order,
                "step_type": step.step_type,
                "config": step.config,
                "wait_duration_hours": step.wait_duration_hours,
                "template_id": step.template_id,
                "subject": getattr(step, "subject", None),
                "email_account_id": getattr(step, "email_account_id", None),
                "condition_type": step.condition_type,
                "condition_value": step.condition_value,
                "true_branch_step_order": step.true_branch_step_order,
                "false_branch_step_order": step.false_branch_step_order,
            }
            for step in steps
        ],
        ensure_ascii=False,
    )


def step_is_send(step: Any) -> bool:
    """True for any step that actually reaches a person (SMS or email)."""
    return str(getattr(step, "step_type", "")) in SEND_STEP_TYPES


async def validate_sequence_steps(
    db: AsyncSession,
    steps: list[Any],
    *,
    channel: str = "sms",
    allow_campaign_message_fallback: bool = False,
) -> list[Any]:
    """Validate a complete sequence and return it sorted by step order.

    ``allow_campaign_message_fallback`` only supports sequences created by the
    old builder, where Send SMS steps had no content of their own. New and
    edited sequences always require explicit per-step content.
    """
    channel = (channel or "sms").lower()
    if channel not in ALLOWED_CHANNELS:
        raise ValueError("A sequence channel must be sms or email")
    send_step_type = "send_email" if channel == "email" else "send_sms"
    send_label = "Send email" if channel == "email" else "Send SMS"

    if not steps:
        raise ValueError("A sequence needs at least one step")
    if len(steps) > MAX_SEQUENCE_STEPS:
        raise ValueError(f"A sequence can have at most {MAX_SEQUENCE_STEPS} steps")

    ordered = sorted(steps, key=lambda step: step.step_order)
    orders = [step.step_order for step in ordered]
    if orders != list(range(len(ordered))):
        raise ValueError("Sequence step numbers must be consecutive, starting at 0")

    template_ids: set[int] = set()
    send_count = 0
    for step in ordered:
        if step.step_type not in ALLOWED_STEP_TYPES:
            raise ValueError(f"Unsupported sequence step: {step.step_type}")

        if step.step_type in SEND_STEP_TYPES:
            if step.step_type != send_step_type:
                # An SMS step inside an email sequence (or the reverse) would
                # reach the wrong channel — the one bug a channel-scoped
                # sequence exists to prevent.
                raise ValueError(
                    f"Step {step.step_order + 1} is {step.step_type}, but this is an "
                    f"{channel} sequence — use {send_step_type}"
                )
            send_count += 1
            body = step_message_body(step.config)
            limit = (
                MAX_STEP_EMAIL_LENGTH if channel == "email" else MAX_STEP_MESSAGE_LENGTH
            )
            if body and len(body) > limit:
                raise ValueError(
                    f"Step {step.step_order + 1} message is longer than {limit} characters"
                )
            if channel == "email":
                subject = (getattr(step, "subject", None) or "").strip()
                if not subject:
                    raise ValueError(
                        f"Step {step.step_order + 1} needs a subject line"
                    )
                if len(subject) > 500:
                    raise ValueError(
                        f"Step {step.step_order + 1} subject is longer than 500 characters"
                    )
            if step.template_id:
                template_ids.add(step.template_id)
            elif not body and not allow_campaign_message_fallback:
                raise ValueError(
                    f"Step {step.step_order + 1} must have a written message or template"
                )

        elif step.step_type == "wait":
            if not step.wait_duration_hours or step.wait_duration_hours < 1:
                raise ValueError(f"Step {step.step_order + 1} wait must be at least 1 hour")

        elif step.step_type == "condition":
            if step.condition_type not in ALLOWED_CONDITIONS:
                raise ValueError(f"Step {step.step_order + 1} has an invalid condition")
            for label, target in (
                ("true", step.true_branch_step_order),
                ("false", step.false_branch_step_order),
            ):
                if target is None:
                    continue
                if target >= len(ordered):
                    raise ValueError(
                        f"Step {step.step_order + 1} {label} branch points outside the sequence"
                    )
                # Backward branches create an infinite automation loop and can
                # repeatedly text a real number. Only forward branches are safe.
                if target <= step.step_order:
                    raise ValueError(
                        f"Step {step.step_order + 1} {label} branch must point to a later step"
                    )

    if send_count == 0:
        raise ValueError(f"A sequence needs at least one {send_label} step")

    if template_ids:
        rows = (
            await db.execute(select(Template).where(Template.id.in_(template_ids)))
        ).scalars().all()
        templates = {template.id: template for template in rows}
        missing = sorted(template_ids - set(templates))
        if missing:
            raise ValueError(f"Sequence template not found: {', '.join(map(str, missing))}")
        inactive = sorted(
            template_id
            for template_id, template in templates.items()
            if not template.is_active or not (template.body or "").strip()
        )
        if inactive:
            raise ValueError(
                f"Sequence template is inactive or empty: {', '.join(map(str, inactive))}"
            )
        # "any" templates work everywhere; a channel-specific one must match.
        wrong_channel = sorted(
            template_id
            for template_id, template in templates.items()
            if (getattr(template, "channel", "any") or "any") not in ("any", channel)
        )
        if wrong_channel:
            raise ValueError(
                "Sequence template is for the other channel: "
                f"{', '.join(map(str, wrong_channel))}"
            )
        if channel == "email":
            empty_subject = sorted(
                template_id
                for template_id, template in templates.items()
                if not (getattr(template, "subject", None) or "").strip()
            )
            if empty_subject:
                raise ValueError(
                    "Email sequence template has no subject: "
                    f"{', '.join(map(str, empty_subject))}"
                )

    return ordered
