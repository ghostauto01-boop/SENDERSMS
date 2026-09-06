"""Campaign follow-up rules API — the Follow-up tab on a campaign."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.campaign import Campaign, CampaignContact
from app.models.campaign_followup import CampaignFollowUp, CampaignFollowUpLog
from app.models.contact import Contact
from app.models.user import User
from app.schemas.campaign_followup import (
    CampaignFollowUpCreate,
    CampaignFollowUpOut,
    CampaignFollowUpUpdate,
)
from app.security.auth import get_current_user
from app.services.campaign_followup_service import process_rule
from app.utils.naming import contact_display_name

router = APIRouter()


async def _get_campaign(db: AsyncSession, campaign_id: int) -> Campaign:
    campaign = (
        await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    ).scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    return campaign


async def _get_rule(db: AsyncSession, followup_id: int) -> CampaignFollowUp:
    rule = (
        await db.execute(select(CampaignFollowUp).where(CampaignFollowUp.id == followup_id))
    ).scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Follow-up not found")
    return rule


@router.get("/campaigns")
async def list_campaigns_with_followups(
    status: Optional[str] = Query(None, description="Filter, e.g. 'running'"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Campaigns the operator can attach follow-ups to, with their counts.

    The Follow-up tab opens with this: pick a campaign, then edit its chain.
    """
    query = select(Campaign)
    if status:
        query = query.where(Campaign.status == status)
    campaigns = (await db.execute(query.order_by(Campaign.created_at.desc()))).scalars().all()

    items = []
    for campaign in campaigns:
        rule_count = (
            await db.execute(
                select(func.count())
                .select_from(CampaignFollowUp)
                .where(CampaignFollowUp.campaign_id == campaign.id)
            )
        ).scalar() or 0
        sent = (
            await db.execute(
                select(func.count())
                .select_from(CampaignFollowUpLog)
                .where(
                    CampaignFollowUpLog.campaign_id == campaign.id,
                    CampaignFollowUpLog.status == "sent",
                )
            )
        ).scalar() or 0
        items.append(
            {
                "id": campaign.id,
                "name": campaign.name,
                "status": campaign.status,
                "total_contacts": campaign.total_contacts,
                "messages_sent": campaign.messages_sent,
                "replies": campaign.replies,
                "followup_count": rule_count,
                "followups_sent": sent,
            }
        )
    return {"total": len(items), "items": items}


@router.get("/campaigns/{campaign_id}")
async def list_campaign_followups(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The full follow-up chain for one campaign, with per-step stats."""
    campaign = await _get_campaign(db, campaign_id)
    rules = (
        await db.execute(
            select(CampaignFollowUp)
            .where(CampaignFollowUp.campaign_id == campaign_id)
            .order_by(CampaignFollowUp.step_order)
        )
    ).scalars().all()

    items = []
    for rule in rules:
        counts = {}
        rows = await db.execute(
            select(CampaignFollowUpLog.status, func.count())
            .where(CampaignFollowUpLog.followup_id == rule.id)
            .group_by(CampaignFollowUpLog.status)
        )
        for status, count in rows.all():
            counts[status] = count
        data = CampaignFollowUpOut.model_validate(rule).model_dump(mode="json")
        data["stats"] = counts
        items.append(data)

    return {
        "campaign": {
            "id": campaign.id,
            "name": campaign.name,
            "status": campaign.status,
            "total_contacts": campaign.total_contacts,
            "messages_sent": campaign.messages_sent,
            "replies": campaign.replies,
        },
        "total": len(items),
        "items": items,
    }


@router.post("/campaigns/{campaign_id}", response_model=CampaignFollowUpOut, status_code=201)
async def create_campaign_followup(
    campaign_id: int,
    data: CampaignFollowUpCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add a follow-up step to a campaign."""
    await _get_campaign(db, campaign_id)

    if not data.message_text and not data.template_id:
        raise HTTPException(422, "Write a follow-up message or choose a template")

    step_order = data.step_order
    if step_order is None:
        highest = (
            await db.execute(
                select(func.max(CampaignFollowUp.step_order)).where(
                    CampaignFollowUp.campaign_id == campaign_id
                )
            )
        ).scalar()
        step_order = (highest or 0) + 1

    clash = (
        await db.execute(
            select(CampaignFollowUp).where(
                CampaignFollowUp.campaign_id == campaign_id,
                CampaignFollowUp.step_order == step_order,
            )
        )
    ).scalars().first()
    if clash:
        raise HTTPException(409, f"Step {step_order} already exists for this campaign")

    rule = CampaignFollowUp(
        campaign_id=campaign_id,
        step_order=step_order,
        name=data.name or f"Follow-up {step_order}",
        message_text=data.message_text,
        template_id=data.template_id,
        delay_minutes=data.delay_minutes,
        stop_on_reply=data.stop_on_reply,
        stop_on_opt_out=data.stop_on_opt_out,
        stop_on_lead_status=data.stop_on_lead_status,
        send_start_hour=data.send_start_hour,
        send_end_hour=data.send_end_hour,
        is_active=data.is_active,
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return rule


@router.put("/{followup_id}", response_model=CampaignFollowUpOut)
async def update_campaign_followup(
    followup_id: int,
    data: CampaignFollowUpUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Edit a follow-up step."""
    rule = await _get_rule(db, followup_id)
    updates = data.model_dump(exclude_unset=True)

    if "step_order" in updates and updates["step_order"] != rule.step_order:
        clash = (
            await db.execute(
                select(CampaignFollowUp).where(
                    CampaignFollowUp.campaign_id == rule.campaign_id,
                    CampaignFollowUp.step_order == updates["step_order"],
                    CampaignFollowUp.id != rule.id,
                )
            )
        ).scalars().first()
        if clash:
            raise HTTPException(409, f"Step {updates['step_order']} already exists")

    for key, value in updates.items():
        setattr(rule, key, value)

    if not rule.message_text and not rule.template_id:
        raise HTTPException(422, "A follow-up needs a message or a template")

    await db.flush()
    await db.refresh(rule)
    return rule


@router.delete("/{followup_id}", status_code=204)
async def delete_campaign_followup(
    followup_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a follow-up step and its log."""
    rule = await _get_rule(db, followup_id)
    from sqlalchemy import delete as sa_delete

    await db.execute(
        sa_delete(CampaignFollowUpLog).where(CampaignFollowUpLog.followup_id == rule.id)
    )
    await db.delete(rule)
    await db.flush()


@router.post("/{followup_id}/run")
async def run_campaign_followup(
    followup_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Process this rule now instead of waiting for the sweep."""
    rule = await _get_rule(db, followup_id)
    result = await process_rule(db, rule)
    return {"success": True, **result}


@router.get("/{followup_id}/preview")
async def preview_campaign_followup(
    followup_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Who this rule would send to, stop, or keep waiting on — without sending.

    A dry run is the difference between confidently switching a chain on and
    hoping. Nothing is written to the database.
    """
    from datetime import datetime, timezone

    from app.services.campaign_followup_service import SENT_STATUSES, evaluate_contact

    rule = await _get_rule(db, followup_id)
    campaign = await _get_campaign(db, rule.campaign_id)

    handled = set(
        (
            await db.execute(
                select(CampaignFollowUpLog.contact_id).where(
                    CampaignFollowUpLog.followup_id == rule.id
                )
            )
        ).scalars().all()
    )

    candidates = (
        await db.execute(
            select(CampaignContact).where(
                CampaignContact.campaign_id == rule.campaign_id,
                CampaignContact.status.in_(SENT_STATUSES),
            )
        )
    ).scalars().all()

    now = datetime.now(timezone.utc)
    buckets: dict[str, list] = {"send": [], "stop": [], "wait": [], "done": []}

    for cc in candidates:
        contact = (
            await db.execute(select(Contact).where(Contact.id == cc.contact_id))
        ).scalar_one_or_none()
        name = contact_display_name(contact, contact.phone_number if contact else "")

        if cc.contact_id in handled:
            log = (
                await db.execute(
                    select(CampaignFollowUpLog).where(
                        CampaignFollowUpLog.followup_id == rule.id,
                        CampaignFollowUpLog.contact_id == cc.contact_id,
                    )
                )
            ).scalars().first()
            buckets["done"].append(
                {
                    "contact_id": cc.contact_id,
                    "name": name,
                    "status": log.status if log else "processed",
                    "reason": log.reason if log else None,
                }
            )
            continue

        decision, reason = await evaluate_contact(db, rule, campaign, cc, now)
        buckets[decision].append(
            {"contact_id": cc.contact_id, "name": name, "reason": reason}
        )

    return {
        "followup_id": rule.id,
        "campaign_id": campaign.id,
        "counts": {key: len(value) for key, value in buckets.items()},
        # Keep the payload small; the counts carry the headline.
        "will_send": buckets["send"][:50],
        "will_stop": buckets["stop"][:50],
        "waiting": buckets["wait"][:50],
        "already_processed": buckets["done"][:50],
    }


@router.get("/{followup_id}/log")
async def followup_log(
    followup_id: int,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """What this rule actually did, contact by contact."""
    await _get_rule(db, followup_id)

    query = select(CampaignFollowUpLog).where(CampaignFollowUpLog.followup_id == followup_id)
    if status:
        query = query.where(CampaignFollowUpLog.status == status)

    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar() or 0
    rows = (
        await db.execute(
            query.order_by(CampaignFollowUpLog.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).scalars().all()

    items = []
    for row in rows:
        contact = (
            await db.execute(select(Contact).where(Contact.id == row.contact_id))
        ).scalar_one_or_none()
        items.append(
            {
                "id": row.id,
                "contact_id": row.contact_id,
                "contact_name": contact_display_name(
                    contact, contact.phone_number if contact else ""
                ),
                "status": row.status,
                "reason": row.reason,
                "body_preview": row.body_preview,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return {"total": total, "items": items}
