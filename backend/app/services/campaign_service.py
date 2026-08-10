"""
Campaign service for campaign lifecycle management.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign, CampaignContact
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.sequence import Sequence, SequenceStep, SequenceVersion
from app.models.template import Template

logger = logging.getLogger(__name__)


class CampaignService:
    """Service for campaign operations."""

    VALID_STATUSES = {"draft", "scheduled", "running", "paused", "completed", "stopped", "failed"}
    TRANSITIONS = {
        "draft": {"scheduled"},
        "scheduled": {"running", "draft"},
        "running": {"paused", "completed", "stopped", "failed"},
        "paused": {"running", "stopped"},
        "completed": set(),
        "stopped": set(),
        "failed": {"draft"},
    }

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_campaign(self, data: dict) -> Campaign:
        """Create a new campaign (draft)."""
        campaign = Campaign(
            name=data["name"],
            description=data.get("description"),
            list_id=data.get("list_id"),
            template_id=data.get("template_id"),
            sequence_id=data.get("sequence_id"),
            gateway_setting_id=data.get("gateway_setting_id"),
            status="draft",
        )
        self.db.add(campaign)
        await self.db.flush()
        return campaign

    async def validate_and_schedule(self, campaign_id: int) -> Campaign:
        """
        Validate a campaign and move it to scheduled state.
        Validates: list, template, sequence, gateway.
        """
        result = await self.db.execute(select(Campaign).where(Campaign.id == campaign_id))
        campaign = result.scalar_one_or_none()
        if not campaign:
            raise ValueError("Campaign not found")
        if campaign.status != "draft":
            raise ValueError(f"Cannot schedule campaign in {campaign.status} status")

        # Validate requirements
        errors = []
        if not campaign.list_id:
            errors.append("No contact list selected")

        # Check list has contacts
        if campaign.list_id:
            count_result = await self.db.execute(
                select(func.count()).select_from(ContactListMember).where(
                    ContactListMember.list_id == campaign.list_id
                )
            )
            if count_result.scalar() == 0:
                errors.append("Contact list is empty")

        if not campaign.gateway_setting_id:
            errors.append("No SMS gateway selected")

        if errors:
            raise ValueError("; ".join(errors))

        # Snapshot the sequence version if using one
        if campaign.sequence_id:
            seq_result = await self.db.execute(
                select(Sequence).where(Sequence.id == campaign.sequence_id)
            )
            sequence = seq_result.scalar_one_or_none()
            if sequence:
                # Create version snapshot
                import json
                steps_result = await self.db.execute(
                    select(SequenceStep).where(
                        SequenceStep.sequence_id == sequence.id,
                        SequenceStep.version == sequence.current_version,
                    ).order_by(SequenceStep.step_order)
                )
                steps = steps_result.scalars().all()
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
                    for s in steps
                ])
                version = SequenceVersion(
                    sequence_id=sequence.id,
                    version=sequence.current_version,
                    snapshot=snapshot,
                )
                self.db.add(version)
                await self.db.flush()
                campaign.sequence_version_id = version.id

        campaign.status = "scheduled"
        campaign.scheduled_at = datetime.now(timezone.utc)
        await self.db.flush()
        return campaign

    async def start_campaign(self, campaign_id: int) -> Campaign:
        """
        Start a campaign: queue all contacts for processing.
        This creates CampaignContact records; actual sending is done by Celery workers.
        """
        result = await self.db.execute(select(Campaign).where(Campaign.id == campaign_id))
        campaign = result.scalar_one_or_none()
        if not campaign:
            raise ValueError("Campaign not found")
        if campaign.status not in ("scheduled", "paused"):
            raise ValueError(f"Cannot start campaign in {campaign.status} status")

        # If starting fresh, populate contacts
        if campaign.status == "scheduled":
            await self._populate_campaign_contacts(campaign)

        campaign.status = "running"
        campaign.started_at = datetime.now(timezone.utc)
        await self.db.flush()
        return campaign

    async def _populate_campaign_contacts(self, campaign: Campaign):
        """Add all list contacts to the campaign."""
        if not campaign.list_id:
            return

        # Get all contacts in the list
        members_result = await self.db.execute(
            select(ContactListMember).where(ContactListMember.list_id == campaign.list_id)
        )
        members = members_result.scalars().all()

        added = 0
        for member in members:
            # Check if already exists
            existing = await self.db.execute(
                select(CampaignContact).where(
                    CampaignContact.campaign_id == campaign.id,
                    CampaignContact.contact_id == member.contact_id,
                )
            )
            if existing.scalar_one_or_none():
                continue

            cc = CampaignContact(
                campaign_id=campaign.id,
                contact_id=member.contact_id,
                status="pending",
                sequence_step=0,
            )
            self.db.add(cc)
            added += 1

        campaign.total_contacts = added
        await self.db.flush()

    async def pause_campaign(self, campaign_id: int) -> Campaign:
        """Pause a running campaign."""
        result = await self.db.execute(select(Campaign).where(Campaign.id == campaign_id))
        campaign = result.scalar_one_or_none()
        if not campaign:
            raise ValueError("Campaign not found")
        if campaign.status != "running":
            raise ValueError("Only running campaigns can be paused")

        campaign.status = "paused"
        await self.db.flush()
        return campaign

    async def resume_campaign(self, campaign_id: int) -> Campaign:
        """Resume a paused campaign."""
        return await self.start_campaign(campaign_id)

    async def stop_campaign(self, campaign_id: int) -> Campaign:
        """Stop a campaign permanently."""
        result = await self.db.execute(select(Campaign).where(Campaign.id == campaign_id))
        campaign = result.scalar_one_or_none()
        if not campaign:
            raise ValueError("Campaign not found")
        if campaign.status not in ("running", "paused", "scheduled"):
            raise ValueError(f"Cannot stop campaign in {campaign.status} status")

        campaign.status = "stopped"
        campaign.completed_at = datetime.now(timezone.utc)

        # Cancel all pending contacts
        await self.db.execute(
            update(CampaignContact)
            .where(
                CampaignContact.campaign_id == campaign_id,
                CampaignContact.status.in_(["pending", "queued"]),
            )
            .values(status="cancelled", next_action_at=None)
        )

        await self.db.flush()
        return campaign

    async def get_campaign_stats(self, campaign_id: int) -> dict:
        """Get campaign statistics."""
        result = await self.db.execute(select(Campaign).where(Campaign.id == campaign_id))
        campaign = result.scalar_one_or_none()
        if not campaign:
            return {}

        return {
            "id": campaign.id,
            "name": campaign.name,
            "status": campaign.status,
            "total_contacts": campaign.total_contacts,
            "messages_sent": campaign.messages_sent,
            "messages_delivered": campaign.messages_delivered,
            "messages_failed": campaign.messages_failed,
            "replies": campaign.replies,
            "interested": campaign.interested,
            "delivery_rate": (
                round(campaign.messages_delivered / max(campaign.messages_sent, 1) * 100, 1)
            ),
            "reply_rate": (
                round(campaign.replies / max(campaign.messages_sent, 1) * 100, 1)
            ),
            "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
            "completed_at": campaign.completed_at.isoformat() if campaign.completed_at else None,
        }

    async def delete_draft(self, campaign_id: int):
        """Delete a draft campaign."""
        result = await self.db.execute(select(Campaign).where(Campaign.id == campaign_id))
        campaign = result.scalar_one_or_none()
        if not campaign:
            raise ValueError("Campaign not found")
        if campaign.status != "draft":
            raise ValueError("Only draft campaigns can be deleted")
        await self.db.delete(campaign)
        await self.db.flush()
