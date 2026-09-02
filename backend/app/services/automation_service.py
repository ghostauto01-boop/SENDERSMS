"""GoHighLevel-style reply automation engine.

An Automation watches an inbound reply and, when its conditions match, runs an
ordered list of actions. This is the feature that lets an operator automate:

  * "Is this {business_name}?"  answered "no / wrong number"  ->
        opt the contact out (so the 2nd message never goes) and stop the
        campaign sequence for them.
  * answered "yes" ->
        send the follow-up / 2nd message.

Conditions are evaluated against the keyless AI classification of the reply
(``ai_classifier``) plus the contact's tags, so "if someone from the
*restaurants* tag replies" works out of the box.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation import Automation
from app.models.contact import Contact, ContactTag, Tag
from app.models.followup import FollowUp
from app.models.suppression import SuppressionEntry
from app.utils.templating import render_template

logger = logging.getLogger(__name__)

# Allowed action types.
SEND_SMS = "send_sms"
STOP_SEQUENCE = "stop_sequence"
OPT_OUT = "opt_out"
ADD_TAG = "add_tag"
REMOVE_TAG = "remove_tag"
SET_STATUS = "set_status"
DELETE_CONTACT = "delete_contact"


def load_json(raw: Optional[str]) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except (ValueError, TypeError):
        return []


class AutomationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _contact_tags(self, contact: Contact) -> set[str]:
        rows = await self.db.execute(
            select(Tag.name)
            .join(ContactTag, ContactTag.tag_id == Tag.id)
            .where(ContactTag.contact_id == contact.id)
        )
        return {name.lower() for name in rows.scalars().all()}

    def _condition_met(self, cond: dict, ctx: dict) -> bool:
        """Evaluate one condition against the classification context."""
        field = (cond.get("field") or "").strip()
        op = (cond.get("op") or "is").strip()
        value = str(cond.get("value") or "").strip().lower()

        if field == "any":
            return True

        if field == "sentiment":
            actual = str(ctx.get("sentiment") or "").lower()
        elif field == "intent":
            actual = str(ctx.get("intent") or "").lower()
        elif field == "label":
            labels = [str(l).lower() for l in ctx.get("labels") or []]
            if op in ("is_not", "not"):
                return value not in labels
            return value in labels
        elif field == "has_tag":
            tags = ctx.get("tags") or set()
            if op in ("is_not", "not"):
                return value not in tags
            return value in tags
        elif field == "keyword":
            text = str(ctx.get("text") or "").lower()
            return value in text
        else:
            return False

        if op in ("is_not", "not"):
            return actual != value
        if op in ("contains", "in"):
            return value in actual
        return actual == value

    def _conditions_met(self, automation: Automation, ctx: dict) -> bool:
        conditions = load_json(automation.conditions_json)
        if not conditions:
            return False
        if automation.match_all:
            return all(self._condition_met(c, ctx) for c in conditions)
        return any(self._condition_met(c, ctx) for c in conditions)

    async def run_for_reply(self, contact: Contact, text: str, classification: dict) -> int:
        """Evaluate enabled automations against an inbound reply.

        Returns how many automations fired. Best-effort: a failure in one
        action never blocks the others or the inbound message itself.
        """
        result = await self.db.execute(
            select(Automation)
            .where(Automation.is_enabled == True, Automation.trigger_type == "inbound_reply")  # noqa: E712
            .order_by(Automation.priority.asc(), Automation.id.asc())
        )
        automations = result.scalars().all()
        if not automations:
            return 0

        ctx = {
            "text": text,
            "sentiment": classification.get("sentiment"),
            "intent": classification.get("intent"),
            "labels": classification.get("labels") or [],
            "tags": await self._contact_tags(contact),
        }

        fired = 0
        from sqlalchemy import inspect
        for automation in automations:
            # A previous action may have deleted the contact; stop then.
            if inspect(contact).deleted:
                break
            if not self._conditions_met(automation, ctx):
                continue
            try:
                await self._execute(automation, contact, text)
                automation.times_triggered = (automation.times_triggered or 0) + 1
                automation.last_triggered_at = datetime.now(timezone.utc)
                fired += 1
                logger.info(
                    "AUTOMATION: '%s' fired for contact %s", automation.name, contact.id
                )
            except Exception as exc:
                logger.error(
                    "AUTOMATION: '%s' failed for contact %s: %s",
                    automation.name, contact.id, exc,
                )
        await self.db.flush()
        return fired

    async def _execute(self, automation: Automation, contact: Contact, text: str) -> None:
        for action in load_json(automation.actions_json):
            await self._run_action(action, contact)

    async def _run_action(self, action: dict, contact: Contact) -> None:
        action_type = (action.get("type") or "").strip()

        if action_type == SEND_SMS:
            body = (action.get("body") or "").strip()
            delay_minutes = int(action.get("delay_minutes") or 0)
            if not body:
                return
            rendered = render_template(body, contact)
            if not rendered.strip():
                return
            if delay_minutes and delay_minutes > 0:
                self.db.add(FollowUp(
                    contact_id=contact.id,
                    status="pending",
                    scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=delay_minutes),
                    message_text=rendered,
                    notify_on_due=False,
                ))
            else:
                from app.services.sms_service import SMSService
                await SMSService(self.db).send_message(contact.id, rendered)
            return

        if action_type == STOP_SEQUENCE:
            from app.services.sms_service import SMSService
            await SMSService(self.db)._stop_seq(contact.id)
            from app.models.conversation import Conversation
            conv = (
                await self.db.execute(
                    select(Conversation).where(Conversation.contact_id == contact.id)
                )
            ).scalars().first()
            if conv:
                conv.sequence_paused = True
            return

        if action_type == OPT_OUT:
            contact.is_opted_out = True
            contact.opted_out_at = datetime.now(timezone.utc)
            contact.opt_out_reason = "automation"
            contact.consent_status = "opted_out"
            contact.has_consented = False
            self.db.add(SuppressionEntry(
                phone_number=contact.phone_number,
                contact_id=contact.id,
                reason="Automation opt-out",
                source="auto",
            ))
            from app.services.sms_service import SMSService
            await SMSService(self.db)._stop_seq(contact.id)
            return

        if action_type == ADD_TAG:
            await self._set_tag(contact, action.get("value"), add=True)
            return

        if action_type == REMOVE_TAG:
            await self._set_tag(contact, action.get("value"), add=False)
            return

        if action_type == SET_STATUS:
            value = (action.get("value") or "").strip()
            if value:
                contact.lead_status = value
            return

        if action_type == DELETE_CONTACT:
            from app.api.v1.contacts import _delete_contact_permanently
            await _delete_contact_permanently(self.db, contact)
            return

        logger.warning("AUTOMATION: unknown action type %r ignored", action_type)

    async def _set_tag(self, contact: Contact, value, add: bool) -> None:
        name = (value or "").strip()
        if not name:
            return
        tag = (
            await self.db.execute(select(Tag).where(Tag.name == name))
        ).scalar_one_or_none()
        if add:
            if tag is None:
                tag = Tag(name=name)
                self.db.add(tag)
                await self.db.flush()
            existing = (
                await self.db.execute(
                    select(ContactTag).where(
                        ContactTag.contact_id == contact.id, ContactTag.tag_id == tag.id
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                self.db.add(ContactTag(contact_id=contact.id, tag_id=tag.id))
        else:
            if tag is not None:
                link = (
                    await self.db.execute(
                        select(ContactTag).where(
                            ContactTag.contact_id == contact.id, ContactTag.tag_id == tag.id
                        )
                    )
                ).scalar_one_or_none()
                if link is not None:
                    await self.db.delete(link)
