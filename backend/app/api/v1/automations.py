"""Automation API — GoHighLevel-style If/Then reply automations."""

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.automation import Automation
from app.models.user import User
from app.schemas.automation import AutomationCreate, AutomationOut, AutomationUpdate
from app.security.auth import get_current_user
from app.services.automation_service import load_json

router = APIRouter()


def _dump(a: Automation) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "description": a.description,
        "is_enabled": a.is_enabled,
        "priority": a.priority,
        "trigger_type": a.trigger_type,
        "conditions": load_json(a.conditions_json),
        "match_all": a.match_all,
        "actions": load_json(a.actions_json),
        "times_triggered": a.times_triggered,
        "last_triggered_at": a.last_triggered_at.isoformat() if a.last_triggered_at else None,
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
    }


@router.get("/", response_model=dict)
async def list_automations(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        await db.execute(
            select(Automation).order_by(Automation.priority.asc(), Automation.id.asc())
        )
    ).scalars().all()
    return {"items": [_dump(a) for a in rows]}


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_automation(
    payload: AutomationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    automation = Automation(
        name=payload.name.strip(),
        description=payload.description,
        is_enabled=payload.is_enabled,
        priority=payload.priority,
        trigger_type=payload.trigger_type,
        conditions_json=json.dumps(payload.conditions, ensure_ascii=False),
        match_all=payload.match_all,
        actions_json=json.dumps(payload.actions, ensure_ascii=False),
    )
    db.add(automation)
    await db.commit()
    await db.refresh(automation)
    return _dump(automation)


@router.get("/{automation_id}")
async def get_automation(
    automation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    automation = await db.get(Automation, automation_id)
    if not automation:
        raise HTTPException(status_code=404, detail="Automation not found")
    return _dump(automation)


@router.put("/{automation_id}")
async def update_automation(
    automation_id: int,
    payload: AutomationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    automation = await db.get(Automation, automation_id)
    if not automation:
        raise HTTPException(status_code=404, detail="Automation not found")

    data = payload.model_dump(exclude_unset=True)
    if "name" in data and not (data["name"] or "").strip():
        raise HTTPException(status_code=400, detail="name cannot be blank")
    if "conditions" in data:
        automation.conditions_json = json.dumps(data.pop("conditions"), ensure_ascii=False)
    if "actions" in data:
        automation.actions_json = json.dumps(data.pop("actions"), ensure_ascii=False)
    for key, value in data.items():
        setattr(automation, key, value)
    await db.commit()
    await db.refresh(automation)
    return _dump(automation)


@router.delete("/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_automation(
    automation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    automation = await db.get(Automation, automation_id)
    if not automation:
        raise HTTPException(status_code=404, detail="Automation not found")
    await db.delete(automation)
    await db.commit()
    return None


@router.post("/test")
async def test_automation(
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Dry-run: which enabled automation(s) would fire for this reply text?

    Accepts {"text": "..."} (optionally with {"tag": "..."}) and returns the
    AI classification plus the matching automation names. Nothing is executed.
    """
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    from app.services.ai_classifier import classify
    classification = classify(text)

    rows = (
        await db.execute(
            select(Automation)
            .where(Automation.is_enabled == True, Automation.trigger_type == "inbound_reply")  # noqa: E712
            .order_by(Automation.priority.asc(), Automation.id.asc())
        )
    ).scalars().all()

    ctx = {
        "text": text,
        "sentiment": classification["sentiment"],
        "intent": classification["intent"],
        "labels": classification["labels"],
        "tags": {str(t).lower() for t in (body.get("tags") or [])},
    }

    from app.services.automation_service import AutomationService
    service = AutomationService(db)
    matched = []
    for automation in rows:
        if service._conditions_met(automation, ctx):
            matched.append({"id": automation.id, "name": automation.name})

    return {"classification": classification, "matched": matched}
