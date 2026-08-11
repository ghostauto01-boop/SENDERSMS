"""
Campaigns API routes.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.campaign import Campaign, CampaignContact
from app.models.user import User
from app.schemas.campaign import CampaignCreate, CampaignUpdate, CampaignOut
from app.security.auth import get_current_user
from app.services.campaign_service import CampaignService

router = APIRouter()


@router.get("/")
async def list_campaigns(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    status: Optional[str] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List campaigns."""
    query = select(Campaign)

    if status:
        query = query.where(Campaign.status == status)
    if search:
        query = query.where(Campaign.name.ilike(f"%{search}%"))

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Campaign.updated_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    campaigns = result.scalars().all()

    return {
        "total": total,
        "items": [CampaignOut.model_validate(c) for c in campaigns],
    }


@router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single campaign."""
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@router.post("/", response_model=CampaignOut, status_code=201)
async def create_campaign(
    data: CampaignCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new campaign (draft)."""
    service = CampaignService(db)
    campaign = await service.create_campaign(data.model_dump())
    return campaign


@router.put("/{campaign_id}", response_model=CampaignOut)
async def update_campaign(
    campaign_id: int,
    data: CampaignUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update campaign settings."""
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(campaign, key, value)
    await db.flush()
    await db.refresh(campaign)
    return campaign


@router.post("/{campaign_id}/validate")
async def validate_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Validate and schedule a campaign."""
    service = CampaignService(db)
    try:
        campaign = await service.validate_and_schedule(campaign_id)
        return {"success": True, "status": campaign.status, "message": "Campaign validated and scheduled"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{campaign_id}/start")
async def start_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Start a campaign (queue for processing)."""
    service = CampaignService(db)
    try:
        campaign = await service.start_campaign(campaign_id)
        previous_status = "scheduled"
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Trigger campaign processing via Celery. If the broker is down we must
    # not leave the campaign stranded in "running" with nothing processing it.
    from app.tasks.campaign_tasks import process_campaign
    from app.tasks.queue import QueueUnavailable, enqueue

    # Commit BEFORE enqueuing. The worker is a separate process reading its own
    # connection: if the task is dispatched while this transaction is still
    # open, the worker can look up the campaign before the CampaignContact rows
    # (and the "running" status) are visible, find nothing to do, and exit --
    # leaving the campaign stuck at 0 sent until the next beat cycle, or
    # forever if beat is not running. This was reproducible on a fast broker.
    await db.commit()

    try:
        enqueue(process_campaign, campaign_id)
    except QueueUnavailable as e:
        campaign.status = previous_status
        campaign.started_at = None
        await db.commit()
        raise HTTPException(status_code=503, detail=str(e))

    return {"success": True, "status": campaign.status, "message": "Campaign started"}


@router.post("/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pause a running campaign."""
    service = CampaignService(db)
    try:
        campaign = await service.pause_campaign(campaign_id)
        return {"success": True, "status": campaign.status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{campaign_id}/resume")
async def resume_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Resume a paused campaign."""
    service = CampaignService(db)
    try:
        campaign = await service.resume_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    from app.tasks.campaign_tasks import process_campaign
    from app.tasks.queue import QueueUnavailable, enqueue

    try:
        enqueue(process_campaign, campaign_id)
    except QueueUnavailable as e:
        # Put it back to paused so the UI reflects reality.
        campaign.status = "paused"
        await db.flush()
        raise HTTPException(status_code=503, detail=str(e))

    return {"success": True, "status": campaign.status}


@router.post("/{campaign_id}/stop")
async def stop_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Stop a campaign permanently."""
    service = CampaignService(db)
    try:
        campaign = await service.stop_campaign(campaign_id)
        return {"success": True, "status": campaign.status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{campaign_id}", status_code=204)
async def delete_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a draft campaign."""
    service = CampaignService(db)
    try:
        await service.delete_draft(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{campaign_id}/analytics")
async def get_campaign_analytics(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get campaign analytics."""
    service = CampaignService(db)
    stats = await service.get_campaign_stats(campaign_id)
    if not stats:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Contact status breakdown
    cc_result = await db.execute(
        select(CampaignContact.status, func.count(CampaignContact.id))
        .where(CampaignContact.campaign_id == campaign_id)
        .group_by(CampaignContact.status)
    )
    contact_statuses = {row[0]: row[1] for row in cc_result}

    stats["contact_statuses"] = contact_statuses
    return stats


@router.post("/{campaign_id}/duplicate")
async def duplicate_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Duplicate a campaign as a new draft."""
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    original = result.scalar_one_or_none()
    if not original:
        raise HTTPException(status_code=404, detail="Campaign not found")

    new_campaign = Campaign(
        name=f"{original.name} (Copy)",
        description=original.description,
        list_id=original.list_id,
        template_id=original.template_id,
        sequence_id=original.sequence_id,
        gateway_setting_id=original.gateway_setting_id,
        status="draft",
    )
    db.add(new_campaign)
    await db.flush()
    await db.refresh(new_campaign)
    return CampaignOut.model_validate(new_campaign)
