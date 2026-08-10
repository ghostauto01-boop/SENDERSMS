"""
Sequences API routes.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.sequence import Sequence, SequenceStep, SequenceVersion
from app.models.user import User
from app.schemas.sequence import SequenceCreate, SequenceUpdate, SequenceOut, SequenceStepCreate
from app.security.auth import get_current_user

router = APIRouter()


@router.get("/")
async def list_sequences(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all sequences."""
    query = select(Sequence)

    if search:
        query = query.where(Sequence.name.ilike(f"%{search}%"))

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Sequence.updated_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    sequences = result.scalars().all()

    return {
        "total": total,
        "items": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "current_version": s.current_version,
                "is_active": s.is_active,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat(),
            }
            for s in sequences
        ],
    }


@router.get("/{sequence_id}")
async def get_sequence(
    sequence_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a sequence with its steps."""
    result = await db.execute(select(Sequence).where(Sequence.id == sequence_id))
    sequence = result.scalar_one_or_none()
    if not sequence:
        raise HTTPException(status_code=404, detail="Sequence not found")

    steps_result = await db.execute(
        select(SequenceStep)
        .where(
            SequenceStep.sequence_id == sequence_id,
            SequenceStep.version == sequence.current_version,
        )
        .order_by(SequenceStep.step_order)
    )
    steps = steps_result.scalars().all()

    return {
        "id": sequence.id,
        "name": sequence.name,
        "description": sequence.description,
        "current_version": sequence.current_version,
        "is_active": sequence.is_active,
        "steps": [
            {
                "id": s.id,
                "step_order": s.step_order,
                "step_type": s.step_type,
                "config": s.config,
                "wait_duration_hours": s.wait_duration_hours,
                "template_id": s.template_id,
                "condition_type": s.condition_type,
                "condition_value": s.condition_value,
                "true_branch_step_order": s.true_branch_step_order,
                "false_branch_step_order": s.false_branch_step_order,
            }
            for s in steps
        ],
        "created_at": sequence.created_at.isoformat(),
        "updated_at": sequence.updated_at.isoformat(),
    }


@router.post("/", status_code=201)
async def create_sequence(
    data: SequenceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new sequence with steps."""
    sequence = Sequence(name=data.name, description=data.description)
    db.add(sequence)
    await db.flush()

    # Create steps
    for step_data in data.steps:
        step = SequenceStep(
            sequence_id=sequence.id,
            version=1,
            step_order=step_data.step_order,
            step_type=step_data.step_type,
            config=step_data.config,
            wait_duration_hours=step_data.wait_duration_hours,
            template_id=step_data.template_id,
            condition_type=step_data.condition_type,
            condition_value=step_data.condition_value,
            true_branch_step_order=step_data.true_branch_step_order,
            false_branch_step_order=step_data.false_branch_step_order,
        )
        db.add(step)

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
    """Update a sequence - creates a new version if steps changed."""
    result = await db.execute(select(Sequence).where(Sequence.id == sequence_id))
    sequence = result.scalar_one_or_none()
    if not sequence:
        raise HTTPException(status_code=404, detail="Sequence not found")

    if data.name is not None:
        sequence.name = data.name
    if data.description is not None:
        sequence.description = data.description

    if data.steps is not None:
        import json
        # Archive current steps as a version snapshot
        old_steps = await db.execute(
            select(SequenceStep)
            .where(
                SequenceStep.sequence_id == sequence_id,
                SequenceStep.version == sequence.current_version,
            )
            .order_by(SequenceStep.step_order)
        )
        old_steps_list = old_steps.scalars().all()
        snapshot = json.dumps([
            {
                "step_order": s.step_order,
                "step_type": s.step_type,
                "config": s.config,
                "wait_duration_hours": s.wait_duration_hours,
                "template_id": s.template_id,
                "condition_type": s.condition_type,
                "condition_value": s.condition_value,
                "true_branch_step_order": s.true_branch_step_order,
                "false_branch_step_order": s.false_branch_step_order,
            }
            for s in old_steps_list
        ])
        version = SequenceVersion(
            sequence_id=sequence.id,
            version=sequence.current_version,
            snapshot=snapshot,
        )
        db.add(version)

        # Increment version
        sequence.current_version += 1
        new_version = sequence.current_version

        # Create new steps
        for step_data in data.steps:
            step = SequenceStep(
                sequence_id=sequence.id,
                version=new_version,
                step_order=step_data.step_order,
                step_type=step_data.step_type,
                config=step_data.config,
                wait_duration_hours=step_data.wait_duration_hours,
                template_id=step_data.template_id,
                condition_type=step_data.condition_type,
                condition_value=step_data.condition_value,
                true_branch_step_order=step_data.true_branch_step_order,
                false_branch_step_order=step_data.false_branch_step_order,
            )
            db.add(step)

    await db.flush()
    return {"success": True, "version": sequence.current_version}


@router.delete("/{sequence_id}", status_code=204)
async def delete_sequence(
    sequence_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a sequence."""
    result = await db.execute(select(Sequence).where(Sequence.id == sequence_id))
    sequence = result.scalar_one_or_none()
    if not sequence:
        raise HTTPException(status_code=404, detail="Sequence not found")
    await db.delete(sequence)
    await db.flush()
