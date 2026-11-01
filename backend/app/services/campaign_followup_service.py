"""Campaign follow-up engine.

For each active rule on a running campaign, find the contacts that:

  * received the previous message in the chain (step 1 follows the campaign's
    own message; step N follows step N-1),
  * have waited at least ``delay_minutes`` since that message,
  * have not already been processed for this rule,
  * and do not trip any stop condition.

Everything about the decision is recorded in ``CampaignFollowUpLog`` — both
sends and stops — so the UI can explain exactly why a contact did or did not
get a reminder, and so a rule can never fire twice for the same person.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory
from app.models.campaign import Campaign, CampaignContact
from app.models.campaign_followup import CampaignFollowUp, CampaignFollowUpLog
from app.models.contact import Contact
from app.models.conversation import Message
from app.models.suppression import SuppressionEntry

logger = logging.getLogger(__name__)

#: Statuses that mean "this contact received the campaign's first message".
SENT_STATUSES = ("sent", "delivered", "replied", "queued")


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; comparisons need them aware."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _local_hour(now: datetime) -> int:
    try:
        return now.astimezone(ZoneInfo(settings.DEFAULT_TIMEZONE)).hour
    except Exception:  # pragma: no cover - bad tz config
        return now.hour


def within_send_window(rule: CampaignFollowUp, now: datetime | None = None) -> bool:
    """Is it inside this rule's allowed sending hours?

    An unset window means "any time". A window that wraps midnight
    (22 -> 6) is supported.
    """
    if rule.send_start_hour is None or rule.send_end_hour is None:
        return True
    hour = _local_hour(now or datetime.now(timezone.utc))
    start, end = rule.send_start_hour, rule.send_end_hour
    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


async def _last_outgoing(db: AsyncSession, campaign_id: int, contact_id: int) -> Message | None:
    return (
        await db.execute(
            select(Message)
            .where(
                Message.campaign_id == campaign_id,
                Message.contact_id == contact_id,
                Message.direction == "outgoing",
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
    ).scalars().first()


async def _has_replied_since(db: AsyncSession, contact_id: int, since: datetime | None) -> bool:
    query = select(Message.id).where(
        Message.contact_id == contact_id, Message.direction == "incoming"
    )
    if since is not None:
        query = query.where(Message.created_at > since)
    return (await db.execute(query.limit(1))).scalars().first() is not None


async def evaluate_contact(
    db: AsyncSession,
    rule: CampaignFollowUp,
    campaign: Campaign,
    cc: CampaignContact,
    now: datetime,
) -> tuple[str, str]:
    """Decide what to do with one contact for one rule.

    Returns ``(decision, reason)`` where decision is ``send``, ``stop`` or
    ``wait``. ``wait`` means "not yet" and leaves no log row, so the contact is
    reconsidered on the next sweep.
    """
    contact = (
        await db.execute(select(Contact).where(Contact.id == cc.contact_id))
    ).scalar_one_or_none()
    if contact is None:
        return "stop", "Contact no longer exists"

    # --- Compliance stops, always enforced ---
    if contact.is_opted_out:
        return "stop", "Contact opted out"
    suppressed = (
        await db.execute(
            select(SuppressionEntry.id).where(
                SuppressionEntry.phone_number == contact.phone_number
            )
        )
    ).scalars().first()
    if suppressed:
        return "stop", "Number is on the suppression list"

    # --- The previous message in the chain ---
    previous = await _last_outgoing(db, campaign.id, cc.contact_id)
    if previous is None:
        return "wait", "No campaign message sent yet"
    if previous.status in ("queued", "sending"):
        return "wait", "Previous message has not left yet"
    if previous.status in ("failed", "cancelled"):
        return "stop", "Previous message failed to send"

    previous_at = _aware(previous.sent_at or previous.created_at)

    # --- Operator stop conditions ---
    if rule.stop_on_reply and await _has_replied_since(db, cc.contact_id, previous_at):
        return "stop", "Contact replied"

    stop_statuses = rule.stop_statuses()
    if stop_statuses and (contact.lead_status or "").lower() in stop_statuses:
        return "stop", f"Lead status is '{contact.lead_status}'"

    # --- Timing ---
    due_at = (previous_at or now) + timedelta(minutes=rule.delay_minutes)
    if now < due_at:
        return "wait", f"Due at {due_at.isoformat()}"
    if not within_send_window(rule, now):
        return "wait", "Outside the allowed sending hours"

    return "send", "Due"


class _SenderPaused(Exception):
    """The pinned sender is unavailable; the follow-up is paused, not failed."""


async def _log_followup(
    db: AsyncSession,
    rule: CampaignFollowUp,
    contact_id: int,
    *,
    status: str,
    reason: str | None = None,
    message_id: int | None = None,
    body_preview: str | None = None,
) -> None:
    """Append-or-update the one log row for (rule, contact).

    A paused row is updated in place when the same contact pauses again (or
    finally sends), so the unique (followup_id, contact_id) guard holds and the
    UI always shows the latest reason.
    """
    existing = (
        await db.execute(
            select(CampaignFollowUpLog).where(
                CampaignFollowUpLog.followup_id == rule.id,
                CampaignFollowUpLog.contact_id == contact_id,
            )
        )
    ).scalars().first()
    if existing is not None:
        existing.status = status
        existing.reason = reason
        if message_id is not None:
            existing.message_id = message_id
        if body_preview is not None:
            existing.body_preview = body_preview
        return
    db.add(
        CampaignFollowUpLog(
            followup_id=rule.id,
            campaign_id=rule.campaign_id,
            contact_id=contact_id,
            status=status,
            reason=reason,
            message_id=message_id,
            body_preview=body_preview,
        )
    )


async def _resolve_body(db: AsyncSession, rule: CampaignFollowUp) -> str | None:
    if rule.message_text and rule.message_text.strip():
        return rule.message_text
    if rule.template_id:
        from app.models.template import Template

        template = (
            await db.execute(select(Template).where(Template.id == rule.template_id))
        ).scalar_one_or_none()
        if template and (template.body or "").strip():
            return template.body
    return None


async def process_rule(db: AsyncSession, rule: CampaignFollowUp, *, limit: int = 100) -> dict:
    """Run one follow-up rule across its campaign's contacts."""
    campaign = (
        await db.execute(select(Campaign).where(Campaign.id == rule.campaign_id))
    ).scalar_one_or_none()
    if not campaign:
        return {"sent": 0, "stopped": 0, "waiting": 0, "reason": "Campaign missing"}
    if campaign.status not in ("running", "completed"):
        # Paused / draft / stopped campaigns must not keep texting people.
        return {"sent": 0, "stopped": 0, "waiting": 0, "reason": f"Campaign is {campaign.status}"}

    body = await _resolve_body(db, rule)
    if not body:
        return {"sent": 0, "stopped": 0, "waiting": 0, "reason": "Follow-up has no message"}

    # Contacts already handled for this rule are excluded outright: the unique
    # (followup_id, contact_id) log row is what guarantees exactly-once.
    # A "paused" row (stored sender unavailable) is NOT final — the contact is
    # reconsidered on every sweep and resumes the moment the sender is fixed.
    handled = set(
        (
            await db.execute(
                select(CampaignFollowUpLog.contact_id).where(
                    CampaignFollowUpLog.followup_id == rule.id,
                    CampaignFollowUpLog.status != "paused",
                )
            )
        ).scalars().all()
    )

    # Step N only considers contacts that step N-1 actually sent to.
    if rule.step_order > 1:
        previous_rule = (
            await db.execute(
                select(CampaignFollowUp).where(
                    CampaignFollowUp.campaign_id == rule.campaign_id,
                    CampaignFollowUp.step_order == rule.step_order - 1,
                )
            )
        ).scalars().first()
        if previous_rule is None:
            return {"sent": 0, "stopped": 0, "waiting": 0, "reason": "Previous step missing"}
        eligible_ids = set(
            (
                await db.execute(
                    select(CampaignFollowUpLog.contact_id).where(
                        CampaignFollowUpLog.followup_id == previous_rule.id,
                        CampaignFollowUpLog.status == "sent",
                    )
                )
            ).scalars().all()
        )
    else:
        eligible_ids = None

    query = select(CampaignContact).where(
        CampaignContact.campaign_id == rule.campaign_id,
        CampaignContact.status.in_(SENT_STATUSES),
    )
    candidates = (await db.execute(query)).scalars().all()

    now = datetime.now(timezone.utc)
    sent = stopped = waiting = 0
    outbox: list[int] = []

    for cc in candidates:
        if cc.contact_id in handled:
            continue
        if eligible_ids is not None and cc.contact_id not in eligible_ids:
            continue
        if sent + stopped >= limit:
            break

        decision, reason = await evaluate_contact(db, rule, campaign, cc, now)

        if decision == "wait":
            waiting += 1
            continue

        if decision == "stop":
            await _log_followup(
                db, rule, cc.contact_id, status="stopped", reason=reason[:300]
            )
            stopped += 1
            continue

        # --- Send ---
        # The rule's channel (falling back to the campaign's) decides the
        # provider: email rules go through Brevo with the email consent checks,
        # SMS rules keep using the existing gateway path.
        from app.services import email_service
        from app.services.sms_service import SMSService

        channel = (getattr(rule, "channel", None) or getattr(campaign, "channel", "sms") or "sms")
        paused_reason: str | None = None
        try:
            if channel == "email":
                contact = (
                    await db.execute(select(Contact).where(Contact.id == cc.contact_id))
                ).scalar_one_or_none()
                if contact is None:
                    raise ValueError("Contact not found")
                problem = await email_service.contact_email_problem(db, contact, outreach=True)
                if problem:
                    raise ValueError(f"Contact cannot receive email ({problem})")

                # ---- Sender pinning + threading (sending policy) ----------
                # The follow-up leaves from the sender that delivered this
                # contact's FIRST campaign email — read from the assignment or
                # the first message row — never from the campaign's *current*
                # sender list. If that sender is unavailable the follow-up is
                # PAUSED with the reason, never silently re-sent from another
                # account.
                root_message = (
                    await db.execute(
                        select(Message)
                        .where(
                            Message.campaign_id == campaign.id,
                            Message.contact_id == cc.contact_id,
                            Message.direction == "outgoing",
                        )
                        .order_by(Message.id.asc())
                        .limit(1)
                    )
                ).scalars().first()
                pinned_sender_id = (
                    getattr(cc, "email_account_id", None)
                    or (root_message.email_account_id if root_message is not None else None)
                )
                account = await email_service.resolve_sender(db, pinned_sender_id)
                if account is None:
                    raise _SenderPaused(
                        "the sender that started this thread no longer exists — reconnect it to resume"
                    )
                usable, why = await email_service.sender_is_usable(account)
                if not usable:
                    raise _SenderPaused(
                        f"the sender that started this thread is paused ({why or 'unavailable'}) — fix it to resume"
                    )

                followup_subject = email_service.reply_subject(
                    (root_message.subject if root_message is not None else None)
                    or rule.subject
                    or campaign.subject
                    or "Follow-up"
                )
                message, send_result = await email_service.send_now(
                    db, contact,
                    subject=followup_subject,
                    text_body=body,
                    account=account,
                    campaign_id=campaign.id,
                    bulk=True,
                    thread_root_rfc_id=(
                        root_message.rfc_message_id if root_message is not None else None
                    ),
                )
                if message is None:
                    raise ValueError("Contact cannot receive email")
                if not send_result.get("success"):
                    raise ValueError(send_result.get("error") or "The email sender rejected the message")
            else:
                message = await SMSService(db).send_message(
                    cc.contact_id, body, campaign_id=campaign.id
                )
        except _SenderPaused as exc:
            logger.warning(
                "Follow-up rule %s contact %s paused: %s", rule.id, cc.contact_id, exc
            )
            message = None
            paused_reason = str(exc)[:300]
        except Exception as exc:  # a gateway blow-up must not abort the sweep
            logger.error("Follow-up rule %s contact %s error: %s", rule.id, cc.contact_id, exc)
            message = None
            reason = str(exc)[:300]

        if paused_reason:
            await _log_followup(
                db, rule, cc.contact_id, status="paused", reason=paused_reason,
            )
            continue

        if message is None:
            await _log_followup(
                db, rule, cc.contact_id, status="failed",
                reason=(reason or "Contact cannot receive messages")[:300],
            )
            stopped += 1
            continue

        await _log_followup(
            db, rule, cc.contact_id,
            status="sent" if message.status != "failed" else "failed",
            reason=None if message.status != "failed" else (message.last_error or "")[:300],
            message_id=message.id,
            body_preview=(message.body or "")[:300],
        )
        if message.status == "failed":
            stopped += 1
        else:
            sent += 1
            outbox.append(message.id)

    rule.sent_count = (rule.sent_count or 0) + sent
    rule.stopped_count = (rule.stopped_count or 0) + stopped
    await db.flush()

    return {"sent": sent, "stopped": stopped, "waiting": waiting, "reason": None}


async def process_due_campaign_followups(limit_per_rule: int = 100) -> dict:
    """Sweep every active follow-up rule on every running campaign."""
    totals = {"sent": 0, "stopped": 0, "waiting": 0, "rules": 0}
    async with async_session_factory() as db:
        rules = (
            await db.execute(
                select(CampaignFollowUp)
                .join(Campaign, Campaign.id == CampaignFollowUp.campaign_id)
                .where(
                    CampaignFollowUp.is_active.is_(True),
                    Campaign.status.in_(("running", "completed")),
                )
                .order_by(CampaignFollowUp.campaign_id, CampaignFollowUp.step_order)
            )
        ).scalars().all()

        for rule in rules:
            try:
                result = await process_rule(db, rule, limit=limit_per_rule)
            except Exception as exc:
                logger.error("Follow-up rule %s failed: %s", rule.id, exc)
                continue
            totals["rules"] += 1
            for key in ("sent", "stopped", "waiting"):
                totals[key] += result.get(key, 0)
        await db.commit()
    return totals
