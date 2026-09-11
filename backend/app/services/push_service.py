"""Inbuilt notifications: in-app centre + free VAPID browser push.

No Pushover, no OneSignal, no quotas. Every event is:

1. stored as a ``NotificationEvent`` (provider="browser") so the bell icon,
   the notification list and the unread badge always have something to show;
2. fanned out to every subscribed browser via Web Push. On Android this
   arrives like any native notification, even with the app closed.

All sending is best-effort and never blocks SMS work: failures are counted
per subscription and dead endpoints are pruned silently.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.notification import NotificationEvent
from app.models.push import PushSubscription
from app.services.system_settings import get_vapid_keys

logger = logging.getLogger(__name__)

BROWSER_PROVIDER = "browser"
MAX_FAILS = 5


async def notify(
    db: AsyncSession,
    event_type: str,
    title: str,
    body: str = "",
    *,
    url: Optional[str] = None,
    reference_id: Optional[int] = None,
    reference_type: Optional[str] = None,
    tag: Optional[str] = None,
) -> NotificationEvent:
    """Record an event AND push it to all subscribed browsers."""
    evt = NotificationEvent(
        event_type=event_type,
        provider=BROWSER_PROVIDER,
        title=(title or "")[:255],
        body=(body or "")[:2000],
        status="pending",
        reference_id=reference_id,
        reference_type=reference_type,
        is_read=False,
        url=(url or "")[:500] or None,
    )
    db.add(evt)
    await db.flush()

    sent, failed = await _fanout(db, title, body, url=url, tag=tag or event_type)
    evt.status = "sent" if sent else ("skipped" if not failed else "failed")
    if failed and not sent:
        evt.error = f"{failed} subscription(s) failed"
    await db.flush()
    return evt


async def _fanout(
    db: AsyncSession,
    title: str,
    body: str,
    *,
    url: Optional[str] = None,
    tag: Optional[str] = None,
) -> tuple[int, int]:
    """Push one payload to every subscription. Returns (sent, failed)."""
    subs = list((await db.execute(select(PushSubscription))).scalars().all())
    if not subs:
        return 0, 0

    import asyncio

    keys = await get_vapid_keys(db)
    payload = json.dumps({
        "title": title,
        "body": (body or "")[:500],
        "url": url or "/",
        "tag": tag or "sendsms",
        "icon": "/icon-192.png",
        "badge": "/icon-192.png",
    })

    def _one(sub: PushSubscription) -> str:
        try:
            from pywebpush import WebPushException, webpush

            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=keys["private"],
                vapid_claims={"sub": f"mailto:{settings.VAPID_CLAIM_EMAIL}"},
                timeout=10,
            )
            return "ok"
        except Exception as e:  # noqa: BLE001 — every failure mode prunes/retries
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):
                return "gone"
            logger.warning("Push to %s failed: %s", sub.id, e)
            return "fail"

    sent = failed = 0
    for sub in subs:
        try:
            outcome = await asyncio.to_thread(_one, sub)
        except Exception as e:  # pragma: no cover — paranoia
            logger.warning("Push thread error: %s", e)
            outcome = "fail"
        if outcome == "ok":
            sent += 1
            sub.fail_count = 0
            sub.last_used_at = datetime.now(timezone.utc)
        elif outcome == "gone":
            failed += 1
            await db.delete(sub)
        else:
            failed += 1
            sub.fail_count = (sub.fail_count or 0) + 1
            if sub.fail_count >= MAX_FAILS:
                logger.info("Pruning dead push subscription %s", sub.id)
                await db.delete(sub)
    await db.flush()
    return sent, failed


# Convenience wrappers for the standard events ------------------------------

async def notify_inbound_sms(db: AsyncSession, contact_name: str, text: str,
                             conversation_id: Optional[int] = None) -> NotificationEvent:
    return await notify(
        db, "new_reply", f"📱 New SMS from {contact_name}", text[:200],
        url=f"/inbox?conversation_id={conversation_id}" if conversation_id else "/inbox",
        reference_id=conversation_id, reference_type="conversation",
        tag=f"reply-{conversation_id}" if conversation_id else "reply",
    )


async def notify_campaign_done(db: AsyncSession, campaign_name: str,
                               sent: int = 0, delivered: int = 0,
                               campaign_id: Optional[int] = None) -> NotificationEvent:
    return await notify(
        db, "campaign_completed", f"✅ Campaign finished: {campaign_name}",
        f"{sent} sent, {delivered} delivered.",
        url="/campaigns", reference_id=campaign_id, reference_type="campaign",
        tag=f"campaign-{campaign_id}" if campaign_id else "campaign",
    )


async def notify_campaign_failed(db: AsyncSession, campaign_name: str, reason: str = "",
                                 campaign_id: Optional[int] = None) -> NotificationEvent:
    return await notify(
        db, "campaign_failed", f"❌ Campaign failed: {campaign_name}",
        (reason or "See the campaign page for details.")[:200],
        url="/campaigns", reference_id=campaign_id, reference_type="campaign",
        tag=f"campaign-{campaign_id}" if campaign_id else "campaign",
    )


async def notify_missed_call(db: AsyncSession, contact_name: str, phone: str) -> NotificationEvent:
    return await notify(
        db, "missed_call", f"📞 Missed call from {contact_name}", phone,
        url="/phone", reference_type="call", tag=f"missed-{phone}",
    )


async def notify_followup_due(db: AsyncSession, contact_name: str,
                              detail: str = "") -> NotificationEvent:
    return await notify(
        db, "followup_due", f"⏰ Follow-up due: {contact_name}",
        (detail or "A follow-up is waiting.")[:200],
        url="/follow-ups", tag="followup",
    )
