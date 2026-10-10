"""
Campaign service for campaign lifecycle management.

The lifecycle is a small state machine and this module is its only author:

    draft ──schedule(time)──▶ scheduled ──(time arrives / start)──▶ running
      │  ▲                      │  │  ▲                              │  │
      │  └───unschedule / edit──┘  │  └──────────── resume ──────────│──┤
      │                            └──pause──▶ paused ◀────pause─────┘  │
      └──start (validates inline)─────────────▶ running ─▶ completed   │
                                       stop: scheduled|running|paused|failed ─▶ stopped

``validate`` is a *report* and never moves a campaign. Every status change goes
through :meth:`CampaignService.transition`, which rejects anything the table
does not list, so the table can no longer drift away from the behaviour.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign, CampaignContact
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.followup import FollowUp
from app.models.sequence import Sequence, SequenceStep, SequenceVersion
from app.models.template import Template

logger = logging.getLogger(__name__)


class CampaignError(ValueError):
    """Base class. A plain ``ValueError`` from this service means "invalid input" (HTTP 400)."""


class CampaignNotFound(CampaignError):
    """No campaign has that id (HTTP 404)."""


class CampaignStateError(CampaignError):
    """The request is fine but the campaign's current state forbids it (HTTP 409)."""


class BreakerTripped(CampaignStateError):
    """The campaign was paused by the bounce circuit breaker and has not been acknowledged.

    Resuming is allowed -- a person may know better than a percentage -- but only
    deliberately (``acknowledge_breaker=true``), never by a reflexive click.
    """

    def __init__(self, campaign):
        self.campaign_id = campaign.id
        self.reason = campaign.paused_reason or ""
        super().__init__(
            f"This campaign was paused automatically. {self.reason} Fix the list first "
            "(verify the addresses), then repeat with acknowledge_breaker=true to resume anyway."
        )


@dataclass
class CampaignCheck:
    """Everything a validation pass learned. Reading it changes nothing."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sequence: Optional[Sequence] = None
    sequence_steps: list = field(default_factory=list)
    audience: dict = field(default_factory=dict)
    projection: dict = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return not self.errors


class CampaignService:
    """Service for campaign operations."""

    VALID_STATUSES = {"draft", "scheduled", "running", "paused", "completed", "stopped", "failed"}
    #: ``completed`` and ``stopped`` are final by design (a finished campaign is
    #: duplicated, not revived). Every other status must have a way out.
    TERMINAL_STATUSES = {"completed", "stopped"}
    #: The legal moves. Enforced by :meth:`transition`.
    TRANSITIONS = {
        "draft": {"scheduled", "running"},
        "scheduled": {"draft", "running", "paused", "stopped"},
        "running": {"paused", "completed", "stopped", "failed"},
        "paused": {"running", "scheduled", "stopped"},
        "failed": {"stopped"},
        "completed": set(),
        "stopped": set(),
    }
    #: Statuses from which a campaign that has sent nothing may be deleted.
    DELETABLE_STATUSES = {"draft", "scheduled", "failed"}

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _check_email_sender(self, campaign: Campaign) -> Optional[str]:
        """Return an error string when an EMAIL campaign has no usable sender."""
        from app.services import email_service

        account = await email_service.get_account(self.db, campaign.email_account_id)
        if account is None:
            account = await email_service.get_default_account(self.db)
        if account is None:
            return (
                "No email sender configured. Add a Brevo API key and From address on the "
                "Email Senders page, then choose it for this campaign."
            )
        usable, why = await email_service.account_is_usable(account, check_room=False)
        if not usable:
            return f"Email sender '{account.name}' cannot send right now ({why})."
        if not (campaign.subject or "").strip():
            # A subject can also come from the chosen template; if none is set
            # anywhere the email would arrive with "(no subject)".
            template_subject = None
            if campaign.template_id:
                from app.models.template import Template

                template = (
                    await self.db.execute(
                        select(Template).where(Template.id == campaign.template_id)
                    )
                ).scalar_one_or_none()
                template_subject = template.subject if template else None
            if not (template_subject or "").strip():
                return "Add a subject line for this email campaign (or pick a template with one)."
        return None

    async def _check_gateway(self, campaign: Campaign) -> Optional[str]:
        if (campaign.channel or "sms") == "email":
            return await self._check_email_sender(campaign)
        """Return an error string if no usable SMS gateway is available.

        A campaign may either point at an explicit GatewaySetting row or fall
        back to the gateway configured through the environment (the same one
        the direct-send path uses). Requiring gateway_setting_id outright made
        every campaign unschedulable, because nothing ever creates those rows.
        """
        if campaign.gateway_setting_id:
            from app.models.gateway import GatewaySetting

            result = await self.db.execute(
                select(GatewaySetting).where(
                    GatewaySetting.id == campaign.gateway_setting_id
                )
            )
            gateway = result.scalar_one_or_none()
            if not gateway:
                return "Selected SMS gateway no longer exists"
            if not gateway.is_enabled:
                return "Selected SMS gateway is disabled"
            return None

        # No explicit gateway: fall back to the environment-configured one.
        from app.config import settings

        if not settings.smsgate_configured:
            return (
                "No SMS gateway configured. Set SMSGATE_BASE_URL, SMSGATE_USERNAME "
                "and SMSGATE_PASSWORD, or select a gateway for this campaign."
            )
        return None

    async def create_campaign(self, data: dict) -> Campaign:
        """Create a new campaign (draft)."""
        campaign = Campaign(
            name=data["name"],
            description=data.get("description"),
            channel=data.get("channel") if data.get("channel") in ("sms", "email") else "sms",
            list_id=data.get("list_id"),
            template_id=data.get("template_id"),
            message_body=(data.get("message_body") or "").strip() or None,
            sequence_id=data.get("sequence_id"),
            gateway_setting_id=data.get("gateway_setting_id"),
            email_account_id=data.get("email_account_id"),
            fallback_email_account_id=data.get("fallback_email_account_id"),
            attachments=data.get("attachments"),
            subject=(data.get("subject") or "").strip() or None,
            html_body=data.get("html_body"),
            track_opens=bool(data.get("track_opens", True)),
            track_clicks=bool(data.get("track_clicks", True)),
            # Optional future launch time; None means "start it manually".
            scheduled_start_at=data.get("scheduled_start_at"),
            status="draft",
        )
        self.db.add(campaign)
        await self.db.flush()
        return campaign

    async def resolve_body(self, campaign: Campaign, template_id: int | None = None) -> str | None:
        """Return the raw (unrendered) message text a campaign will send.

        Precedence: an explicit sequence-step template, then the campaign's own
        inline message_body, then its selected template. Returns None when the
        campaign has no message at all -- callers must treat that as an error
        rather than inventing a placeholder, since anything we invent would be
        texted verbatim to real people.
        """
        tid = template_id or (None if campaign.message_body else campaign.template_id)
        if tid:
            row = await self.db.execute(select(Template).where(Template.id == tid))
            template = row.scalar_one_or_none()
            if template and (template.body or "").strip():
                return template.body
            if template_id:
                return None
        if campaign.message_body and campaign.message_body.strip():
            return campaign.message_body
        if campaign.template_id:
            row = await self.db.execute(select(Template).where(Template.id == campaign.template_id))
            template = row.scalar_one_or_none()
            if template and (template.body or "").strip():
                return template.body
        return None

    # ------------------------------------------------------------------
    # Lookup and the one place status changes
    # ------------------------------------------------------------------

    async def _get(self, campaign_id: int) -> Campaign:
        result = await self.db.execute(select(Campaign).where(Campaign.id == campaign_id))
        campaign = result.scalar_one_or_none()
        if not campaign:
            raise CampaignNotFound("Campaign not found")
        return campaign

    def transition(self, campaign: Campaign, new_status: str) -> None:
        """Move ``campaign`` to ``new_status`` or raise if the table forbids it.

        ``TRANSITIONS`` used to be documentation: nothing consulted it, so it had
        already drifted from what the endpoints did. Routing every change through
        here keeps the two in step (and a test derives the real graph from the
        HTTP API to prove it).
        """
        current = campaign.status
        if new_status == current:
            return
        if new_status not in self.TRANSITIONS.get(current, set()):
            raise CampaignStateError(
                f"A campaign that is {current} cannot become {new_status}."
            )
        campaign.status = new_status

    # ------------------------------------------------------------------
    # Validation: a report first, a state change only when asked for
    # ------------------------------------------------------------------

    async def _collect(self, campaign: Campaign) -> CampaignCheck:
        """Run every launch check and return what was found. Never mutates."""
        check = CampaignCheck()
        errors = check.errors
        if not campaign.list_id:
            errors.append("No contact list selected")

        if campaign.list_id:
            members = (
                await self.db.execute(
                    select(func.count()).select_from(ContactListMember).where(
                        ContactListMember.list_id == campaign.list_id
                    )
                )
            ).scalar() or 0
            check.audience["list_members"] = members
            if members == 0:
                errors.append("Contact list is empty")

        # A campaign with neither an inline message nor a usable template used
        # to fall through to a hardcoded "Hello" at send time and blast that
        # literal word to every contact. Refuse to launch instead.
        if not campaign.sequence_id and not await self.resolve_body(campaign):
            errors.append("No message to send. Write a message or choose a template.")

        if campaign.sequence_id:
            sequence = (
                await self.db.execute(
                    select(Sequence).where(Sequence.id == campaign.sequence_id)
                )
            ).scalar_one_or_none()
            if not sequence:
                errors.append("Selected sequence no longer exists")
            elif not sequence.is_active:
                errors.append("Selected sequence is inactive")
            else:
                check.sequence = sequence
                steps = list(
                    (
                        await self.db.execute(
                            select(SequenceStep)
                            .where(
                                SequenceStep.sequence_id == sequence.id,
                                SequenceStep.version == sequence.current_version,
                            )
                            .order_by(SequenceStep.step_order)
                        )
                    ).scalars().all()
                )
                from app.services.sequence_service import validate_sequence_steps

                try:
                    check.sequence_steps = await validate_sequence_steps(
                        self.db,
                        steps,
                        allow_campaign_message_fallback=bool(await self.resolve_body(campaign)),
                    )
                except ValueError as exc:
                    errors.append(f"Sequence is invalid: {exc}")

        gateway_error = await self._check_gateway(campaign)
        if gateway_error:
            errors.append(gateway_error)

        if campaign.list_id and check.audience.get("list_members"):
            await self._analyse_audience(campaign, check)
        return check

    async def _members(self, campaign: Campaign) -> list[Contact]:
        """Every contact on the campaign's list."""
        return list(
            (
                await self.db.execute(
                    select(Contact)
                    .join(ContactListMember, ContactListMember.contact_id == Contact.id)
                    .where(ContactListMember.list_id == campaign.list_id)
                    .order_by(Contact.id)
                )
            ).scalars().all()
        )

    async def _analyse_audience(self, campaign: Campaign, check: CampaignCheck) -> None:
        """Who would actually be messaged, and how long that takes under the sending rules.

        Uses the same eligibility rules the send path applies, so the numbers in a
        validation report are the numbers the campaign will really produce.
        """
        from app.services import sending_limits

        channel = campaign.channel or "sms"
        contacts = await self._members(campaign)
        if channel == "email":
            from app.services import email_service

            eligible, skipped = await email_service.screen_contacts_for_email(self.db, contacts)
        else:
            from app.services.list_hygiene import contact_is_blocked_from_send

            eligible, skipped = [], {}
            for contact in contacts:
                reason = contact_is_blocked_from_send(contact)
                if reason:
                    skipped[reason] = skipped.get(reason, 0) + 1
                else:
                    eligible.append(contact)

        check.audience.update(
            list_members=len(contacts), sendable=len(eligible), skipped=skipped
        )
        skipped_total = sum(skipped.values())
        if skipped_total:
            detail = ", ".join(f"{n} {reason}" for reason, n in sorted(skipped.items()))
            check.warnings.append(
                f"{skipped_total} of {len(contacts)} contacts will be skipped ({detail})."
            )
        if channel == "email" and contacts and not eligible:
            check.errors.append(
                "None of the contacts on this list can be emailed "
                f"({', '.join(f'{n} {r}' for r, n in sorted(skipped.items()))})."
            )

        account_ids = None
        if channel == "email":
            from app.services import email_service

            account = await email_service.get_account(self.db, campaign.email_account_id)
            account = account or await email_service.get_default_account(self.db)
            account_ids = [account.id] if account else None
        check.projection = await sending_limits.project_send(
            self.db, channel=channel, sendable=len(eligible), account_ids=account_ids,
        )
        warning = sending_limits.projection_warning(check.projection)
        if warning:
            check.warnings.append(warning)

    async def validate_report(self, campaign_id: int) -> dict:
        """Check a campaign and report. **Changes nothing**, however often it is called.

        This is what ``POST /campaigns/{id}/validate`` returns. It used to be
        ``validate_and_schedule``, so a "check" moved the campaign to
        ``scheduled`` -- and the launcher starts any scheduled campaign whose
        launch time has passed, so validating could send to a whole list.
        """
        campaign = await self._get(campaign_id)
        check = await self._collect(campaign)
        warnings = list(check.warnings)
        if campaign.status not in ("draft", "scheduled"):
            warnings.append(
                f"This campaign is {campaign.status}; the checks below describe its "
                "definition only and nothing can be launched from here."
            )
        if check.valid:
            message = (
                "Campaign is valid. Nothing was changed: use Start to send now, "
                "or Schedule to set a launch time."
            )
        else:
            message = (
                f"{len(check.errors)} problem(s) found. Nothing was changed: fix "
                "them and validate again."
            )
        return {
            "valid": check.valid,
            "ok": check.valid,
            "status": campaign.status,
            "changed": False,
            "errors": check.errors,
            "warnings": warnings,
            "audience": check.audience,
            "projection": check.projection,
            "message": message,
        }

    async def _freeze_sequence(self, campaign: Campaign, check: CampaignCheck) -> None:
        """Pin the exact sequence definition this campaign will run.

        Editing the reusable sequence later must not rewrite automation that is
        already running. Re-freezing an unchanged sequence reuses its snapshot.
        """
        sequence = check.sequence
        if sequence is None:
            # A campaign edited from "sequence" back to a single message must
            # not keep executing its stale sequence snapshot.
            campaign.sequence_version_id = None
            return
        from app.services.sequence_service import snapshot_steps

        snapshot = snapshot_steps(check.sequence_steps)
        if campaign.sequence_version_id:
            current = (
                await self.db.execute(
                    select(SequenceVersion).where(SequenceVersion.id == campaign.sequence_version_id)
                )
            ).scalar_one_or_none()
            if (
                current is not None
                and current.sequence_id == sequence.id
                and current.version == sequence.current_version
                and current.snapshot == snapshot
            ):
                return
        version = SequenceVersion(
            sequence_id=sequence.id, version=sequence.current_version, snapshot=snapshot
        )
        self.db.add(version)
        await self.db.flush()
        campaign.sequence_version_id = version.id

    async def validate_and_schedule(
        self,
        campaign_id: int,
        allowed_statuses: tuple = ("draft",),
    ) -> Campaign:
        """Validate, then move the campaign to ``scheduled``. A real state change.

        Only :meth:`set_schedule` (the ``/schedule`` endpoint) and direct callers
        that mean to schedule should use this. ``/validate`` must use
        :meth:`validate_report`.

        ``allowed_statuses`` widens the states this may be called from: the
        schedule endpoint re-validates an already-``scheduled`` campaign when the
        launch time changes.
        """
        campaign = await self._get(campaign_id)
        if campaign.status not in allowed_statuses:
            raise CampaignStateError(f"Cannot schedule campaign in {campaign.status} status")

        check = await self._collect(campaign)
        if check.errors:
            raise ValueError("; ".join(check.errors))

        await self._freeze_sequence(campaign, check)
        self.transition(campaign, "scheduled")
        campaign.scheduled_at = datetime.now(timezone.utc)
        await self.db.flush()
        return campaign

    async def set_schedule(self, campaign_id: int, start_at: Optional[datetime]) -> dict:
        """Set, change or cancel a campaign's automatic launch time.

        Returns ``{"campaign", "changed", "message"}``. ``changed`` is False when
        the request left the campaign exactly as it was, and the message says so
        instead of reporting a success that did nothing.
        """
        campaign = await self._get(campaign_id)
        if campaign.status not in ("draft", "scheduled"):
            raise CampaignStateError(f"Cannot schedule a campaign that is {campaign.status}.")

        if start_at is None:
            if campaign.status == "scheduled":
                # Cancelling the schedule must actually undo it. This used to clear
                # only the timestamp and report "Schedule cleared" while the
                # campaign stayed ``scheduled`` -- looking armed, doing nothing.
                self.transition(campaign, "draft")
                campaign.scheduled_start_at = None
                campaign.scheduled_at = None
                await self.db.flush()
                return {
                    "campaign": campaign,
                    "changed": True,
                    "message": "Schedule cancelled. The campaign is a draft again.",
                }
            if campaign.scheduled_start_at is not None:
                campaign.scheduled_start_at = None
                await self.db.flush()
                return {
                    "campaign": campaign,
                    "changed": True,
                    "message": "Launch time removed. The campaign is still a draft.",
                }
            return {
                "campaign": campaign,
                "changed": False,
                "message": "Nothing to cancel: this campaign has no schedule.",
            }

        previous_status = campaign.status
        previous_time = campaign.scheduled_start_at
        # Raises if there is no message, no audience, etc. A campaign that is
        # already scheduled is being rescheduled, which is allowed.
        await self.validate_and_schedule(campaign_id, allowed_statuses=("draft", "scheduled"))
        campaign.scheduled_start_at = start_at
        await self.db.flush()
        same_time = (
            previous_time is not None
            and previous_time.replace(tzinfo=previous_time.tzinfo or timezone.utc)
            == start_at.replace(tzinfo=start_at.tzinfo or timezone.utc)
        )
        changed = not (previous_status == "scheduled" and same_time)
        return {
            "campaign": campaign,
            "changed": changed,
            "message": (
                f"Campaign will send automatically at {start_at.isoformat()}"
                if changed
                else "That launch time was already set. Nothing changed."
            ),
        }

    async def start_campaign(
        self, campaign_id: int, *, acknowledge_breaker: bool = False
    ) -> Campaign:
        """Start a campaign: queue all contacts for processing.

        A ``draft`` is validated *here*, inline, so "start" is one honest step
        and never depends on a separate validate call having moved it somewhere
        first. ``scheduled`` campaigns are re-validated too, because the world
        changes between scheduling and launch (a list emptied, a sender
        disabled). A campaign that was paused while scheduled has never sent, so
        it starts fresh; one paused mid-send resumes where it left off.

        This creates CampaignContact records; actual sending is done by the workers.
        """
        from app.services import circuit_breaker

        campaign = await self._get(campaign_id)
        status = campaign.status
        fresh = status in ("draft", "scheduled") or (
            status == "paused" and campaign.paused_from == "scheduled"
        )
        if not fresh and status != "paused":
            raise CampaignStateError(f"Cannot start campaign in {status} status")
        if circuit_breaker.is_breaker_pause(campaign):
            # /start must not be a back door around the breaker's acknowledgement.
            if not acknowledge_breaker:
                raise BreakerTripped(campaign)
            circuit_breaker.acknowledge(campaign)

        if fresh:
            check = await self._collect(campaign)
            if check.errors:
                raise ValueError("; ".join(check.errors))
            await self._freeze_sequence(campaign, check)
            await self._populate_campaign_contacts(campaign)

        self.transition(campaign, "running")
        campaign.paused_from = None
        campaign.paused_at = None
        campaign.paused_reason = None
        # started_at records the first start; a resume must not rewrite it.
        if campaign.started_at is None or fresh:
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

            contact = (
                await self.db.execute(select(Contact).where(Contact.id == member.contact_id))
            ).scalar_one_or_none()
            if not contact:
                continue

            # The eligibility gate has to match the channel. Screening an EMAIL
            # campaign with the SMS rules dropped every email-only contact —
            # exactly the contacts an email campaign exists to reach — because
            # they have no phone number to classify.
            if (campaign.channel or "sms") == "email":
                from app.services import email_service

                if await email_service.contact_email_problem(self.db, contact):
                    continue
            else:
                from app.services.list_hygiene import contact_is_blocked_from_send

                if contact_is_blocked_from_send(contact):
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

    async def pause_campaign(
        self, campaign_id: int, *, reason: Optional[str] = None
    ) -> Campaign:
        """Pause a running **or scheduled** campaign.

        A scheduled campaign used to have no way to be held: pause refused it and
        delete refused it, so the only exits were "send" or "stop for good".
        ``paused_from`` remembers which it was, because resuming the two means
        different things.
        """
        campaign = await self._get(campaign_id)
        if campaign.status not in ("running", "scheduled"):
            raise CampaignStateError(
                f"Only running or scheduled campaigns can be paused (this one is {campaign.status})"
            )
        origin = campaign.status
        self.transition(campaign, "paused")
        campaign.paused_from = "scheduled" if origin == "scheduled" else None
        campaign.paused_at = datetime.now(timezone.utc)
        campaign.paused_reason = (reason or "Paused manually")[:500]
        await self.db.flush()
        return campaign

    async def resume_campaign(
        self, campaign_id: int, *, acknowledge_breaker: bool = False
    ) -> Campaign:
        """Resume a paused campaign.

        Paused mid-send: back to ``running``. Paused while scheduled: back to
        ``scheduled`` -- never straight to sending, because it has no contact rows
        yet and resuming a *hold* must not turn into a launch. If its launch time
        passed while it was paused the time is dropped (the launcher would fire it
        on the next tick) and the operator must choose to start it.
        """
        campaign = await self._get(campaign_id)
        if campaign.status != "paused":
            raise CampaignStateError(
                f"Only paused campaigns can be resumed (this one is {campaign.status})"
            )
        if campaign.paused_from != "scheduled":
            return await self.start_campaign(campaign_id, acknowledge_breaker=acknowledge_breaker)

        self.transition(campaign, "scheduled")
        campaign.paused_from = None
        campaign.paused_at = None
        campaign.paused_reason = None
        when = campaign.scheduled_start_at
        if when is not None:
            aware = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
            if aware <= datetime.now(timezone.utc):
                campaign.scheduled_start_at = None
        await self.db.flush()
        return campaign

    async def stop_campaign(self, campaign_id: int) -> Campaign:
        """Stop a campaign permanently."""
        campaign = await self._get(campaign_id)
        if campaign.status not in ("running", "paused", "scheduled", "failed"):
            raise CampaignStateError(f"Cannot stop campaign in {campaign.status} status")

        self.transition(campaign, "stopped")
        campaign.completed_at = datetime.now(timezone.utc)
        campaign.paused_from = None

        # Mail already created but not yet sent must not go out after a stop.
        from app.models.conversation import Message

        await self.db.execute(
            update(Message)
            .where(
                Message.campaign_id == campaign_id,
                Message.direction == "outgoing",
                Message.status.in_(["queued", "retrying"]),
            )
            .values(status="cancelled", last_error="Campaign stopped")
        )

        # Cancel all pending contacts
        await self.db.execute(
            update(CampaignContact)
            .where(
                CampaignContact.campaign_id == campaign_id,
                CampaignContact.status.in_(["pending", "queued"]),
            )
            .values(status="cancelled", next_action_at=None)
        )
        await self.db.execute(
            update(FollowUp)
            .where(
                FollowUp.campaign_id == campaign_id,
                FollowUp.status == "pending",
            )
            .values(
                status="cancelled",
                last_error="Campaign stopped",
                updated_at=datetime.now(timezone.utc),
            )
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

    async def delete_campaign(self, campaign_id: int) -> None:
        """Delete a campaign that has not sent anything.

        Allowed for drafts, scheduled campaigns, campaigns paused while scheduled,
        and failed campaigns -- but only while ``messages_sent`` is zero. Once a
        campaign has touched real recipients its history is the record of who was
        contacted, so it is stopped (kept) rather than deleted.
        """
        campaign = await self._get(campaign_id)
        deletable = campaign.status in self.DELETABLE_STATUSES or (
            campaign.status == "paused" and campaign.paused_from == "scheduled"
        )
        if not deletable:
            raise CampaignStateError(
                f"A {campaign.status} campaign cannot be deleted. Stop it instead; "
                "only draft, scheduled and failed campaigns can be deleted."
            )
        if (campaign.messages_sent or 0) > 0:
            raise CampaignStateError(
                f"This campaign has already sent {campaign.messages_sent} message(s), so it "
                "cannot be deleted. Stop it to keep the history."
            )
        await self.db.delete(campaign)
        await self.db.flush()

    async def delete_draft(self, campaign_id: int) -> None:
        """Back-compat name for :meth:`delete_campaign`."""
        await self.delete_campaign(campaign_id)
