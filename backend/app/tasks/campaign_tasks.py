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


@celery_app.task
def process_followup(followup_id: int):
    """Process a scheduled follow-up."""
    async def _process():
        outbox: list[int] = []
        async with async_session_factory() as db:
            result = await db.execute(select(FollowUp).where(FollowUp.id == followup_id))
            followup = result.scalar_one_or_none()
            if not followup or followup.status != "pending":
                return

            followup.status = "sending"
            followup.attempt_count += 1

            # Get campaign contact if applicable
            if followup.campaign_contact_id:
                cc_result = await db.execute(
                    select(CampaignContact).where(CampaignContact.id == followup.campaign_contact_id)
                )
                cc = cc_result.scalar_one_or_none()
                if cc:
                    contact_result = await db.execute(select(Contact).where(Contact.id == followup.contact_id))
                    contact = contact_result.scalar_one_or_none()
                    campaign_result = await db.execute(select(Campaign).where(Campaign.id == followup.campaign_id))
                    campaign = campaign_result.scalar_one_or_none()

                    if contact and campaign and not contact.is_opted_out:
                        # Get sequence version
                        if campaign.sequence_version_id:
                            ver_result = await db.execute(
                                select(SequenceVersion).where(SequenceVersion.id == campaign.sequence_version_id)
                            )
                            version = ver_result.scalar_one_or_none()
                            if version:
                                steps = json.loads(version.snapshot)
                                cc.sequence_step = followup.sequence_step_order
                                await _execute_sequence_step(
                                    db, campaign, cc, contact, steps,
                                    followup.sequence_step_order, outbox,
                                )

            followup.status = "sent"
            followup.executed_at = datetime.now(timezone.utc)
            await db.commit()

        # Publish only after the commit above, so the worker cannot dequeue a
        # message id before its row exists.
        await _flush_outbox(outbox)

    loop = asyncio.get_event_loop()
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(_process())


@celery_app.task
def schedule_followup(followup_id: int):
    """Schedule a follow-up for later execution."""
    # This is a placeholder - in production, you'd use Celery's ETA/countdown
    try:
        process_followup.apply_async(
            args=[followup_id],
            countdown=60,  # Default 1 minute; in production, use the actual scheduled time
        )
    except Exception as exc:
        # Surface a clear reason instead of a raw kombu error; the follow-up is
        # still in the DB and will be retried by the periodic sweep.
        logger.error("Could not schedule follow-up %s: %s", followup_id, exc)
        raise QueueUnavailable(
            "Background task queue is unavailable. Check that the Redis broker "
            "(REDIS_URL) is reachable, then try again."
        ) from exc
