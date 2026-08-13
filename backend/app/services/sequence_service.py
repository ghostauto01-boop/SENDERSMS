"""Shared validation and serialization helpers for SMS sequences."""

import json
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.template import Template


ALLOWED_STEP_TYPES = {"send_sms", "wait", "condition", "stop"}
ALLOWED_CONDITIONS = {
    "contact_replied",
    "contact_did_not_reply",
    "message_delivered",
    "message_failed",
    "contact_opted_out",
}
MAX_SEQUENCE_STEPS = 50
MAX_STEP_MESSAGE_LENGTH = 5000


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
                "condition_type": step.condition_type,
                "condition_value": step.condition_value,
                "true_branch_step_order": step.true_branch_step_order,
                "false_branch_step_order": step.false_branch_step_order,
            }
            for step in steps
        ],
        ensure_ascii=False,
    )


async def validate_sequence_steps(
    db: AsyncSession,
    steps: list[Any],
    *,
    allow_campaign_message_fallback: bool = False,
) -> list[Any]:
    """Validate a complete sequence and return it sorted by step order.

    ``allow_campaign_message_fallback`` only supports sequences created by the
    old builder, where Send SMS steps had no content of their own. New and
    edited sequences always require explicit per-step content.
    """
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

        if step.step_type == "send_sms":
            send_count += 1
            body = step_message_body(step.config)
            if body and len(body) > MAX_STEP_MESSAGE_LENGTH:
                raise ValueError(
                    f"Step {step.step_order + 1} message is longer than "
                    f"{MAX_STEP_MESSAGE_LENGTH} characters"
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
        raise ValueError("A sequence needs at least one Send SMS step")

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

    return ordered
