"""Inbuilt notifications: centre (list/read) + browser push subscriptions."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.notification import NotificationEvent
from app.models.push import PushSubscription
from app.models.user import User
from app.security.auth import get_current_user
from app.services.push_service import BROWSER_PROVIDER, notify

logger = logging.getLogger(__name__)
router = APIRouter()


def _out(e: NotificationEvent) -> dict:
    return {
        "id": e.id, "event_type": e.event_type, "title": e.title, "body": e.body,
        "status": e.status, "is_read": bool(e.is_read), "url": e.url,
        "reference_id": e.reference_id, "reference_type": e.reference_type,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.get("/")
async def list_notifications(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    q = select(NotificationEvent).where(NotificationEvent.provider == BROWSER_PROVIDER)
    if unread_only:
        q = q.where(NotificationEvent.is_read.is_(False))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    unread = (await db.execute(
        select(func.count()).where(NotificationEvent.provider == BROWSER_PROVIDER,
                                   NotificationEvent.is_read.is_(False)))).scalar() or 0
    rows = (await db.execute(
        q.order_by(desc(NotificationEvent.id)).offset((page - 1) * per_page).limit(per_page)
    )).scalars().all()
    return {"total": total, "unread": unread, "page": page, "per_page": per_page,
            "items": [_out(e) for e in rows]}


@router.get("/unread-count")
async def unread_count(db: AsyncSession = Depends(get_db),
                       cu: User = Depends(get_current_user)):
    n = (await db.execute(
        select(func.count()).where(NotificationEvent.provider == BROWSER_PROVIDER,
                                   NotificationEvent.is_read.is_(False)))).scalar() or 0
    last = (await db.execute(
        select(NotificationEvent.id).where(NotificationEvent.provider == BROWSER_PROVIDER)
        .order_by(desc(NotificationEvent.id)).limit(1))).scalar()
    return {"unread": n, "latest_id": last}


@router.post("/{event_id}/read")
async def mark_read(event_id: int, db: AsyncSession = Depends(get_db),
                    cu: User = Depends(get_current_user)):
    e = (await db.execute(select(NotificationEvent).where(
        NotificationEvent.id == event_id,
        NotificationEvent.provider == BROWSER_PROVIDER))).scalar_one_or_none()
    if e is None:
        raise HTTPException(404, "Notification not found")
    e.is_read = True
    await db.flush()
    return {"success": True}


@router.post("/read-all")
async def mark_all_read(db: AsyncSession = Depends(get_db),
                        cu: User = Depends(get_current_user)):
    await db.execute(
        update(NotificationEvent)
        .where(NotificationEvent.provider == BROWSER_PROVIDER,
               NotificationEvent.is_read.is_(False))
        .values(is_read=True))
    await db.flush()
    return {"success": True}


# --- Browser push subscriptions ---------------------------------------------

class PushKeys(BaseModel):
    p256dh: str
    auth: str


class SubscribeIn(BaseModel):
    endpoint: str
    keys: PushKeys
    user_agent: Optional[str] = None


@router.get("/push/vapid-key")
async def vapid_key(db: AsyncSession = Depends(get_db),
                    cu: User = Depends(get_current_user)):
    """Public VAPID key the browser needs to subscribe (safe to expose)."""
    from app.services.system_settings import get_vapid_keys

    keys = await get_vapid_keys(db)
    await db.commit()
    return {"public_key": keys["public"]}


@router.post("/push/subscribe")
async def subscribe(payload: SubscribeIn, db: AsyncSession = Depends(get_db),
                    cu: User = Depends(get_current_user)):
    endpoint = (payload.endpoint or "").strip()
    p256dh = (payload.keys.p256dh or "").strip()
    auth = (payload.keys.auth or "").strip()
    if not endpoint.startswith("https://") or not p256dh or not auth:
        raise HTTPException(400, "Invalid push subscription")
    if len(endpoint) > 2000 or len(p256dh) > 500 or len(auth) > 200:
        raise HTTPException(400, "Invalid push subscription")
    existing = (await db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    ).scalar_one_or_none()
    if existing is None:
        db.add(PushSubscription(endpoint=endpoint, p256dh=p256dh, auth=auth,
                                 user_agent=(payload.user_agent or "")[:500]))
    else:
        existing.p256dh = p256dh
        existing.auth = auth
        existing.fail_count = 0
        if payload.user_agent:
            existing.user_agent = payload.user_agent[:500]
    await db.flush()
    await db.commit()
    total = (await db.execute(select(func.count(PushSubscription.id)))).scalar() or 0
    return {"success": True, "devices": total}


@router.post("/push/unsubscribe")
async def unsubscribe(payload: SubscribeIn, db: AsyncSession = Depends(get_db),
                      cu: User = Depends(get_current_user)):
    row = (await db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == (payload.endpoint or "").strip()))
    ).scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.flush()
    return {"success": True}


@router.get("/push/subscriptions")
async def list_subscriptions(db: AsyncSession = Depends(get_db),
                             cu: User = Depends(get_current_user)):
    rows = (await db.execute(select(PushSubscription).order_by(desc(PushSubscription.id)))).scalars().all()
    return {"total": len(rows), "items": [
        {"id": r.id, "endpoint_host": (r.endpoint.split("/")[2] if "://" in r.endpoint else "?")[:80],
         "user_agent": (r.user_agent or "")[:120],
         "created_at": r.created_at.isoformat() if r.created_at else None,
         "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None}
        for r in rows]}


@router.post("/push/test")
async def test_push(db: AsyncSession = Depends(get_db),
                    cu: User = Depends(get_current_user)):
    """Send yourself a test notification (in-app + browser push)."""
    evt = await notify(db, "test", "🔔 Test notification",
                       "Inbuilt notifications are working — no Pushover needed.",
                       url="/", tag="test")
    await db.commit()
    subs = (await db.execute(select(func.count(PushSubscription.id)))).scalar() or 0
    return {"success": True, "notification_id": evt.id, "devices": subs,
            "pushed": evt.status == "sent",
            "note": ("Sent to your browser." if subs else
                     "Saved in-app, but no browser is subscribed yet — tap Enable notifications.")}
