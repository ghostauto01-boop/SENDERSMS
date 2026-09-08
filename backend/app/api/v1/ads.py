"""SMS Ads Manager API.

Mounted at /api/v1/ads. Entirely additive: no existing route changes behaviour.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.ads import (
    AdsActivityLog,
    AdsAssignment,
    AdsAudience,
    AdsCalendarEvent,
    AdsCampaign,
    AdsCreative,
    AdsCreativeVersion,
    AdsEvent,
    AdsFollowUpStep,
    AdsFollowUpTask,
    AdsSet,
)
from app.models.contact import Contact, ContactTag, Tag
from app.models.contact_list import ContactList, ContactListMember
from app.models.conversation import Message
from app.models.suppression import SuppressionEntry
from app.models.user import User
from app.schemas.ads import (
    AddContactsIn,
    AttachAudienceIn,
    AudienceIn,
    AudienceOut,
    AudiencePatch,
    BulkActionIn,
    CalendarEventIn,
    CampaignIn,
    CampaignOut,
    CampaignPatch,
    ContactActionIn,
    CreativeIn,
    CreativeOut,
    CreativePatch,
    FollowUpStepIn,
    FollowUpTaskIn,
    OptimizeIn,
    SetIn,
    SetOut,
    SetPatch,
    SuppressionIn,
    TargetingPreviewIn,
    TrackClickIn,
)
from app.security.auth import get_current_user
from app.services import ads_service as svc

router = APIRouter()


async def _get_campaign(db: AsyncSession, campaign_id: int) -> AdsCampaign:
    row = (
        await db.execute(select(AdsCampaign).where(AdsCampaign.id == campaign_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Campaign not found")
    return row


async def _get_set(db: AsyncSession, set_id: int) -> AdsSet:
    row = (await db.execute(select(AdsSet).where(AdsSet.id == set_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "SMS set not found")
    return row


async def _get_creative(db: AsyncSession, creative_id: int) -> AdsCreative:
    row = (
        await db.execute(select(AdsCreative).where(AdsCreative.id == creative_id))
    ).scalar_one_or_none()
    if row is None or row.is_deleted:
        raise HTTPException(404, "Creative not found")
    return row


async def _get_audience(db: AsyncSession, audience_id: int) -> AdsAudience:
    row = (
        await db.execute(select(AdsAudience).where(AdsAudience.id == audience_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Audience not found")
    return row


# ==========================================================================
# Overview
# ==========================================================================


@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    campaigns = list((await db.execute(select(AdsCampaign))).scalars().all())
    active = [c for c in campaigns if c.status == "active"]

    totals = await svc._counts_for(db, [AdsAssignment.id.is_not(None)])
    followups_due = (
        await db.execute(
            select(func.count()).select_from(AdsFollowUpTask).where(
                AdsFollowUpTask.status == "pending", AdsFollowUpTask.due_at <= svc.now_utc()
            )
        )
    ).scalar() or 0
    meetings = (
        await db.execute(
            select(func.count()).select_from(AdsCalendarEvent).where(
                AdsCalendarEvent.event_type == "meeting", AdsCalendarEvent.status == "scheduled"
            )
        )
    ).scalar() or 0
    suppressed = (await db.execute(select(func.count()).select_from(SuppressionEntry))).scalar() or 0

    # Sent-over-time series for the dashboard chart (last 14 days).
    since = svc.now_utc() - timedelta(days=13)
    events = list(
        (
            await db.execute(
                select(AdsEvent).where(
                    AdsEvent.event_type.in_(("MESSAGE_SENT", "REPLY_RECEIVED")),
                    AdsEvent.created_at >= since,
                )
            )
        ).scalars().all()
    )
    series: dict[str, dict] = {}
    for i in range(14):
        day = (since + timedelta(days=i)).date().isoformat()
        series[day] = {"date": day, "sent": 0, "replies": 0}
    for e in events:
        key = svc.as_utc(e.created_at).date().isoformat()
        if key in series:
            if e.event_type == "MESSAGE_SENT":
                series[key]["sent"] += 1
            else:
                series[key]["replies"] += 1

    return {
        "active_campaigns": len(active),
        "total_campaigns": len(campaigns),
        "totals": totals,
        "followups_due": followups_due,
        "meetings": meetings,
        "suppressed": suppressed,
        "credits_used": totals["sent"],
        "series": list(series.values()),
    }


# ==========================================================================
# Campaigns
# ==========================================================================


@router.get("/campaigns")
async def list_campaigns(
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(AdsCampaign)
    if status:
        query = query.where(AdsCampaign.status == status)
    if search:
        query = query.where(AdsCampaign.name.ilike(f"%{search}%"))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    rows = list(
        (
            await db.execute(
                query.order_by(AdsCampaign.updated_at.desc())
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        ).scalars().all()
    )
    items = []
    for c in rows:
        stats = await svc._counts_for(
            db, [AdsAssignment.campaign_id == c.id], campaign_id=c.id
        )
        items.append(
            {
                **CampaignOut.model_validate(c).model_dump(),
                "stats": stats,
                "state": await svc.campaign_state(db, c),
                "score": svc.performance_score(c.objective, stats),
            }
        )
    return {"total": total, "items": items}


@router.post("/campaigns", status_code=201)
async def create_campaign(
    data: CampaignIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    campaign = AdsCampaign(**data.model_dump(), owner_id=user.id, status="draft")
    db.add(campaign)
    await db.flush()
    svc.log_activity(
        db, "campaign_created", campaign_id=campaign.id, actor=user.username, detail=campaign.name
    )
    await db.commit()
    await db.refresh(campaign)
    return CampaignOut.model_validate(campaign)


@router.get("/campaigns/{campaign_id}")
async def get_campaign(
    campaign_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    campaign = await _get_campaign(db, campaign_id)
    sets = list(
        (
            await db.execute(
                select(AdsSet).where(AdsSet.campaign_id == campaign_id).order_by(AdsSet.id)
            )
        ).scalars().all()
    )
    creatives = list(
        (
            await db.execute(
                select(AdsCreative)
                .where(AdsCreative.campaign_id == campaign_id, AdsCreative.is_deleted.is_(False))
                .order_by(AdsCreative.id)
            )
        ).scalars().all()
    )
    steps = list(
        (
            await db.execute(
                select(AdsFollowUpStep)
                .where(AdsFollowUpStep.campaign_id == campaign_id)
                .order_by(AdsFollowUpStep.step_order)
            )
        ).scalars().all()
    )
    return {
        **CampaignOut.model_validate(campaign).model_dump(),
        "state": await svc.campaign_state(db, campaign),
        "sets": [SetOut.model_validate(s).model_dump() for s in sets],
        "creatives": [CreativeOut.model_validate(c).model_dump() for c in creatives],
        "followup_steps": [
            {
                "id": s.id,
                "step_order": s.step_order,
                "name": s.name,
                "wait_hours": s.wait_hours,
                "condition": s.condition,
                "action": s.action,
                "body": s.body,
                "action_value": s.action_value,
                "is_active": s.is_active,
            }
            for s in steps
        ],
    }


@router.patch("/campaigns/{campaign_id}")
async def update_campaign(
    campaign_id: int,
    data: CampaignPatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Edit a campaign -- including while it is running.

    Unlike the legacy campaign editor (which freezes a running campaign because
    it has a single shared message), the ads manager versions its creatives and
    keeps per-contact assignments, so live edits are safe.
    """
    campaign = await _get_campaign(db, campaign_id)
    payload = data.model_dump(exclude_unset=True)
    if "status" in payload and payload["status"] not in (
        "draft", "scheduled", "active", "paused", "completed", "archived", "error",
    ):
        raise HTTPException(400, "Invalid status")
    if "optimize_metric" in payload and payload["optimize_metric"] not in svc.OPTIMIZE_METRICS:
        raise HTTPException(400, f"Invalid optimize_metric, expected one of {svc.OPTIMIZE_METRICS}")
    if "optimize_action" in payload and payload["optimize_action"] not in svc.OPTIMIZE_ACTIONS:
        raise HTTPException(400, f"Invalid optimize_action, expected one of {svc.OPTIMIZE_ACTIONS}")
    if "optimize_min_sends" in payload and (payload["optimize_min_sends"] or 0) < 1:
        raise HTTPException(400, "optimize_min_sends must be at least 1")
    for key, value in payload.items():
        setattr(campaign, key, value)
    campaign.updated_at = svc.now_utc()
    svc.log_activity(
        db,
        "campaign_updated",
        campaign_id=campaign.id,
        actor=user.username,
        detail=", ".join(payload.keys()),
    )
    await db.commit()
    await db.refresh(campaign)
    return CampaignOut.model_validate(campaign)


@router.delete("/campaigns/{campaign_id}", status_code=204)
async def delete_campaign(
    campaign_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    campaign = await _get_campaign(db, campaign_id)
    await db.delete(campaign)
    await db.commit()


@router.post("/campaigns/{campaign_id}/validate")
async def validate(
    campaign_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    return await svc.validate_campaign(db, await _get_campaign(db, campaign_id))


@router.post("/campaigns/{campaign_id}/simulate")
async def simulate(
    campaign_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    return await svc.simulate(db, await _get_campaign(db, campaign_id))


@router.post("/campaigns/{campaign_id}/launch")
async def launch(
    campaign_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    campaign = await _get_campaign(db, campaign_id)
    result = await svc.launch(db, campaign, actor=user.username)
    if not result["ok"]:
        raise HTTPException(400, "; ".join(result["errors"]))
    return result


@router.post("/campaigns/{campaign_id}/pause")
async def pause(
    campaign_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    campaign = await _get_campaign(db, campaign_id)
    if campaign.status not in ("active", "scheduled"):
        raise HTTPException(409, f"Cannot pause a campaign that is {campaign.status}")
    campaign.status = "paused"
    svc.log_activity(db, "campaign_paused", campaign_id=campaign.id, actor=user.username)
    await db.commit()
    return {"ok": True, "status": campaign.status}


@router.post("/campaigns/{campaign_id}/resume")
async def resume(
    campaign_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    campaign = await _get_campaign(db, campaign_id)
    if campaign.status != "paused":
        raise HTTPException(409, "Only a paused campaign can be resumed")
    campaign.status = "active"
    svc.log_activity(db, "campaign_resumed", campaign_id=campaign.id, actor=user.username)
    await db.commit()
    return {"ok": True, "status": campaign.status}


@router.post("/campaigns/{campaign_id}/complete")
async def complete(
    campaign_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    campaign = await _get_campaign(db, campaign_id)
    campaign.status = "completed"
    svc.log_activity(db, "campaign_completed", campaign_id=campaign.id, actor=user.username)
    await db.commit()
    return {"ok": True, "status": campaign.status}


@router.post("/campaigns/{campaign_id}/archive")
async def archive(
    campaign_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    campaign = await _get_campaign(db, campaign_id)
    campaign.status = "archived"
    svc.log_activity(db, "campaign_archived", campaign_id=campaign.id, actor=user.username)
    await db.commit()
    return {"ok": True, "status": campaign.status}


@router.post("/campaigns/{campaign_id}/duplicate")
async def duplicate(
    campaign_id: int,
    copy_audience: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    campaign = await _get_campaign(db, campaign_id)
    clone = await svc.duplicate_campaign(db, campaign, copy_audience=copy_audience)
    await db.commit()
    await db.refresh(clone)
    return CampaignOut.model_validate(clone)


@router.post("/campaigns/{campaign_id}/dispatch")
async def dispatch_now(
    campaign_id: int,
    limit: int = Query(25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Manually push the next slice (the background worker does this too)."""
    campaign = await _get_campaign(db, campaign_id)
    return await svc.dispatch_campaign(db, campaign, limit=limit, send_inline=True)


@router.post("/campaigns/{campaign_id}/contacts")
async def add_contacts(
    campaign_id: int,
    data: AddContactsIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Add contacts to a campaign -- including a running one.

    Only the NEW contacts are evaluated; existing assignments are untouched.
    """
    campaign = await _get_campaign(db, campaign_id)
    ads_set = None
    if data.set_id:
        ads_set = await _get_set(db, data.set_id)
    else:
        ads_set = (
            await db.execute(
                select(AdsSet).where(AdsSet.campaign_id == campaign_id).order_by(AdsSet.id).limit(1)
            )
        ).scalars().first()
    if ads_set is None:
        raise HTTPException(400, "Create an SMS set first")

    contacts: list[Contact] = []
    if data.contact_ids:
        contacts += list(
            (await db.execute(select(Contact).where(Contact.id.in_(data.contact_ids)))).scalars().all()
        )
    if data.list_ids:
        contacts += list(
            (
                await db.execute(
                    select(Contact).where(
                        Contact.id.in_(
                            select(ContactListMember.contact_id).where(
                                ContactListMember.list_id.in_(data.list_ids)
                            )
                        )
                    )
                )
            ).scalars().all()
        )
    if data.tags:
        ids = await svc._tagged_contact_ids(db, data.tags)
        if ids:
            contacts += list(
                (await db.execute(select(Contact).where(Contact.id.in_(ids)))).scalars().all()
            )
    # De-duplicate by id before screening.
    unique = {c.id: c for c in contacts}
    eligible, skipped = await svc.screen_contacts(db, campaign, list(unique.values()))
    result = await svc.assign_contacts(db, campaign, ads_set, eligible)
    svc.log_activity(
        db,
        "contacts_added",
        campaign_id=campaign.id,
        actor=user.username,
        detail=f"{result['assigned']} added, {sum(skipped.values())} skipped",
    )
    await db.commit()
    return {
        "selected": len(unique),
        "added": result["assigned"],
        "skipped": skipped,
        "per_creative": result["per_creative"],
    }


@router.post("/campaigns/{campaign_id}/rebuild-audience")
async def rebuild_audience(
    campaign_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    campaign = await _get_campaign(db, campaign_id)
    result = await svc.build_audience(db, campaign)
    await db.commit()
    return result


@router.get("/campaigns/{campaign_id}/audience")
async def campaign_audience(
    campaign_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(AdsAssignment).where(AdsAssignment.campaign_id == campaign_id)
    if status:
        query = query.where(AdsAssignment.send_status == status)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    rows = list(
        (
            await db.execute(
                query.order_by(AdsAssignment.id).offset((page - 1) * per_page).limit(per_page)
            )
        ).scalars().all()
    )
    contact_ids = [r.contact_id for r in rows]
    contacts = {
        c.id: c
        for c in (
            await db.execute(select(Contact).where(Contact.id.in_(contact_ids)))
        ).scalars().all()
    } if contact_ids else {}
    creatives = {
        c.id: c.name
        for c in (
            await db.execute(select(AdsCreative).where(AdsCreative.campaign_id == campaign_id))
        ).scalars().all()
    }
    items = []
    for r in rows:
        contact = contacts.get(r.contact_id)
        items.append(
            {
                "id": r.id,
                "contact_id": r.contact_id,
                "name": (
                    " ".join(filter(None, [contact.first_name, contact.last_name])).strip()
                    or (contact.business_name if contact else "")
                    if contact
                    else ""
                ),
                "phone_number": contact.phone_number if contact else r.phone_number,
                "creative_id": r.creative_id,
                "creative": creatives.get(r.creative_id),
                "send_status": r.send_status,
                "skip_reason": r.skip_reason,
                "reply_status": r.reply_status,
                "sent_at": svc.as_utc(r.sent_at),
            }
        )
    return {"total": total, "items": items}


@router.get("/campaigns/{campaign_id}/analytics")
async def analytics(
    campaign_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    return await svc.campaign_analytics(db, await _get_campaign(db, campaign_id))


@router.delete("/campaigns/{campaign_id}/audience/{assignment_id}", status_code=204)
async def remove_audience_contact(
    campaign_id: int,
    assignment_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove one contact from a campaign's audience (unsent only).

    Contacts that already received the message cannot be removed -- that would
    rewrite analytics history. Suppress them instead.
    """
    await _get_campaign(db, campaign_id)
    try:
        await svc.remove_assignment(db, assignment_id, campaign_id=campaign_id)
    except ValueError as e:
        if str(e) == "already_sent":
            raise HTTPException(
                409,
                "This contact was already sent the message, so it cannot be removed. "
                "Add it to the suppression list to block future sends.",
            )
        raise HTTPException(404, "Audience row not found")
    svc.log_activity(
        db, "contact_removed", campaign_id=campaign_id, entity_type="assignment",
        entity_id=assignment_id, actor=user.username,
    )
    await db.commit()


@router.post("/campaigns/{campaign_id}/audience/bulk-remove")
async def bulk_remove_audience(
    campaign_id: int,
    data: BulkActionIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove several unsent contacts at once. Already-sent rows are skipped
    (reported, not deleted) so analytics history stays intact."""
    await _get_campaign(db, campaign_id)
    result = await svc.bulk_remove_assignments(db, campaign_id, data.ids, actor=user.username)
    await db.commit()
    return result


@router.get("/campaigns/{campaign_id}/optimization")
async def optimization_status(
    campaign_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """Andromeda status: master switch, per-set toggles and what it would do."""
    return await svc.optimization_plan(db, await _get_campaign(db, campaign_id))


@router.post("/campaigns/{campaign_id}/optimize")
async def run_optimization_now(
    campaign_id: int,
    data: OptimizeIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Run one Andromeda pass. A real pass requires the master auto-optimize
    switch to be ON; dry runs always work for previewing."""
    campaign = await _get_campaign(db, campaign_id)
    result = await svc.run_optimization(db, campaign, actor=user.username, dry_run=data.dry_run)
    if not result.get("ok"):
        raise HTTPException(
            409,
            "Auto-optimization is turned OFF for this campaign. Turn it on first -- "
            "nothing automatic runs without your explicit opt-in.",
        )
    await db.commit()
    return result


@router.get("/campaigns/{campaign_id}/activity")
async def campaign_activity(
    campaign_id: int,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = list(
        (
            await db.execute(
                select(AdsActivityLog)
                .where(AdsActivityLog.campaign_id == campaign_id)
                .order_by(AdsActivityLog.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
    )
    return {
        "items": [
            {
                "id": r.id,
                "action": r.action,
                "detail": r.detail,
                "actor": r.actor,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "created_at": svc.as_utc(r.created_at),
            }
            for r in rows
        ]
    }


# ==========================================================================
# SMS Sets
# ==========================================================================


@router.get("/campaigns/{campaign_id}/sets")
async def list_sets(
    campaign_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    rows = list(
        (
            await db.execute(
                select(AdsSet).where(AdsSet.campaign_id == campaign_id).order_by(AdsSet.id)
            )
        ).scalars().all()
    )
    out = []
    for s in rows:
        stats = await svc._counts_for(
            db, [AdsAssignment.set_id == s.id], campaign_id=campaign_id, set_id=s.id
        )
        out.append({**SetOut.model_validate(s).model_dump(), "stats": stats})
    return {"items": out}


@router.post("/campaigns/{campaign_id}/sets", status_code=201)
async def create_set(
    campaign_id: int,
    data: SetIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_campaign(db, campaign_id)
    payload = data.model_dump()
    if payload.get("audience_id"):
        await _get_audience(db, payload["audience_id"])
    ads_set = AdsSet(campaign_id=campaign_id, **payload)
    db.add(ads_set)
    await db.flush()
    svc.log_activity(
        db, "set_created", campaign_id=campaign_id, entity_type="set", entity_id=ads_set.id,
        actor=user.username, detail=ads_set.name,
    )
    await db.commit()
    await db.refresh(ads_set)
    return SetOut.model_validate(ads_set)


@router.patch("/sets/{set_id}")
async def update_set(
    set_id: int, data: SetPatch, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    ads_set = await _get_set(db, set_id)
    payload = data.model_dump(exclude_unset=True)
    if payload.get("audience_id"):
        await _get_audience(db, payload["audience_id"])
    for key, value in payload.items():
        setattr(ads_set, key, value)
    svc.log_activity(
        db, "set_updated", campaign_id=ads_set.campaign_id, entity_type="set", entity_id=set_id,
        actor=user.username,
    )
    await db.commit()
    await db.refresh(ads_set)
    return SetOut.model_validate(ads_set)


@router.post("/sets/{set_id}/duplicate", status_code=201)
async def duplicate_set(
    set_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """Clone an SMS set with its creatives. The copy starts with a fresh
    audience (no contacts carried over)."""
    ads_set = await _get_set(db, set_id)
    clone = await svc.duplicate_set(db, ads_set, actor=user.username)
    await db.commit()
    await db.refresh(clone)
    return SetOut.model_validate(clone)


@router.post("/sets/{set_id}/pause-losers")
async def pause_losers(
    set_id: int,
    min_sends: int = Query(10, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """One click: keep the winning creative, pause the rest, move their pending
    contacts onto the winner."""
    try:
        result = await svc.pause_losing_creatives(db, set_id, min_sends=min_sends, actor=user.username)
    except ValueError as e:
        detail = {
            "not_found": "SMS set not found",
            "need_two_creatives": "Need at least 2 active creatives to pick a winner",
            "not_enough_data": f"No creative has {min_sends} sends yet -- too early to call a winner",
        }.get(str(e), "Could not pause losers")
        raise HTTPException(400 if str(e) != "not_found" else 404, detail)
    await db.commit()
    return result


@router.post("/sets/preview")
async def preview_unsaved_targeting(
    data: TargetingPreviewIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Live size estimate for targeting that has not been saved yet (set and
    audience editors). Touches nothing."""
    return await svc.preview_filters(db, data.model_dump())


@router.delete("/sets/{set_id}", status_code=204)
async def delete_set(
    set_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    ads_set = await _get_set(db, set_id)
    # Archive instead of destroying if it already sent anything -- history must
    # survive, so analytics stay attributable.
    sent = (
        await db.execute(
            select(func.count()).select_from(AdsAssignment).where(
                AdsAssignment.set_id == set_id, AdsAssignment.sent_at.is_not(None)
            )
        )
    ).scalar() or 0
    if sent:
        ads_set.status = "archived"
    else:
        await db.delete(ads_set)
    await db.commit()


@router.get("/sets/{set_id}/preview")
async def preview_set(
    set_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """Live audience count while the user edits targeting."""
    ads_set = await _get_set(db, set_id)
    campaign = await _get_campaign(db, ads_set.campaign_id)
    contacts = await svc.resolve_audience(db, ads_set)
    eligible, skipped = await svc.screen_contacts(db, campaign, contacts)
    plan = await svc.assign_contacts(db, campaign, ads_set, eligible, dry_run=True)
    return {
        "matched": len(contacts),
        "eligible": len(eligible),
        "skipped": skipped,
        "split": plan["per_creative"],
        "sample": [
            {
                "id": c.id,
                "name": " ".join(filter(None, [c.first_name, c.last_name])).strip() or c.business_name,
                "phone_number": c.phone_number,
            }
            for c in eligible[:10]
        ],
    }


# ==========================================================================
# Creatives
# ==========================================================================


@router.get("/sets/{set_id}/creatives")
async def list_creatives(
    set_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    rows = list(
        (
            await db.execute(
                select(AdsCreative)
                .where(AdsCreative.set_id == set_id, AdsCreative.is_deleted.is_(False))
                .order_by(AdsCreative.id)
            )
        ).scalars().all()
    )
    out = []
    for c in rows:
        stats = await svc._counts_for(
            db,
            [AdsAssignment.creative_id == c.id],
            campaign_id=c.campaign_id,
            set_id=c.set_id,
            creative_id=c.id,
        )
        out.append({**CreativeOut.model_validate(c).model_dump(), "stats": stats})
    return {"items": out}


@router.post("/sets/{set_id}/creatives", status_code=201)
async def create_creative(
    set_id: int,
    data: CreativeIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ads_set = await _get_set(db, set_id)
    creative = AdsCreative(set_id=set_id, campaign_id=ads_set.campaign_id, **data.model_dump())
    db.add(creative)
    await db.flush()
    await svc.ensure_version(db, creative)
    svc.log_activity(
        db, "creative_created", campaign_id=ads_set.campaign_id, entity_type="creative",
        entity_id=creative.id, actor=user.username, detail=creative.name,
    )
    await db.commit()
    await db.refresh(creative)
    return CreativeOut.model_validate(creative)


@router.patch("/creatives/{creative_id}")
async def update_creative(
    creative_id: int,
    data: CreativePatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Edit a creative safely, even mid-campaign.

    Changing the text writes a NEW version. Messages already sent keep their
    version, so historical analytics never move.
    """
    creative = await _get_creative(db, creative_id)
    payload = data.model_dump(exclude_unset=True)
    new_body = payload.pop("body", None)
    new_cta = payload.pop("cta", creative.cta)
    for key, value in payload.items():
        setattr(creative, key, value)
    if new_body is not None and new_body != creative.body:
        await svc.bump_version(db, creative, new_body, new_cta)
        svc.log_activity(
            db, "creative_version_created", campaign_id=creative.campaign_id,
            entity_type="creative", entity_id=creative.id, actor=user.username,
            detail=f"v{creative.current_version}",
        )
    elif new_cta != creative.cta:
        creative.cta = new_cta
    if payload.get("status") == "paused":
        svc.record_event(db, "CREATIVE_PAUSED", campaign_id=creative.campaign_id, creative_id=creative.id)
    svc.log_activity(
        db, "creative_updated", campaign_id=creative.campaign_id, entity_type="creative",
        entity_id=creative.id, actor=user.username,
    )
    await db.commit()
    await db.refresh(creative)
    return CreativeOut.model_validate(creative)


@router.get("/creatives/{creative_id}/versions")
async def creative_versions(
    creative_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    rows = list(
        (
            await db.execute(
                select(AdsCreativeVersion)
                .where(AdsCreativeVersion.creative_id == creative_id)
                .order_by(AdsCreativeVersion.version.desc())
            )
        ).scalars().all()
    )
    out = []
    for v in rows:
        sent = (
            await db.execute(
                select(func.count()).select_from(AdsAssignment).where(
                    AdsAssignment.creative_version_id == v.id, AdsAssignment.sent_at.is_not(None)
                )
            )
        ).scalar() or 0
        out.append(
            {"id": v.id, "version": v.version, "body": v.body, "cta": v.cta, "sent": sent,
             "created_at": svc.as_utc(v.created_at)}
        )
    return {"items": out}


@router.post("/creatives/{creative_id}/duplicate", status_code=201)
async def duplicate_creative(
    creative_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    creative = await _get_creative(db, creative_id)
    clone = AdsCreative(
        set_id=creative.set_id,
        campaign_id=creative.campaign_id,
        name=f"{creative.name} (Copy)"[:255],
        body=creative.body,
        cta=creative.cta,
        tracking_link=creative.tracking_link,
        allocation=creative.allocation,
        status="draft",
    )
    db.add(clone)
    await db.flush()
    await svc.ensure_version(db, clone)
    await db.commit()
    await db.refresh(clone)
    return CreativeOut.model_validate(clone)


@router.delete("/creatives/{creative_id}", status_code=204)
async def delete_creative(
    creative_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """Soft delete: historical analytics for this creative stay intact."""
    creative = await _get_creative(db, creative_id)
    creative.is_deleted = True
    creative.status = "archived"
    await db.execute(
        update(AdsAssignment)
        .where(AdsAssignment.creative_id == creative_id, AdsAssignment.send_status == "pending")
        .values(send_status="cancelled", skip_reason="creative_paused")
    )
    svc.log_activity(
        db, "creative_deleted", campaign_id=creative.campaign_id, entity_type="creative",
        entity_id=creative.id, actor=user.username,
    )
    await db.commit()


@router.post("/creatives/{creative_id}/promote")
async def promote_winner(
    creative_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """Give the winner 100% of future traffic; pause its siblings."""
    creative = await _get_creative(db, creative_id)
    siblings = list(
        (
            await db.execute(
                select(AdsCreative).where(
                    AdsCreative.set_id == creative.set_id, AdsCreative.is_deleted.is_(False)
                )
            )
        ).scalars().all()
    )
    for s in siblings:
        if s.id == creative.id:
            s.status = "active"
            s.allocation = 100.0
        else:
            s.status = "paused"
            s.allocation = 0.0
            svc.record_event(db, "CREATIVE_PAUSED", campaign_id=s.campaign_id, creative_id=s.id)
    ads_set = await _get_set(db, creative.set_id)
    ads_set.split_mode = "percentage"
    # Move contacts still waiting on a paused creative onto the winner so the
    # campaign keeps flowing instead of stalling on creative_paused.
    await db.execute(
        update(AdsAssignment)
        .where(
            AdsAssignment.set_id == creative.set_id,
            AdsAssignment.send_status == "pending",
            AdsAssignment.creative_id != creative.id,
        )
        .values(
            creative_id=creative.id,
            creative_version_id=(await svc.ensure_version(db, creative)).id,
            skip_reason=None,
            next_attempt_at=None,
        )
    )
    svc.log_activity(
        db, "creative_promoted", campaign_id=creative.campaign_id, entity_type="creative",
        entity_id=creative.id, actor=user.username, detail=creative.name,
    )
    await db.commit()
    return {"ok": True}


@router.get("/creatives/{creative_id}/analytics")
async def creative_analytics(
    creative_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """Full metrics for one creative: delivery / reply / click / open rates,
    score, winner status within its set, and per-version sends."""
    creative = await _get_creative(db, creative_id)
    campaign = await _get_campaign(db, creative.campaign_id)
    stats = await svc._counts_for(
        db,
        [AdsAssignment.creative_id == creative.id],
        campaign_id=creative.campaign_id,
        set_id=creative.set_id,
        creative_id=creative.id,
    )
    stats["score"] = svc.performance_score(campaign.objective, stats)

    # Winner status within the set.
    peers = list(
        (
            await db.execute(
                select(AdsCreative).where(
                    AdsCreative.set_id == creative.set_id,
                    AdsCreative.is_deleted.is_(False),
                )
            )
        ).scalars().all()
    )
    peer_stats = []
    for p in peers:
        s = await svc._counts_for(
            db,
            [AdsAssignment.creative_id == p.id],
            campaign_id=creative.campaign_id,
            set_id=creative.set_id,
            creative_id=p.id,
        )
        s["score"] = svc.performance_score(campaign.objective, s)
        peer_stats.append({"id": p.id, "name": p.name, "set_id": p.set_id, **s})
    winners = svc.pick_winners(peer_stats, min_sends=svc.DISPLAY_WINNER_MIN_SENDS)

    versions = list(
        (
            await db.execute(
                select(AdsCreativeVersion)
                .where(AdsCreativeVersion.creative_id == creative_id)
                .order_by(AdsCreativeVersion.version.desc())
            )
        ).scalars().all()
    )
    version_rows = []
    for v in versions:
        sent = (
            await db.execute(
                select(func.count()).select_from(AdsAssignment).where(
                    AdsAssignment.creative_version_id == v.id, AdsAssignment.sent_at.is_not(None)
                )
            )
        ).scalar() or 0
        version_rows.append(
            {"id": v.id, "version": v.version, "body": v.body, "cta": v.cta,
             "sent": sent, "created_at": svc.as_utc(v.created_at)}
        )
    return {
        **CreativeOut.model_validate(creative).model_dump(),
        "stats": stats,
        "is_winner": winners.get(creative.set_id) == creative.id,
        "needs_more_data": (stats.get("sent") or 0) < svc.DISPLAY_WINNER_MIN_SENDS,
        "peers": [
            {"id": p["id"], "name": p["name"], "sent": p["sent"], "score": p["score"],
             "reply_rate": p["reply_rate"], "is_winner": winners.get(creative.set_id) == p["id"]}
            for p in peer_stats
        ],
        "versions": version_rows,
    }


# ==========================================================================
# Saved Audiences (Meta-style reusable audiences)
# ==========================================================================


@router.get("/audiences")
async def list_audiences(
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(AdsAudience).order_by(AdsAudience.updated_at.desc())
    if search:
        query = query.where(AdsAudience.name.ilike(f"%{search}%"))
    rows = list((await db.execute(query)).scalars().all())
    items = []
    for a in rows:
        contacts = await svc.resolve_audience_contacts(db, a)
        items.append(
            {
                **AudienceOut.model_validate(a).model_dump(),
                "match_count": len(contacts),
                "list_count": len(a.csv("list_ids")),
                "explicit_contacts": len(a.csv("contact_ids")),
            }
        )
    return {"total": len(items), "items": items}


@router.post("/audiences", status_code=201)
async def create_audience(
    data: AudienceIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    audience = AdsAudience(**data.model_dump(), owner_id=user.id)
    db.add(audience)
    await db.flush()
    svc.log_activity(
        db, "audience_created", actor=user.username, entity_type="audience",
        entity_id=audience.id, detail=audience.name,
    )
    await db.commit()
    await db.refresh(audience)
    return AudienceOut.model_validate(audience)


@router.get("/audiences/{audience_id}")
async def get_audience(
    audience_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    audience = await _get_audience(db, audience_id)
    preview = await svc.preview_saved_audience(db, audience)
    return {**AudienceOut.model_validate(audience).model_dump(), "preview": preview}


@router.patch("/audiences/{audience_id}")
async def update_audience(
    audience_id: int,
    data: AudiencePatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    audience = await _get_audience(db, audience_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(audience, key, value)
    svc.log_activity(
        db, "audience_updated", actor=user.username, entity_type="audience",
        entity_id=audience.id, detail=audience.name,
    )
    await db.commit()
    await db.refresh(audience)
    return AudienceOut.model_validate(audience)


@router.delete("/audiences/{audience_id}", status_code=204)
async def delete_audience(
    audience_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    audience = await _get_audience(db, audience_id)
    # Unlink sets (their copied targeting stays -- only the link is dropped).
    await db.execute(
        update(AdsSet).where(AdsSet.audience_id == audience_id).values(audience_id=None)
    )
    await db.delete(audience)
    await db.commit()


@router.get("/audiences/{audience_id}/preview")
async def preview_audience(
    audience_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    return await svc.preview_saved_audience(db, await _get_audience(db, audience_id))


@router.post("/audiences/{audience_id}/duplicate", status_code=201)
async def duplicate_audience(
    audience_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    audience = await _get_audience(db, audience_id)
    names = set((await db.execute(select(AdsAudience.name))).scalars().all())
    base = f"{audience.name} (Copy)"
    candidate, n = base, 2
    while candidate in names:
        candidate = f"{base} ({n})"
        n += 1
    clone = AdsAudience(
        name=candidate[:255],
        description=audience.description,
        owner_id=user.id,
        list_ids=audience.list_ids,
        contact_ids=audience.contact_ids,
        include_tags=audience.include_tags,
        exclude_tags=audience.exclude_tags,
        include_statuses=audience.include_statuses,
        exclude_statuses=audience.exclude_statuses,
        city=audience.city,
        state=audience.state,
        industry=audience.industry,
        activity_filter=audience.activity_filter,
        exclude_campaign_ids=audience.exclude_campaign_ids,
    )
    db.add(clone)
    await db.commit()
    await db.refresh(clone)
    return AudienceOut.model_validate(clone)


@router.post("/audiences/{audience_id}/attach")
async def attach_audience(
    audience_id: int,
    data: AttachAudienceIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Use a saved audience in a campaign: either point an existing SMS set at
    it (replacing that set's targeting) or create a new set from it."""
    audience = await _get_audience(db, audience_id)
    campaign = await _get_campaign(db, data.campaign_id)
    if data.set_id:
        ads_set = await _get_set(db, data.set_id)
        if ads_set.campaign_id != campaign.id:
            raise HTTPException(400, "That SMS set belongs to a different campaign")
        svc._copy_audience_onto_set(audience, ads_set)
        svc.log_activity(
            db, "audience_attached", campaign_id=campaign.id, entity_type="set",
            entity_id=ads_set.id, actor=user.username, detail=audience.name,
        )
    else:
        ads_set = await svc.create_set_from_audience(
            db, campaign, audience, name=data.new_set_name, actor=user.username
        )
    await db.commit()
    await db.refresh(ads_set)
    contacts = await svc.resolve_audience(db, ads_set)
    eligible, skipped = await svc.screen_contacts(db, campaign, contacts)
    return {
        **SetOut.model_validate(ads_set).model_dump(),
        "matched": len(contacts),
        "eligible": len(eligible),
        "skipped": skipped,
    }


# ==========================================================================
# Click tracking (powers click rate + open rate)
# ==========================================================================


@router.post("/track/click")
async def track_click(
    data: TrackClickIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """Record a tracked-link tap (used by tests, manual logging and the
    redirect below when called with credentials)."""
    await svc.record_link_click(
        db,
        campaign_id=data.campaign_id,
        creative_id=data.creative_id,
        contact_id=data.contact_id,
        set_id=data.set_id,
        assignment_id=data.assignment_id,
    )
    await db.commit()
    return {"ok": True}


@router.get("/r/{assignment_id}")
async def click_redirect(assignment_id: int, db: AsyncSession = Depends(get_db)):
    """Public tracked-link redirect. The creative's tracking_link is wrapped
    with this URL at send time in a future release; for now any tap on
    /ads/r/<assignment_id> is recorded and bounced to the creative's link.

    No login required -- link taps come from phones, not browsers.
    """
    assignment = (
        await db.execute(select(AdsAssignment).where(AdsAssignment.id == assignment_id))
    ).scalar_one_or_none()
    if assignment is None:
        raise HTTPException(404, "Link not found")
    creative = (
        await db.execute(select(AdsCreative).where(AdsCreative.id == assignment.creative_id))
    ).scalar_one_or_none()
    await svc.record_link_click(
        db,
        campaign_id=assignment.campaign_id,
        creative_id=assignment.creative_id,
        contact_id=assignment.contact_id,
        set_id=assignment.set_id,
        assignment_id=assignment.id,
        detail="redirect",
    )
    await db.commit()
    target = (creative.tracking_link if creative else None) or "https://example.com"
    if not target.startswith(("http://", "https://")):
        target = f"https://{target}"
    return RedirectResponse(target, status_code=302)


# ==========================================================================
# Follow-up steps + Follow-Up Center
# ==========================================================================


@router.post("/campaigns/{campaign_id}/followup-steps", status_code=201)
async def create_step(
    campaign_id: int,
    data: FollowUpStepIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_campaign(db, campaign_id)
    step = AdsFollowUpStep(campaign_id=campaign_id, **data.model_dump())
    db.add(step)
    svc.log_activity(db, "followup_step_created", campaign_id=campaign_id, actor=user.username)
    await db.commit()
    await db.refresh(step)
    return {"id": step.id}


@router.patch("/followup-steps/{step_id}")
async def update_step(
    step_id: int,
    data: FollowUpStepIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    step = (
        await db.execute(select(AdsFollowUpStep).where(AdsFollowUpStep.id == step_id))
    ).scalar_one_or_none()
    if step is None:
        raise HTTPException(404, "Step not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(step, key, value)
    await db.commit()
    return {"ok": True}


@router.delete("/followup-steps/{step_id}", status_code=204)
async def delete_step(
    step_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    step = (
        await db.execute(select(AdsFollowUpStep).where(AdsFollowUpStep.id == step_id))
    ).scalar_one_or_none()
    if step is None:
        raise HTTPException(404, "Step not found")
    await db.execute(
        update(AdsFollowUpTask)
        .where(AdsFollowUpTask.step_id == step_id, AdsFollowUpTask.status == "pending")
        .values(status="cancelled", note="Step removed")
    )
    await db.delete(step)
    await db.commit()


@router.get("/followups")
async def list_followups(
    bucket: str = Query("today"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    now = svc.now_utc()
    end_of_day = now.replace(hour=23, minute=59, second=59)
    query = select(AdsFollowUpTask)
    if bucket == "today":
        query = query.where(
            AdsFollowUpTask.status == "pending",
            AdsFollowUpTask.due_at <= end_of_day,
            AdsFollowUpTask.due_at >= now.replace(hour=0, minute=0, second=0),
        )
    elif bucket == "overdue":
        query = query.where(AdsFollowUpTask.status == "pending", AdsFollowUpTask.due_at < now.replace(hour=0, minute=0, second=0))
    elif bucket == "upcoming":
        query = query.where(AdsFollowUpTask.status == "pending", AdsFollowUpTask.due_at > end_of_day)
    elif bucket == "waiting":
        query = query.where(AdsFollowUpTask.status == "waiting_reply")
    elif bucket == "completed":
        query = query.where(AdsFollowUpTask.status == "completed")
    elif bucket == "cancelled":
        query = query.where(AdsFollowUpTask.status == "cancelled")

    rows = list((await db.execute(query.order_by(AdsFollowUpTask.due_at).limit(200))).scalars().all())
    contacts = {
        c.id: c
        for c in (
            await db.execute(select(Contact).where(Contact.id.in_([r.contact_id for r in rows])))
        ).scalars().all()
    } if rows else {}
    campaigns = {
        c.id: c.name
        for c in (
            await db.execute(select(AdsCampaign).where(AdsCampaign.id.in_([r.campaign_id for r in rows])))
        ).scalars().all()
    } if rows else {}
    items = []
    for r in rows:
        contact = contacts.get(r.contact_id)
        items.append(
            {
                "id": r.id,
                "contact_id": r.contact_id,
                "contact": (
                    " ".join(filter(None, [contact.first_name, contact.last_name])).strip()
                    or contact.business_name
                    or contact.phone_number
                ) if contact else "Unknown",
                "business": contact.business_name if contact else None,
                "phone_number": contact.phone_number if contact else None,
                "campaign": campaigns.get(r.campaign_id),
                "campaign_id": r.campaign_id,
                "due_at": svc.as_utc(r.due_at),
                "status": r.status,
                "priority": r.priority,
                "body": r.body,
                "note": r.note,
            }
        )
    return {"items": items}


@router.post("/followups", status_code=201)
async def create_followup(
    data: FollowUpTaskIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    task = AdsFollowUpTask(
        campaign_id=data.campaign_id or 0,
        contact_id=data.contact_id,
        due_at=svc.as_utc(data.due_at) or svc.now_utc() + timedelta(days=1),
        note=data.note,
        body=data.body,
        priority=data.priority,
        status="pending",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return {"id": task.id}


@router.post("/followups/{task_id}/{action}")
async def followup_action(
    task_id: int,
    action: str,
    due_at: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = (
        await db.execute(select(AdsFollowUpTask).where(AdsFollowUpTask.id == task_id))
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(404, "Follow-up not found")
    if action == "complete":
        task.status = "completed"
        task.completed_at = svc.now_utc()
    elif action == "skip":
        task.status = "skipped"
    elif action == "cancel":
        task.status = "cancelled"
    elif action == "reschedule":
        if not due_at:
            raise HTTPException(400, "due_at is required to reschedule")
        task.due_at = svc.as_utc(due_at)
    elif action == "send-now":
        task.due_at = svc.now_utc() - timedelta(seconds=1)
    else:
        raise HTTPException(400, "Unknown action")
    await db.commit()
    return {"ok": True, "status": task.status}


@router.post("/followups/process")
async def process_followups_now(
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    return await svc.process_followups(db, send_inline=True)


# ==========================================================================
# Calendar
# ==========================================================================


@router.get("/calendar")
async def list_events(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(AdsCalendarEvent)
    if start:
        query = query.where(AdsCalendarEvent.starts_at >= svc.as_utc(start))
    if end:
        query = query.where(AdsCalendarEvent.starts_at <= svc.as_utc(end))
    rows = list((await db.execute(query.order_by(AdsCalendarEvent.starts_at))).scalars().all())
    contacts = {
        c.id: c
        for c in (
            await db.execute(
                select(Contact).where(Contact.id.in_([r.contact_id for r in rows if r.contact_id]))
            )
        ).scalars().all()
    } if rows else {}
    return {
        "items": [
            {
                "id": r.id,
                "title": r.title,
                "event_type": r.event_type,
                "contact_id": r.contact_id,
                "contact": (
                    " ".join(filter(None, [contacts[r.contact_id].first_name, contacts[r.contact_id].last_name])).strip()
                    or contacts[r.contact_id].business_name
                    or contacts[r.contact_id].phone_number
                )
                if r.contact_id in contacts
                else None,
                "campaign_id": r.campaign_id,
                "starts_at": svc.as_utc(r.starts_at),
                "duration_minutes": r.duration_minutes,
                "notes": r.notes,
                "priority": r.priority,
                "status": r.status,
            }
            for r in rows
        ]
    }


@router.post("/calendar", status_code=201)
async def create_event(
    data: CalendarEventIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    event = AdsCalendarEvent(**{**data.model_dump(), "starts_at": svc.as_utc(data.starts_at)})
    db.add(event)
    await db.flush()
    if event.event_type == "meeting":
        svc.record_event(
            db, "MEETING_BOOKED", campaign_id=event.campaign_id, contact_id=event.contact_id
        )
    await db.commit()
    await db.refresh(event)
    return {"id": event.id}


@router.patch("/calendar/{event_id}")
async def update_event(
    event_id: int,
    data: CalendarEventIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    event = (
        await db.execute(select(AdsCalendarEvent).where(AdsCalendarEvent.id == event_id))
    ).scalar_one_or_none()
    if event is None:
        raise HTTPException(404, "Event not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(event, key, svc.as_utc(value) if key == "starts_at" else value)
    await db.commit()
    return {"ok": True}


@router.delete("/calendar/{event_id}", status_code=204)
async def delete_event(
    event_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    event = (
        await db.execute(select(AdsCalendarEvent).where(AdsCalendarEvent.id == event_id))
    ).scalar_one_or_none()
    if event is None:
        raise HTTPException(404, "Event not found")
    await db.delete(event)
    await db.commit()


# ==========================================================================
# Suppression list (the EXISTING table)
# ==========================================================================


@router.get("/suppression")
async def list_suppression(
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(SuppressionEntry)
    if search:
        query = query.where(SuppressionEntry.phone_number.ilike(f"%{search}%"))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    rows = list(
        (
            await db.execute(
                query.order_by(SuppressionEntry.id.desc()).offset((page - 1) * per_page).limit(per_page)
            )
        ).scalars().all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "phone_number": r.phone_number,
                "contact_id": r.contact_id,
                "reason": r.reason,
                "source": r.source,
                "suppressed_at": svc.as_utc(r.suppressed_at),
            }
            for r in rows
        ],
    }


@router.post("/suppression", status_code=201)
async def add_suppression(
    data: SuppressionIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    contact = None
    if data.contact_id:
        contact = (
            await db.execute(select(Contact).where(Contact.id == data.contact_id))
        ).scalar_one_or_none()
    number = svc.normalize(data.phone_number) if data.phone_number else None
    if contact is None and number:
        contact = (
            await db.execute(select(Contact).where(Contact.phone_number == number))
        ).scalar_one_or_none()
    if contact is not None:
        await svc.suppress_contact(db, contact, reason=data.reason, source="manual")
    elif number:
        exists = (
            await db.execute(select(SuppressionEntry).where(SuppressionEntry.phone_number == number))
        ).scalar_one_or_none()
        if exists is None:
            db.add(SuppressionEntry(phone_number=number, reason=data.reason, source="manual"))
    else:
        raise HTTPException(400, "Provide a phone number or contact")
    svc.log_activity(db, "contact_suppressed", actor=user.username, detail=data.reason)
    await db.commit()
    return {"ok": True}


@router.delete("/suppression/{entry_id}", status_code=204)
async def remove_suppression(
    entry_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    entry = (
        await db.execute(select(SuppressionEntry).where(SuppressionEntry.id == entry_id))
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(404, "Entry not found")
    contact = (
        await db.execute(select(Contact).where(Contact.phone_number == entry.phone_number))
    ).scalar_one_or_none()
    if contact is not None:
        contact.is_opted_out = False
        contact.opted_out_at = None
    await db.delete(entry)
    await db.commit()


# ==========================================================================
# Contacts: CRM actions + timeline
# ==========================================================================


@router.post("/contacts/{contact_id}/action")
async def contact_action(
    contact_id: int,
    data: ContactActionIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Quick actions from a contact profile or conversation."""
    contact = (
        await db.execute(select(Contact).where(Contact.id == contact_id))
    ).scalar_one_or_none()
    if contact is None:
        raise HTTPException(404, "Contact not found")

    action = data.action
    if action == "interested":
        contact.lead_status = "interested"
        await db.execute(
            update(AdsAssignment)
            .where(AdsAssignment.contact_id == contact_id)
            .values(reply_status="positive")
        )
    elif action == "not_interested":
        contact.lead_status = "not_interested"
        if data.stop_campaigns:
            await db.execute(
                update(AdsAssignment)
                .where(AdsAssignment.contact_id == contact_id, AdsAssignment.send_status == "pending")
                .values(send_status="cancelled", skip_reason="not_interested")
            )
            await db.execute(
                update(AdsFollowUpTask)
                .where(AdsFollowUpTask.contact_id == contact_id, AdsFollowUpTask.status == "pending")
                .values(status="cancelled", note="Marked not interested")
            )
        if data.suppress:
            await svc.suppress_contact(db, contact, reason="Not interested")
    elif action == "status":
        if not data.value:
            raise HTTPException(400, "value is required")
        contact.lead_status = data.value
    elif action == "add_tag":
        await svc.add_tag(db, contact_id, data.value or "")
    elif action == "remove_tag":
        await svc.remove_tag(db, contact_id, data.value or "")
    elif action == "note":
        stamp = svc.now_utc().strftime("%Y-%m-%d %H:%M")
        contact.notes = f"{contact.notes or ''}\n[{stamp}] {data.note or ''}".strip()
    elif action == "converted":
        contact.lead_status = "converted"
        await db.execute(
            update(AdsAssignment)
            .where(AdsAssignment.contact_id == contact_id)
            .values(conversion_status="converted")
        )
        svc.record_event(db, "CONTACT_CONVERTED", contact_id=contact_id)
    elif action == "customer":
        contact.lead_status = "customer"
    elif action == "suppress":
        await svc.suppress_contact(db, contact, reason=data.note or "Manual suppression")
    else:
        raise HTTPException(400, "Unknown action")

    svc.log_activity(db, f"contact_{action}", entity_type="contact", entity_id=contact_id, actor=user.username)
    await db.commit()
    return {"ok": True, "lead_status": contact.lead_status}


@router.get("/contacts/{contact_id}/timeline")
async def contact_timeline(
    contact_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """Auto-generated chronological timeline for a contact."""
    contact = (
        await db.execute(select(Contact).where(Contact.id == contact_id))
    ).scalar_one_or_none()
    if contact is None:
        raise HTTPException(404, "Contact not found")

    items: list[dict] = [
        {"at": svc.as_utc(contact.created_at), "type": "created", "text": "Contact created"}
    ]
    for m in (
        await db.execute(
            select(Message).where(Message.contact_id == contact_id).order_by(Message.created_at).limit(200)
        )
    ).scalars().all():
        items.append(
            {
                "at": svc.as_utc(m.created_at),
                "type": "message_in" if m.direction == "incoming" else "message_out",
                "text": (m.body or "")[:160],
                "status": m.status,
            }
        )
    for e in (
        await db.execute(
            select(AdsEvent).where(AdsEvent.contact_id == contact_id).order_by(AdsEvent.created_at).limit(200)
        )
    ).scalars().all():
        items.append(
            {"at": svc.as_utc(e.created_at), "type": e.event_type.lower(), "text": e.detail or e.event_type}
        )
    for ev in (
        await db.execute(select(AdsCalendarEvent).where(AdsCalendarEvent.contact_id == contact_id))
    ).scalars().all():
        items.append(
            {"at": svc.as_utc(ev.starts_at), "type": "calendar", "text": f"{ev.event_type}: {ev.title}"}
        )
    items.sort(key=lambda x: x["at"] or svc.now_utc())

    assignments = list(
        (
            await db.execute(select(AdsAssignment).where(AdsAssignment.contact_id == contact_id))
        ).scalars().all()
    )
    campaigns = {
        c.id: c.name
        for c in (
            await db.execute(
                select(AdsCampaign).where(AdsCampaign.id.in_([a.campaign_id for a in assignments]))
            )
        ).scalars().all()
    } if assignments else {}
    creatives = {
        c.id: c.name
        for c in (
            await db.execute(
                select(AdsCreative).where(AdsCreative.id.in_([a.creative_id for a in assignments]))
            )
        ).scalars().all()
    } if assignments else {}

    return {
        "timeline": items,
        "campaigns": [
            {
                "campaign_id": a.campaign_id,
                "campaign": campaigns.get(a.campaign_id),
                "creative": creatives.get(a.creative_id),
                "send_status": a.send_status,
                "reply_status": a.reply_status,
                "conversion_status": a.conversion_status,
                "sent_at": svc.as_utc(a.sent_at),
            }
            for a in assignments
        ],
    }


# ==========================================================================
# Search, bulk actions, exports
# ==========================================================================


@router.get("/search")
async def global_search(
    q: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    term = f"%{q.strip()}%"
    campaigns = list(
        (await db.execute(select(AdsCampaign).where(AdsCampaign.name.ilike(term)).limit(10))).scalars().all()
    )
    sets = list((await db.execute(select(AdsSet).where(AdsSet.name.ilike(term)).limit(10))).scalars().all())
    creatives = list(
        (
            await db.execute(
                select(AdsCreative)
                .where(
                    or_(AdsCreative.name.ilike(term), AdsCreative.body.ilike(term)),
                    AdsCreative.is_deleted.is_(False),
                )
                .limit(10)
            )
        ).scalars().all()
    )
    contacts = list(
        (
            await db.execute(
                select(Contact)
                .where(
                    or_(
                        Contact.phone_number.ilike(term),
                        Contact.first_name.ilike(term),
                        Contact.last_name.ilike(term),
                        Contact.business_name.ilike(term),
                    )
                )
                .limit(10)
            )
        ).scalars().all()
    )
    return {
        "campaigns": [{"id": c.id, "name": c.name, "status": c.status} for c in campaigns],
        "sets": [{"id": s.id, "name": s.name, "campaign_id": s.campaign_id} for s in sets],
        "creatives": [{"id": c.id, "name": c.name, "campaign_id": c.campaign_id} for c in creatives],
        "contacts": [
            {
                "id": c.id,
                "name": " ".join(filter(None, [c.first_name, c.last_name])).strip() or c.business_name,
                "phone_number": c.phone_number,
            }
            for c in contacts
        ],
    }


@router.post("/campaigns/bulk")
async def bulk_campaigns(
    data: BulkActionIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    rows = list(
        (await db.execute(select(AdsCampaign).where(AdsCampaign.id.in_(data.ids)))).scalars().all()
    )
    changed = 0
    for campaign in rows:
        if data.action == "pause" and campaign.status in ("active", "scheduled"):
            campaign.status = "paused"
        elif data.action == "resume" and campaign.status == "paused":
            campaign.status = "active"
        elif data.action == "archive":
            campaign.status = "archived"
        elif data.action == "delete":
            await db.delete(campaign)
        else:
            continue
        changed += 1
    await db.commit()
    return {"changed": changed}


@router.post("/creatives/bulk")
async def bulk_creatives(
    data: BulkActionIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    rows = list(
        (await db.execute(select(AdsCreative).where(AdsCreative.id.in_(data.ids)))).scalars().all()
    )
    changed = 0
    for creative in rows:
        if data.action == "pause":
            creative.status = "paused"
        elif data.action == "resume":
            creative.status = "active"
        elif data.action == "archive":
            creative.status = "archived"
        elif data.action == "delete":
            creative.is_deleted = True
        else:
            continue
        changed += 1
    await db.commit()
    return {"changed": changed}


@router.post("/contacts/bulk")
async def bulk_contacts(
    data: BulkActionIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    contacts = list(
        (await db.execute(select(Contact).where(Contact.id.in_(data.ids)))).scalars().all()
    )
    changed = 0
    for contact in contacts:
        if data.action == "add_tag" and data.value:
            await svc.add_tag(db, contact.id, data.value)
        elif data.action == "remove_tag" and data.value:
            await svc.remove_tag(db, contact.id, data.value)
        elif data.action == "suppress":
            await svc.suppress_contact(db, contact, reason=data.value or "Bulk suppression")
        elif data.action == "status" and data.value:
            contact.lead_status = data.value
        else:
            continue
        changed += 1
    await db.commit()
    return {"changed": changed}


@router.get("/export/{kind}")
async def export(
    kind: str,
    campaign_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    if kind == "campaigns":
        writer.writerow(["id", "name", "status", "objective", "assigned", "sent", "replies", "score"])
        for c in (await db.execute(select(AdsCampaign))).scalars().all():
            s = await svc._counts_for(
                db, [AdsAssignment.campaign_id == c.id], campaign_id=c.id
            )
            writer.writerow(
                [c.id, c.name, c.status, c.objective, s["assigned"], s["sent"], s["replies"],
                 svc.performance_score(c.objective, s)]
            )
    elif kind == "creatives":
        writer.writerow(["id", "campaign_id", "name", "status", "assigned", "sent", "delivered",
                         "delivery_rate", "replies", "reply_rate", "positive", "clicks",
                         "click_rate", "opens", "open_rate", "opt_outs", "opt_out_rate", "score"])
        query = select(AdsCreative).where(AdsCreative.is_deleted.is_(False))
        if campaign_id:
            query = query.where(AdsCreative.campaign_id == campaign_id)
        objectives: dict[int, str] = dict(
            (await db.execute(select(AdsCampaign.id, AdsCampaign.objective))).all()
        )
        for c in (await db.execute(query)).scalars().all():
            s = await svc._counts_for(
                db,
                [AdsAssignment.creative_id == c.id],
                campaign_id=c.campaign_id,
                set_id=c.set_id,
                creative_id=c.id,
            )
            writer.writerow(
                [c.id, c.campaign_id, c.name, c.status, s["assigned"], s["sent"], s["delivered"],
                 s["delivery_rate"], s["replies"], s["reply_rate"], s["positive_replies"],
                 s["clicks"], s["click_rate"], s["opens"], s["open_rate"], s["opt_outs"],
                 s["opt_out_rate"], svc.performance_score(objectives.get(c.campaign_id, "replies"), s)]
            )
    elif kind == "suppression":
        writer.writerow(["phone_number", "reason", "source", "suppressed_at"])
        for r in (await db.execute(select(SuppressionEntry))).scalars().all():
            writer.writerow([r.phone_number, r.reason, r.source, svc.as_utc(r.suppressed_at)])
    elif kind == "followups":
        writer.writerow(["id", "campaign_id", "contact_id", "due_at", "status", "priority"])
        for r in (await db.execute(select(AdsFollowUpTask))).scalars().all():
            writer.writerow([r.id, r.campaign_id, r.contact_id, svc.as_utc(r.due_at), r.status, r.priority])
    elif kind == "audience":
        if not campaign_id:
            raise HTTPException(400, "campaign_id is required")
        writer.writerow(["contact_id", "phone_number", "creative_id", "send_status", "skip_reason", "reply_status", "sent_at"])
        for r in (
            await db.execute(select(AdsAssignment).where(AdsAssignment.campaign_id == campaign_id))
        ).scalars().all():
            writer.writerow(
                [r.contact_id, r.phone_number, r.creative_id, r.send_status, r.skip_reason,
                 r.reply_status, svc.as_utc(r.sent_at)]
            )
    else:
        raise HTTPException(400, "Unknown export type")

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=ads-{kind}.csv"},
    )


# ==========================================================================
# Settings + reference data
# ==========================================================================


@router.get("/reference")
async def reference(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Lists, tags and statuses the campaign builder needs."""
    lists = list((await db.execute(select(ContactList).order_by(ContactList.name))).scalars().all())
    counts = dict(
        (
            await db.execute(
                select(ContactListMember.list_id, func.count()).group_by(ContactListMember.list_id)
            )
        ).all()
    )
    tags = list((await db.execute(select(Tag).order_by(Tag.name))).scalars().all())
    statuses = list(
        (
            await db.execute(
                select(Contact.lead_status).distinct().where(Contact.lead_status.is_not(None))
            )
        ).scalars().all()
    )
    total_contacts = (await db.execute(select(func.count()).select_from(Contact))).scalar() or 0
    return {
        "lists": [{"id": l.id, "name": l.name, "count": counts.get(l.id, 0)} for l in lists],
        "tags": [t.name for t in tags],
        "statuses": sorted({s for s in statuses if s}),
        "total_contacts": total_contacts,
        "objectives": [
            {"value": "replies", "label": "Get replies"},
            {"value": "leads", "label": "Generate leads"},
            {"value": "meetings", "label": "Book meetings"},
            {"value": "website_visits", "label": "Drive website visits"},
            {"value": "promotion", "label": "Product promotion"},
            {"value": "reengagement", "label": "Customer re-engagement"},
            {"value": "followup", "label": "Follow-up"},
            {"value": "retention", "label": "Customer retention"},
            {"value": "review", "label": "Review request"},
            {"value": "custom", "label": "Custom"},
        ],
    }


@router.get("/activity")
async def global_activity(
    limit: int = Query(150, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = list(
        (
            await db.execute(
                select(AdsActivityLog).order_by(AdsActivityLog.created_at.desc()).limit(limit)
            )
        ).scalars().all()
    )
    return {
        "items": [
            {
                "id": r.id,
                "action": r.action,
                "detail": r.detail,
                "actor": r.actor,
                "campaign_id": r.campaign_id,
                "created_at": svc.as_utc(r.created_at),
            }
            for r in rows
        ]
    }
