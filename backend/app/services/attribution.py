"""Campaign attribution for conversations.

WHY THIS EXISTS
---------------
Every send path already knew which campaign it was sending for, but only the
``messages`` row recorded it. The inbox reads ``conversations``, so a thread
could not say which campaign the lead came from without scanning its whole
message history on every render.

This module owns one question -- *which campaign is this lead from?* -- and
answers it consistently for both campaign systems:

* ``campaigns``      -- the classic Campaigns page (``Conversation.campaign_id``)
* ``ads_campaigns``  -- the SMS Ads Manager (``Conversation.ads_campaign_id``)

Exactly one of the two is set on a given conversation.

Two attributions are stored, because they answer different questions:

``campaign_id`` / ``ads_campaign_id``
    FIRST-TOUCH. The campaign that originally sourced this lead. This is the
    badge shown in the inbox, and it never changes once set -- a lead that
    came from "January Promo" is still from January Promo after you reply to
    them by hand.

``last_campaign_id`` / ``last_ads_campaign_id``
    LAST-TOUCH. The most recent campaign to message the contact. Replies are
    credited here, which matches how ``SMSService.process_inbound_message``
    already credits ``Campaign.replies``.

Everything here is best-effort and additive: a failure must never be able to
stop an SMS from being sent or an inbound message from being stored.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message

logger = logging.getLogger(__name__)


def stamp_conversation(
    conversation: Conversation,
    *,
    campaign_id: Optional[int] = None,
    ads_campaign_id: Optional[int] = None,
) -> None:
    """Record that ``campaign_id``/``ads_campaign_id`` just messaged this thread.

    First touch is written once and then left alone; last touch is always
    refreshed. Passing neither id is a no-op, so callers can hand through an
    optional campaign without branching.
    """
    if conversation is None:
        return
    if campaign_id is None and ads_campaign_id is None:
        return

    # First touch: only if this thread has never been attributed.
    already_attributed = bool(conversation.campaign_id or conversation.ads_campaign_id)
    if not already_attributed:
        if campaign_id is not None:
            conversation.campaign_id = campaign_id
        else:
            conversation.ads_campaign_id = ads_campaign_id

    # Last touch: always the newest campaign to send.
    if campaign_id is not None:
        conversation.last_campaign_id = campaign_id
        conversation.last_ads_campaign_id = None
    else:
        conversation.last_ads_campaign_id = ads_campaign_id
        conversation.last_campaign_id = None


async def backfill_conversation(db: AsyncSession, conversation: Conversation) -> bool:
    """Derive attribution for a thread created before this feature existed.

    Reads the contact's own message history (which has always carried
    ``campaign_id``) and fills in whatever is missing. Returns True when
    something changed, so the caller can decide whether to flush.

    This is what makes the inbox badge correct for existing data without a
    migration job: the first time a legacy thread is listed or opened, it is
    repaired in place.
    """
    if conversation is None:
        return False
    has_first = bool(conversation.campaign_id or conversation.ads_campaign_id)
    has_last = bool(conversation.last_campaign_id or conversation.last_ads_campaign_id)
    if has_first and has_last:
        return False

    changed = False

    if not has_first:
        first = (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.direction == "outgoing",
                )
                .order_by(Message.created_at.asc(), Message.id.asc())
                .limit(50)
            )
        ).scalars().all()
        for msg in first:
            if msg.campaign_id:
                conversation.campaign_id = msg.campaign_id
                changed = True
                break
            if getattr(msg, "ads_campaign_id", None):
                conversation.ads_campaign_id = msg.ads_campaign_id
                changed = True
                break

    if not has_last:
        recent = (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.direction == "outgoing",
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(50)
            )
        ).scalars().all()
        for msg in recent:
            if msg.campaign_id:
                conversation.last_campaign_id = msg.campaign_id
                changed = True
                break
            if getattr(msg, "ads_campaign_id", None):
                conversation.last_ads_campaign_id = msg.ads_campaign_id
                changed = True
                break

    return changed


async def campaign_label_map(db: AsyncSession, conversations: list[Conversation]) -> dict:
    """Return ``{(kind, id): {...}}`` labels for a page of conversations.

    Two queries total, regardless of how many conversations are on the page --
    the inbox list renders a badge per row and must not run a query per row.
    ``kind`` is ``"campaign"`` or ``"ads"``.
    """
    campaign_ids = set()
    ads_ids = set()
    for conv in conversations:
        for cid in (conv.campaign_id, conv.last_campaign_id):
            if cid:
                campaign_ids.add(cid)
        for aid in (conv.ads_campaign_id, conv.last_ads_campaign_id):
            if aid:
                ads_ids.add(aid)

    labels: dict = {}

    if campaign_ids:
        from app.models.campaign import Campaign

        rows = (
            await db.execute(select(Campaign).where(Campaign.id.in_(campaign_ids)))
        ).scalars().all()
        for row in rows:
            labels[("campaign", row.id)] = {
                "id": row.id,
                "kind": "campaign",
                "name": row.name,
                "status": row.status,
            }

    if ads_ids:
        try:
            from app.models.ads import AdsCampaign

            rows = (
                await db.execute(select(AdsCampaign).where(AdsCampaign.id.in_(ads_ids)))
            ).scalars().all()
            for row in rows:
                labels[("ads", row.id)] = {
                    "id": row.id,
                    "kind": "ads",
                    "name": row.name,
                    "status": row.status,
                }
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("attribution: ads label lookup failed: %s", exc)

    return labels


def conversation_campaign(conv: Conversation, labels: dict) -> Optional[dict]:
    """The first-touch campaign badge for a conversation, or None."""
    if conv.campaign_id:
        return labels.get(("campaign", conv.campaign_id))
    if conv.ads_campaign_id:
        return labels.get(("ads", conv.ads_campaign_id))
    return None


def conversation_last_campaign(conv: Conversation, labels: dict) -> Optional[dict]:
    """The last-touch campaign for a conversation, or None."""
    if conv.last_campaign_id:
        return labels.get(("campaign", conv.last_campaign_id))
    if conv.last_ads_campaign_id:
        return labels.get(("ads", conv.last_ads_campaign_id))
    return None
