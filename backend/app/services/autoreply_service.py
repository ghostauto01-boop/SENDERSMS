"""Auto-reply evaluation and sending.

Kept separate from SMSService so the matching logic can be tested without a
gateway, and so a failure here can never take down inbound message storage --
receiving a message is more important than answering it.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.autoreply import AutoReplyRule
from app.models.conversation import Message
from app.utils.templating import render_template

logger = logging.getLogger(__name__)


def rule_matches(rule: AutoReplyRule, body: str) -> bool:
    """Does this rule fire for this inbound text?

    Case-insensitive throughout. A rule with no keywords never matches unless
    it is an explicit catch-all ("any"), otherwise an operator who saved a
    half-filled form would silently start replying to everything.
    """
    text = (body or "").strip().lower()
    if rule.match_type == "any":
        return True
    if not text:
        return False

    keywords = rule.keyword_list()
    if not keywords:
        return False

    if rule.match_type == "exact":
        return text in keywords
    if rule.match_type == "starts":
        return any(text.startswith(k) for k in keywords)
    # default: contains
    return any(k in text for k in keywords)


class AutoReplyService:
    def __init__(self, db):
        self.db = db

    async def find_matching_rules(
        self, body: str, channel: str = "sms"
    ) -> list[AutoReplyRule]:
        """Enabled rules for THIS channel that match, in priority order.

        Channel scoping matters: an SMS rule that answers "price?" must not
        start answering email replies with a text message.
        """
        result = await self.db.execute(
            select(AutoReplyRule)
            .where(
                AutoReplyRule.is_enabled == True,  # noqa: E712
                AutoReplyRule.channel == channel,
            )
            .order_by(AutoReplyRule.priority.asc(), AutoReplyRule.id.asc())
        )
        matched = []
        for rule in result.scalars().all():
            if rule_matches(rule, body):
                matched.append(rule)
                if rule.stop_on_match:
                    break
        return matched

    async def _in_cooldown(self, rule: AutoReplyRule, contact_id: int) -> bool:
        """Have we already auto-replied to this contact recently?

        The cooldown is per contact, not global: one chatty contact must not
        mute the autoresponder for everybody else.
        """
        if not rule.cooldown_minutes:
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=rule.cooldown_minutes)
        # Cooldown is per channel: a reply sent to an SMS must not silence the
        # email autoresponder for the same contact.
        query = (
            select(Message)
            .where(
                Message.contact_id == contact_id,
                Message.direction == "outgoing",
                Message.is_auto_reply == True,  # noqa: E712
                Message.created_at >= cutoff,
            )
        )
        if getattr(rule, "channel", None):
            query = query.where(Message.channel == rule.channel)
        recent = await self.db.execute(query.limit(1))
        return recent.scalars().first() is not None

    async def build_reply(self, contact, body: str, channel: str = "sms"):
        """Return (rule, rendered_text) for the first rule that should fire.

        Returns (None, None) when nothing matches or the contact is in
        cooldown. Never raises for ordinary "no reply" cases.
        """
        # Never argue with someone who just opted out. Each channel has its own
        # consent flag, so an email unsubscribe never silences SMS replies.
        if channel == "email":
            if getattr(contact, "is_email_opted_out", False):
                return None, None
        elif getattr(contact, "is_opted_out", False):
            return None, None

        for rule in await self.find_matching_rules(body, channel=channel):
            if await self._in_cooldown(rule, contact.id):
                logger.info(
                    "AUTOREPLY: rule %s matched but contact %s is in cooldown",
                    rule.id,
                    contact.id,
                )
                continue
            from app.services.variable_service import render_for_contact
            text = await render_for_contact(self.db, rule.reply_body, contact)
            if not text or not text.strip():
                logger.warning("AUTOREPLY: rule %s rendered empty; skipping", rule.id)
                continue
            return rule, text
        return None, None
