"""Auto-reply rule management.

Full CRUD -- the operator defines every rule; the app ships with none.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.autoreply import AutoReplyRule
from app.models.user import User
from app.schemas.autoreply import (
    AutoReplyRuleCreate,
    AutoReplyRuleOut,
    AutoReplyRuleUpdate,
    AutoReplyTestRequest,
    AutoReplyTestResponse,
)
from app.security.auth import get_current_user
from app.services.autoreply_service import AutoReplyService
from app.utils.templating import render_template

router = APIRouter()


def _validate(match_type: Optional[str], keywords: Optional[str]) -> None:
    """A keyword rule with no keywords would either never fire or fire on
    everything. Both are surprising, so reject it at the edge."""
    if match_type and match_type != "any":
        if not keywords or not keywords.strip():
            raise HTTPException(
                status_code=400,
                detail=f"Match type '{match_type}' needs at least one keyword. "
                "Use match type 'any' for a catch-all rule.",
            )


@router.get("/", response_model=dict)
async def list_rules(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """All rules, in the order they are evaluated."""
    result = await db.execute(
        select(AutoReplyRule).order_by(AutoReplyRule.priority.asc(), AutoReplyRule.id.asc())
    )
    rules = result.scalars().all()
    return {"items": [AutoReplyRuleOut.model_validate(r).model_dump() for r in rules]}


@router.post("/", response_model=AutoReplyRuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: AutoReplyRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _validate(payload.match_type, payload.keywords)
    rule = AutoReplyRule(**payload.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.get("/{rule_id}", response_model=AutoReplyRuleOut)
async def get_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rule = await db.get(AutoReplyRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.put("/{rule_id}", response_model=AutoReplyRuleOut)
async def update_rule(
    rule_id: int,
    payload: AutoReplyRuleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rule = await db.get(AutoReplyRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    data = payload.model_dump(exclude_unset=True)
    if "reply_body" in data and (not data["reply_body"] or not data["reply_body"].strip()):
        raise HTTPException(status_code=400, detail="reply_body cannot be blank")

    # Validate the post-update combination, not just what was sent.
    _validate(
        data.get("match_type", rule.match_type),
        data.get("keywords", rule.keywords),
    )

    for key, value in data.items():
        setattr(rule, key, value)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rule = await db.get(AutoReplyRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    await db.delete(rule)
    await db.commit()
    return None


@router.post("/test", response_model=AutoReplyTestResponse)
async def test_rules(
    payload: AutoReplyTestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Dry run. Shows which rule would answer, without sending anything.

    Cooldown is deliberately ignored here so the operator can test a rule
    repeatedly without waiting it out.
    """
    matches = await AutoReplyService(db).find_matching_rules(payload.body)
    if not matches:
        return AutoReplyTestResponse(matched=False)
    rule = matches[0]
    return AutoReplyTestResponse(
        matched=True,
        rule_id=rule.id,
        rule_name=rule.name,
        reply_body=render_template(rule.reply_body, None),
    )
