"""
Celery tasks for campaign and sequence processing.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.celery_app import celery_app
from app.tasks.queue import QueueUnavailable, enqueue, try_enqueue
from app.database import async_session_factory
from app.models.campaign import Campaign, CampaignContact
from app.models.sequence import SequenceVersion
from app.models.contact import Contact
from app.models.template import Template
from app.models.followup import FollowUp
from app.models.conversation import Message

logger = logging.getLogger(__name__)


@celery_app.task
def process_running_campaigns():
    """Process all running campaigns (called by beat)."""
    async def _process():
        async with async_session_factory() as db:
            result = await db.execute(
                select(Campaign).where(Campaign.status == "running").limit(10)
            )
            campaigns = result.scalars().all()
            from app.tasks.queue import try_enqueue
            for campaign in campaigns:
                # Best-effort: a broker blip on one campaign must not abort
                # the whole beat cycle.
                try_enqueue(process_campaign, campaign.id)

    loop = asyncio.get_event_loop()
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(_process())


async def launch_due_campaigns_async() -> list[int]:
    """Start every campaign whose scheduled launch time has passed.

    Shared by the Celery beat task and the API's inline poller, so scheduling
    still works on deployments where only one of the two is running. Safe to
    call concurrently -- see the atomic claim below. Returns the launched ids.
    """
    from app.services.campaign_service import CampaignService

    now = datetime.now(timezone.utc)
    async with async_session_factory() as db:
        result = await db.execute(
            select(Campaign)
            .where(
                Campaign.status == "scheduled",
                Campaign.scheduled_start_at.is_not(None),
                Campaign.scheduled_start_at <= now,
            )
            .order_by(Campaign.scheduled_start_at.asc())
            .limit(10)
        )
        due = result.scalars().all()
        if not due:
            return []

        service = CampaignService(db)
        started = []
        for campaign in due:
            # Claim the campaign atomically before touching it. Beat and the
            # API's inline poller can both run this, and two launchers that
            # each read status="scheduled" would both start the same
            # campaign. The conditional UPDATE lets exactly one win: the
            # loser matches 0 rows and skips.
            claim = await db.execute(
                update(Campaign)
                .where(Campaign.id == campaign.id, Campaign.status == "scheduled")
                .values(scheduled_start_at=None)
            )
            if claim.rowcount != 1:
                logger.info("SCHEDULE: campaign %s already claimed; skipping", campaign.id)
                continue
            try:
                # Same path as a manual start: populates CampaignContact
                # rows from the list and flips the status to "running".
                await service.start_campaign(campaign.id)
                started.append(campaign.id)
            except ValueError as e:
                # Invalid campaign (no contacts, no message). Do not retry
                # every 2 minutes forever -- drop it back to draft so the
                # user sees it did not go out and why.
                logger.error("SCHEDULE: campaign %s not startable: %s", campaign.id, e)
                campaign.status = "draft"
            except Exception as e:
                logger.error("SCHEDULE: campaign %s failed to launch: %s", campaign.id, e)

        # Commit BEFORE enqueuing so the worker sees "running" and the
        # contact rows (same ordering bug as the manual start path).
        await db.commit()

        for campaign_id in started:
            if try_enqueue(process_campaign, campaign_id):
                logger.info("SCHEDULE: launched campaign %s", campaign_id)
            else:
                # Still "running"; the next process_running_campaigns
                # beat cycle will pick it up.
                logger.warning(
                    "SCHEDULE: campaign %s started but broker unavailable", campaign_id
                )

        return started


@celery_app.task
def launch_due_campaigns():
    """Beat entrypoint for scheduled campaign launches.

    Without this, setting a launch time only records an intention: the
    campaign would sit in "scheduled" forever waiting for a manual Start.
    """
    loop = asyncio.get_event_loop()
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(launch_due_campaigns_async())


@celery_app.task
def process_campaign(campaign_id: int):
    """Process the next batch of contacts for a campaign."""
    async def _process():
        async with async_session_factory() as db:
            result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
            campaign = result.scalar_one_or_none()
            if not campaign or campaign.status != "running":
                return

            # Get pending contacts (limit batch size)
            cc_result = await db.execute(
                select(CampaignContact).where(
                    CampaignContact.campaign_id == campaign_id,
                    CampaignContact.status == "pending",
                ).limit(50)
            )
            contacts = cc_result.scalars().all()

            if not contacts:
                # Check if all done. "sent" means handed to the gateway but not
                # yet acknowledged, so it stays in the in-flight set; the
                # delivery/failure webhook moves the row to a terminal state.
                remaining = await db.execute(
                    select(CampaignContact).where(
                        CampaignContact.campaign_id == campaign_id,
                        CampaignContact.status.in_(["pending", "queued", "sent"]),
                    ).limit(1)
                )
                if remaining.scalars().first() is None:
                    campaign.status = "completed"
                    campaign.completed_at = datetime.now(timezone.utc)
                    await db.commit()
                return

            # Messages are enqueued only AFTER the transaction commits. Handing
            # a message id to the broker while the row is still uncommitted is
            # a race the worker loses: it looks the id up, sees nothing, and
            # drops the send. That silently skipped most of every batch --
            # only the last contact (whose enqueue happened to land after the
            # commit) was ever texted.
            outbox: list[int] = []
            for cc in contacts:
                await _process_campaign_contact(db, campaign, cc, outbox)

            await db.commit()

        # Separate session: the rows above are now durable and visible to the
        # worker, so it is safe to publish.
        await _flush_outbox(outbox)

    loop = asyncio.get_event_loop()
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(_process())


async def _flush_outbox(message_ids):
    """Publish committed messages to the broker.

    Anything we fail to publish is rolled back to a retryable state so the
    next beat picks it up, rather than leaving a message stuck in "queued"
    forever with the contact never contacted.
    """
    if not message_ids:
        return
    from app.tasks.sms_tasks import send_sms

    stranded = []
    for mid in message_ids:
        try:
            enqueue(send_sms, mid)
        except QueueUnavailable:
            stranded.append(mid)

    if not stranded:
        return

    logger.error(
        "Task queue unavailable; %d campaign message(s) not published.",
        len(stranded),
    )
    # Put the contacts back to pending and drop the phantom message rows so
    # the campaign retries them instead of reporting a send that never left.
    async with async_session_factory() as db:
        msgs = (
            await db.execute(select(Message).where(Message.id.in_(stranded)))
        ).scalars().all()
        for m in msgs:
            cc = (
                await db.execute(
                    select(CampaignContact).where(
                        CampaignContact.campaign_id == m.campaign_id,
                        CampaignContact.contact_id == m.contact_id,
                    )
                )
            ).scalar_one_or_none()
            await _flush_outbox_rollback(db, m, cc, commit=False)
        await db.commit()


async def _flush_outbox_rollback(db, message, cc, commit=True):
    """Undo a send that could not be published to the broker.

    Leaves no phantom "queued" message behind and returns the contact to
    "pending" so a later run retries it.
    """
    if cc is not None and cc.status == "queued":
        cc.status = "pending"
        if cc.sequence_step:
            cc.sequence_step -= 1
    await db.delete(message)
    if commit:
        await db.commit()


async def _process_campaign_contact(db: AsyncSession, campaign: Campaign, cc: CampaignContact, outbox=None):
    """Process a single contact in a campaign."""
    # Get contact
    contact_result = await db.execute(select(Contact).where(Contact.id == cc.contact_id))
    contact = contact_result.scalar_one_or_none()
    if not contact or contact.is_opted_out:
        cc.status = "opted_out"
        return

    # Get sequence snapshot
    if campaign.sequence_version_id:
        version_result = await db.execute(
            select(SequenceVersion).where(SequenceVersion.id == campaign.sequence_version_id)
        )
        version = version_result.scalar_one_or_none()
        if version:
            steps = json.loads(version.snapshot)
            await _execute_sequence_step(db, campaign, cc, contact, steps, cc.sequence_step, outbox)
        else:
            # No sequence, just send the template message
            await _send_template_message(db, campaign, cc, contact, outbox=outbox)
    else:
        await _send_template_message(db, campaign, cc, contact, outbox=outbox)


async def _execute_sequence_step(db, campaign, cc, contact, steps, current_step_idx, outbox=None):
    """Execute a sequence step."""
    if current_step_idx >= len(steps):
        cc.status = "completed"
        return

    step = steps[current_step_idx]

    if step["step_type"] == "send_sms":
        await _send_template_message(db, campaign, cc, contact, step.get("template_id"), outbox=outbox)

    elif step["step_type"] == "wait":
        wait_hours = step.get("wait_duration_hours", 24)
        cc.next_action_at = datetime.now(timezone.utc) + timedelta(hours=wait_hours)
        # Schedule follow-up
        followup = FollowUp(
            contact_id=cc.contact_id,
            campaign_id=campaign.id,
            campaign_contact_id=cc.id,
            sequence_id=campaign.sequence_id,
            sequence_step_order=current_step_idx + 1,
            scheduled_at=cc.next_action_at,
            status="pending",
        )
        db.add(followup)
        cc.status = "queued"

    elif step["step_type"] == "condition":
        condition_met = await _check_condition(db, cc, step)
        next_step = step.get("true_branch_step_order" if condition_met else "false_branch_step_order")
        if next_step is not None:
            cc.sequence_step = next_step
            await _execute_sequence_step(db, campaign, cc, contact, steps, next_step, outbox)
        else:
            cc.status = "completed"

    elif step["step_type"] == "stop":
        cc.status = "completed"


async def _send_template_message(db, campaign, cc, contact, template_id=None, outbox=None):
    """Send a template message to a contact."""
    # Resolve through the shared resolver so an inline campaign message, a
    # selected template and a sequence step all follow one precedence rule.
    from app.services.campaign_service import CampaignService

    body = await CampaignService(db).resolve_body(campaign, template_id)
    if not body or not body.strip():
        # There used to be a hardcoded `body = "Hello"` fallback here, so a
        # campaign with no template texted the literal word "Hello" to every
        # contact. Failing the send is the only safe option: these go to real
        # phone numbers and cannot be recalled.
        # CampaignContact has no error_message column; status + log is the
        # extent of what we can record here.
        cc.status = "failed"
        logger.error(
            f"Campaign {campaign.id} has no message body; skipping contact {contact.id}"
        )
        return

    # Personalize via the shared renderer so campaign sends, direct sends and
    # the preview endpoint all produce identical text.
    from app.utils.templating import render_template
    body = render_template(body, contact)

    # Create message
    from app.utils.phone import count_sms_segments
    import uuid
    char_count, segment_count = count_sms_segments(body)
    idempotency_key = f"campaign-{campaign.id}-{cc.contact_id}-{uuid.uuid4().hex[:8]}"

    # Find or create conversation
    from app.models.conversation import Conversation
    conv_result = await db.execute(
        select(Conversation)
        .where(Conversation.contact_id == contact.id)
        .order_by(Conversation.id)
        .limit(1)
    )
    conversation = conv_result.scalars().first()
    if not conversation:
        conversation = Conversation(
            contact_id=contact.id,
            campaign_id=campaign.id,
            status="active",
        )
        db.add(conversation)
        await db.flush()

    message = Message(
        conversation_id=conversation.id,
        contact_id=contact.id,
        campaign_id=campaign.id,
        direction="outgoing",
        body=body,
        segment_count=segment_count,
        char_count=char_count,
        status="queued",
        provider="smsgate",
        idempotency_key=idempotency_key,
    )
    db.add(message)
    await db.flush()

    # Keep the conversation summary in step with the message we just created.
    # Every other send path does this; campaign sends did not, so a contact
    # messaged only by a campaign showed up in the inbox as an empty thread
    # with no preview and no timestamp to sort by.
    conversation.message_count = (conversation.message_count or 0) + 1
    conversation.last_message_preview = body[:100]
    conversation.last_message_at = datetime.now(timezone.utc)

    # Update campaign contact.
    #
    # NOTE: the "sent" counters are deliberately NOT incremented here. Being
    # handed to the broker is not the same as being accepted by the gateway --
    # when the gateway rejected every message (auth failure, device offline)
    # the campaign still reported a full send and the failures were invisible.
    # app.tasks.sms_tasks records the real outcome once the gateway answers.
    cc.status = "queued"
    cc.last_message_at = datetime.now(timezone.utc)
    cc.sequence_step += 1

    # Update contact
    contact.last_contacted_at = datetime.now(timezone.utc)

    # Hand the send off to the caller's outbox rather than publishing here.
    # The broker must only learn about this message once the surrounding
    # transaction has committed, otherwise the worker can dequeue the id
    # before the row is visible and silently drop the send. The caller
    # publishes (and cleans up after a broker failure) after it commits.
    if outbox is not None:
        outbox.append(message.id)
        return

    # Standalone caller with no outbox: commit the full unit of work first --
    # message, contact row and counters -- so the worker cannot outrun it.
    from app.tasks.sms_tasks import send_sms

    await db.commit()
    try:
        enqueue(send_sms, message.id)
    except QueueUnavailable:
        await _flush_outbox_rollback(db, message, cc)
        logger.error(
            "Broker unavailable while sending campaign %s to contact %s; "
            "contact left pending for retry.",
            campaign.id,
            cc.contact_id,
        )
        raise


async def _check_condition(db, cc, step):
    """Check a sequence condition."""
    condition_type = step.get("condition_type", "")
    condition_value = step.get("condition_value", "")

    if condition_type == "contact_replied":
        # Check for replies
        from app.models.conversation import Message
        msg_result = await db.execute(
            select(Message).where(
                Message.contact_id == cc.contact_id,
                Message.direction == "incoming",
                Message.created_at > cc.last_message_at if cc.last_message_at else datetime.min.replace(tzinfo=timezone.utc),
            )
        )
        return msg_result.scalar_one_or_none() is not None

    elif condition_type == "contact_did_not_reply":
        msg_result = await db.execute(
            select(Message).where(
                Message.contact_id == cc.contact_id,
                Message.direction == "incoming",
                Message.created_at > cc.last_message_at if cc.last_message_at else datetime.min.replace(tzinfo=timezone.utc),
            )
        )
        return msg_result.scalar_one_or_none() is None

    elif condition_type == "message_delivered":
        return cc.status == "delivered"

    elif condition_type == "message_failed":
        return cc.status == "failed"

    elif condition_type == "contact_opted_out":
        contact_result = await db.execute(select(Contact).where(Contact.id == cc.contact_id))
        contact = contact_result.scalar_one_or_none()
        return contact.is_opted_out if contact else False

    return False


async def process_followup_async(followup_id: int) -> bool:
    """Claim and execute one follow-up exactly once.

    Manual follow-ups send their saved message directly through SMSService.
    Sequence follow-ups advance the campaign sequence as before. The atomic
    pending -> sending claim prevents the inline poller and Celery beat from
    sending the same due follow-up at the same time.
    """
    async with async_session_factory() as claim_db:
        claim = await claim_db.execute(
            update(FollowUp)
            .where(FollowUp.id == followup_id, FollowUp.status == "pending")
            .values(
                status="sending",
                attempt_count=FollowUp.attempt_count + 1,
            )
        )
        await claim_db.commit()
        if claim.rowcount != 1:
            return False

    outbox: list[int] = []
    async with async_session_factory() as db:
        followup = (
            await db.execute(select(FollowUp).where(FollowUp.id == followup_id))
        ).scalar_one_or_none()
        if not followup or followup.status != "sending":
            return False

        try:
            contact = (
                await db.execute(select(Contact).where(Contact.id == followup.contact_id))
            ).scalar_one_or_none()
            if not contact:
                raise ValueError("Contact not found")
            if contact.is_opted_out:
                raise ValueError("Contact has opted out")

            if followup.campaign_contact_id:
                cc = (
                    await db.execute(
                        select(CampaignContact).where(
                            CampaignContact.id == followup.campaign_contact_id
                        )
                    )
                ).scalar_one_or_none()
                campaign = (
                    await db.execute(
                        select(Campaign).where(Campaign.id == followup.campaign_id)
                    )
                ).scalar_one_or_none()
                if not cc or not campaign:
                    raise ValueError("Campaign follow-up data is missing")
                if not campaign.sequence_version_id:
                    raise ValueError("Campaign sequence version is missing")

                version = (
                    await db.execute(
                        select(SequenceVersion).where(
                            SequenceVersion.id == campaign.sequence_version_id
                        )
                    )
                ).scalar_one_or_none()
                if not version:
                    raise ValueError("Campaign sequence version not found")
                if followup.sequence_step_order is None:
                    raise ValueError("Campaign follow-up step is missing")

                steps = json.loads(version.snapshot)
                cc.sequence_step = followup.sequence_step_order
                await _execute_sequence_step(
                    db,
                    campaign,
                    cc,
                    contact,
                    steps,
                    followup.sequence_step_order,
                    outbox,
                )
                followup.status = "sent"
            else:
                if not followup.message_text or not followup.message_text.strip():
                    raise ValueError("Follow-up message is empty")
                from app.services.sms_service import SMSService

                message = await SMSService(db).send_message(
                    followup.contact_id, followup.message_text
                )
                if message is None:
                    raise ValueError("Contact cannot receive this message")
                followup.message_id = message.id
                if message.status == "failed":
                    raise ValueError(message.last_error or "SMS gateway rejected the message")
                followup.status = "sent"

            followup.last_error = None
            followup.executed_at = datetime.now(timezone.utc)
            await db.commit()
        except Exception as exc:
            followup.last_error = str(exc)[:2000]
            followup.executed_at = datetime.now(timezone.utc)
            # A gateway failure is visible immediately instead of retrying the
            # same real phone number every 30 seconds without operator input.
            followup.status = "failed"
            await db.commit()
            logger.error("Follow-up %s failed: %s", followup_id, exc)
            return False

    # Publish sequence-created messages only after their rows are committed.
    await _flush_outbox(outbox)
    return True


async def process_due_followups_async(limit: int = 50) -> list[int]:
    """Execute pending follow-ups whose scheduled time has arrived."""
    now = datetime.now(timezone.utc)
    async with async_session_factory() as db:
        due_ids = (
            await db.execute(
                select(FollowUp.id)
                .where(
                    FollowUp.status == "pending",
                    FollowUp.scheduled_at <= now,
                )
                .order_by(FollowUp.scheduled_at.asc())
                .limit(limit)
            )
        ).scalars().all()

    processed: list[int] = []
    for followup_id in due_ids:
        if await process_followup_async(followup_id):
            processed.append(followup_id)
    return processed


@celery_app.task
def process_followup(followup_id: int):
    """Celery entrypoint for sending one follow-up now."""
    loop = asyncio.get_event_loop()
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(process_followup_async(followup_id))


@celery_app.task
def process_due_followups():
    """Celery beat entrypoint for the due follow-up sweep."""
    loop = asyncio.get_event_loop()
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(process_due_followups_async())


@celery_app.task
def schedule_followup(followup_id: int):
    """Queue a follow-up for immediate execution.

    Future follow-ups are picked up by ``process_due_followups`` at their real
    scheduled time; this helper remains for callers that explicitly want now.
    """
    try:
        enqueue(process_followup, followup_id)
    except QueueUnavailable as exc:
        logger.error("Could not schedule follow-up %s: %s", followup_id, exc)
        raise
