"""
Campaigns API routes.
"""

from datetime import timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.campaign import Campaign, CampaignContact
from app.models.user import User
from app.schemas.campaign import (
    CampaignCreate,
    CampaignOut,
    CampaignScheduleRequest,
    CampaignUpdate,
)
from app.security.auth import get_current_user
from app.services.campaign_service import CampaignService

# Statuses in which a campaign's definition may still be changed. Once it is
# scheduled, contacts have not been populated yet but the campaign is queued for
# launch, so we still allow edits and re-validation; from "running" onward the
# definition is frozen.
EDITABLE_STATUSES = {"draft", "scheduled"}

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


def _unique_copy_name(name: str, existing: set[str]) -> str:
    """Return "<name> (Copy)", or "(Copy 2)", "(Copy 3)"... if that is taken.

    Duplicating the same campaign twice previously produced two campaigns with
    identical names, which is impossible to tell apart in the list.
    """
    base = f"{name} (Copy)"
    if base not in existing:
        return base[:255]
    n = 2
    while f"{name} (Copy {n})" in existing:
        n += 1
    return f"{name} (Copy {n})"[:255]


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

    # A campaign that has sent, or is sending, must not have its message or
    # audience rewritten underneath it. The worker re-reads the campaign row for
    # every batch, so editing a running campaign would send the old text to the
    # contacts already processed and the new text to the rest -- with no record
    # of which got which. Duplicate it and edit the copy instead.
    if campaign.status not in EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot edit a campaign that is {campaign.status}. "
                "Duplicate it to make changes."
            ),
        )

    update_data = data.model_dump(exclude_unset=True)

    # Writing a message and picking a template are mutually exclusive choices.
    # Setting one clears the other, otherwise a campaign edited from template to
    # written text would keep a stale template_id and resolve_body's precedence
    # would decide the outcome instead of the user.
    if "message_body" in update_data:
        body = (update_data["message_body"] or "").strip() or None
        update_data["message_body"] = body
        if body and "template_id" not in update_data:
            update_data["template_id"] = None
    if update_data.get("template_id") and "message_body" not in update_data:
        update_data["message_body"] = None

    for key, value in update_data.items():
        setattr(campaign, key, value)

    # Changing the audience invalidates the pre-computed total.
    if "list_id" in update_data:
        campaign.total_contacts = 0

    # An edited campaign has not been checked in its new form. Send it back to
    # draft so it must pass validation again before it can be started -- without
    # this, editing a scheduled campaign into an invalid state (empty list, no
    # message) would still let Start succeed.
    #
    # Changing only the launch time is exempt: the campaign's content and
    # audience are unchanged, so re-validating adds nothing, and demoting it
    # would quietly cancel the scheduled send the user just set up.
    content_changes = set(update_data) - {"scheduled_start_at"}
    if campaign.status == "scheduled" and content_changes:
        campaign.status = "draft"
        campaign.scheduled_at = None

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


@router.post("/{campaign_id}/schedule")
async def schedule_campaign(
    campaign_id: int,
    data: CampaignScheduleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set the time a campaign should launch by itself, or clear it.

    Validates the campaign first (same checks as /validate) so a scheduled
    launch cannot fail at 3am for a reason we could have caught now. Pass
    scheduled_start_at=null to cancel the schedule and leave it validated for
    a manual start.
    """
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Only a campaign that has not started can be given a start time.
    if campaign.status not in EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot schedule a campaign that is {campaign.status}.",
        )

    if data.scheduled_start_at is None:
        campaign.scheduled_start_at = None
        await db.commit()
        await db.refresh(campaign)
        return {
            "success": True,
            "status": campaign.status,
            "scheduled_start_at": None,
            "message": "Schedule cleared. Campaign must be started manually.",
        }

    service = CampaignService(db)
    try:
        # Raises if there is no message, no audience, etc. A campaign that is
        # already scheduled is being rescheduled, which is allowed.
        await service.validate_and_schedule(
            campaign_id, allowed_statuses=("draft", "scheduled")
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    campaign.scheduled_start_at = data.scheduled_start_at
    await db.commit()
    await db.refresh(campaign)
    # Stamp UTC if the driver gave the value back naive, so the browser does
    # not read the timestamp as local time.
    when = campaign.scheduled_start_at
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return {
        "success": True,
        "status": campaign.status,
        "scheduled_start_at": when.isoformat(),
        "message": f"Campaign will send automatically at {when.isoformat()}",
    }


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

    names_result = await db.execute(select(Campaign.name))
    existing_names = set(names_result.scalars().all())

    # Copy the whole definition. This previously copied only the references and
    # silently dropped message_body and every sending rule, so duplicating a
    # campaign that had a written message produced a copy with NO message --
    # which then failed validation for a reason the user could not see.
    new_campaign = Campaign(
        name=_unique_copy_name(original.name, existing_names),
        description=original.description,
        list_id=original.list_id,
        template_id=original.template_id,
        message_body=original.message_body,
        sequence_id=original.sequence_id,
        gateway_setting_id=original.gateway_setting_id,
        # Sending rules are part of what makes a campaign worth duplicating.
        daily_limit=original.daily_limit,
        hourly_limit=original.hourly_limit,
        per_minute_limit=original.per_minute_limit,
        min_delay=original.min_delay,
        max_delay=original.max_delay,
        send_start_hour=original.send_start_hour,
        send_end_hour=original.send_end_hour,
        allow_weekends=original.allow_weekends,
        # Deliberately NOT copied: status (always a fresh draft), the stats
        # counters, and the scheduled/started/completed timestamps. A copy has
        # not sent anything yet.
        status="draft",
    )
    db.add(new_campaign)
    await db.flush()
    await db.refresh(new_campaign)
    return CampaignOut.model_validate(new_campaign)
