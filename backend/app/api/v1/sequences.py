"""Sequences API routes."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.campaign import Campaign
from app.models.followup import FollowUp
from app.models.sequence import Sequence, SequenceStep, SequenceVersion
from app.models.user import User
from app.schemas.sequence import SequenceCreate, SequenceUpdate
from app.security.auth import get_current_user
from app.services.sequence_service import snapshot_steps, validate_sequence_steps

router = APIRouter()


async def _current_steps(db: AsyncSession, sequence: Sequence) -> list[SequenceStep]:
    return list(
        (
            await db.execute(
                select(SequenceStep)
                .where(
                    SequenceStep.sequence_id == sequence.id,
                    SequenceStep.version == sequence.current_version,
                )
                .order_by(SequenceStep.step_order)
            )
        ).scalars().all()
    )


def _step_item(step: SequenceStep) -> dict:
    return {
        "id": step.id,
        "step_order": step.step_order,
        "step_type": step.step_type,
        "config": step.config,
        "wait_duration_hours": step.wait_duration_hours,
        "template_id": step.template_id,
        "subject": step.subject,
        "email_account_id": step.email_account_id,
        "condition_type": step.condition_type,
        "condition_value": step.condition_value,
        "true_branch_step_order": step.true_branch_step_order,
        "false_branch_step_order": step.false_branch_step_order,
    }


def _sequence_item(sequence: Sequence, steps: list[SequenceStep]) -> dict:
    return {
        "id": sequence.id,
        "name": sequence.name,
        "description": sequence.description,
        "channel": (sequence.channel or "sms"),
        "current_version": sequence.current_version,
        "is_active": sequence.is_active,
        "steps": [_step_item(step) for step in steps],
        "created_at": sequence.created_at.isoformat(),
        "updated_at": sequence.updated_at.isoformat(),
    }


@router.get("/")
async def list_sequences(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    search: Optional[str] = None,
    channel: Optional[str] = Query(default=None, description="sms | email"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List sequences for one channel, with their current steps for the builder UI.

    The channel defaults to *no filter* so existing callers keep seeing every
    sequence; the app always sends the channel it is showing.
    """
    query = select(Sequence)
    if channel in ("sms", "email"):
        query = query.where(Sequence.channel == channel)
    if search:
        query = query.where(Sequence.name.ilike(f"%{search}%"))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    sequences = (
        await db.execute(
            query.order_by(Sequence.updated_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).scalars().all()

    items = []
    for sequence in sequences:
        items.append(_sequence_item(sequence, await _current_steps(db, sequence)))
    return {"total": total, "items": items}


@router.get("/{sequence_id}")
async def get_sequence(
    sequence_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a sequence with its current steps."""
    sequence = (
        await db.execute(select(Sequence).where(Sequence.id == sequence_id))
    ).scalar_one_or_none()
    if not sequence:
        raise HTTPException(status_code=404, detail="Sequence not found")
    return _sequence_item(sequence, await _current_steps(db, sequence))


@router.post("/", status_code=201)
async def create_sequence(
    data: SequenceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a validated sequence with per-step SMS or email content."""
    try:
        steps = await validate_sequence_steps(db, data.steps, channel=data.channel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sequence = Sequence(
        name=data.name.strip(),
        description=data.description,
        channel=data.channel,
    )
    db.add(sequence)
    await db.flush()

    for step_data in steps:
        db.add(
            SequenceStep(
                sequence_id=sequence.id,
                version=1,
                step_order=step_data.step_order,
                step_type=step_data.step_type,
                config=step_data.config,
                wait_duration_hours=step_data.wait_duration_hours,
                template_id=step_data.template_id,
                subject=step_data.subject,
                email_account_id=step_data.email_account_id,
                condition_type=step_data.condition_type,
                condition_value=step_data.condition_value,
                true_branch_step_order=step_data.true_branch_step_order,
                false_branch_step_order=step_data.false_branch_step_order,
            )
        )

    await db.flush()
    await db.refresh(sequence)
    return {"success": True, "id": sequence.id, "name": sequence.name}


@router.put("/{sequence_id}")
async def update_sequence(
    sequence_id: int,
    data: SequenceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a sequence, preserving running campaign snapshots."""
    sequence = (
        await db.execute(select(Sequence).where(Sequence.id == sequence_id))
    ).scalar_one_or_none()
    if not sequence:
        raise HTTPException(status_code=404, detail="Sequence not found")

    if data.name is not None:
        sequence.name = data.name.strip()
    if "description" in data.model_fields_set:
        sequence.description = data.description

    # Channel changes are allowed only while nothing is running: a live
    # campaign snapshot says "send_sms", and flipping the channel would leave
    # it pointing at steps the other channel cannot execute.
    if data.channel is not None and data.channel != (sequence.channel or "sms"):
        running = (
            await db.execute(
                select(func.count(Campaign.id)).where(
                    Campaign.sequence_id == sequence_id,
                    Campaign.status.in_(("running", "scheduled", "paused")),
                )
            )
        ).scalar() or 0
        if running:
            raise HTTPException(
                status_code=409,
                detail="This sequence is used by a live campaign. Duplicate it to change its channel.",
            )
        sequence.channel = data.channel

    if data.steps is not None:
        try:
            steps = await validate_sequence_steps(
                db, data.steps, channel=(sequence.channel or "sms")
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        old_steps = await _current_steps(db, sequence)
        if old_steps:
            db.add(
                SequenceVersion(
                    sequence_id=sequence.id,
                    version=sequence.current_version,
                    snapshot=snapshot_steps(old_steps),
                )
            )

        sequence.current_version += 1
        for step_data in steps:
            db.add(
                SequenceStep(
                    sequence_id=sequence.id,
                    version=sequence.current_version,
                    step_order=step_data.step_order,
                    step_type=step_data.step_type,
                    config=step_data.config,
                    wait_duration_hours=step_data.wait_duration_hours,
                    template_id=step_data.template_id,
                    subject=step_data.subject,
                    email_account_id=step_data.email_account_id,
                    condition_type=step_data.condition_type,
                    condition_value=step_data.condition_value,
                    true_branch_step_order=step_data.true_branch_step_order,
                    false_branch_step_order=step_data.false_branch_step_order,
                )
            )

    await db.flush()
    return {"success": True, "version": sequence.current_version}


@router.delete("/{sequence_id}", status_code=204)
async def delete_sequence(
    sequence_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete an unused sequence."""
    sequence = (
        await db.execute(select(Sequence).where(Sequence.id == sequence_id))
    ).scalar_one_or_none()
    if not sequence:
        raise HTTPException(status_code=404, detail="Sequence not found")

    campaign_count = (
        await db.execute(
            select(func.count(Campaign.id)).where(Campaign.sequence_id == sequence_id)
        )
    ).scalar() or 0
    followup_count = (
        await db.execute(
            select(func.count(FollowUp.id)).where(FollowUp.sequence_id == sequence_id)
        )
    ).scalar() or 0
    if campaign_count or followup_count:
        raise HTTPException(
            status_code=409,
            detail="This sequence is used by a campaign and cannot be deleted. Duplicate or edit it instead.",
        )

    await db.delete(sequence)
    await db.flush()
