"""The mail app's API — every connected Gmail account, in folders.

Mounted at ``/api/v1/mail``. This is the backend for the **Mail** page: a Gmail-
shaped client where the operator picks an account (or *All inboxes*), picks a
folder (Inbox / Sent / Spam / Drafts / Trash), reads a message in a threaded
pane, and replies — with the reply leaving from the account that received the
original.

Separate from ``/api/v1/mailbox`` on purpose. That router is about *connection*
(OAuth, credentials, filters, the routing diagnosis); this one is about *mail*.
The mirror they read from is
:mod:`app.services.mailbox_message_service`, which Gmail stays the source of
truth for: a flag, a move or a delete is applied to Gmail first and the stored
row only reflects what Gmail reports.

Nothing here is public. Every route needs a logged-in operator.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.security.auth import get_current_user
from app.services import mailbox_message_service, mailbox_service

logger = logging.getLogger(__name__)

router = APIRouter()


class FlagsIn(BaseModel):
    """A read/unread/starred change. Omitted fields are left alone."""

    read: Optional[bool] = None
    starred: Optional[bool] = None


class MoveIn(BaseModel):
    folder: str = Field(min_length=1, max_length=20)


class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=200_000)
    html: Optional[str] = None
    #: "Reply all": copy the other addresses the original was sent to.
    to_all: bool = False


class RefreshIn(BaseModel):
    #: Re-download everything in the look-back window, ignoring the watermarks.
    full: bool = False


# ---------------------------------------------------------------------------
# Overview + listing
# ---------------------------------------------------------------------------


@router.get("/overview")
async def overview(
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """The left rail: every account, its per-folder counts, and its sync health.

    Called on load and on the mail page's poll, which is what makes the unread
    badge on the sidebar and the counts beside each folder stay live.
    """
    return await mailbox_message_service.accounts_overview(db)


@router.get("/messages")
async def all_messages(
    folder: str = Query("INBOX", max_length=20),
    q: Optional[str] = Query(default=None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    mailbox_id: Optional[int] = Query(default=None),
    conversation_id: Optional[int] = Query(default=None),
    contact_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """One page of messages — one account when ``mailbox_id`` is set, else all."""
    return await mailbox_message_service.list_messages(
        db, mailbox_id=mailbox_id, folder=folder, query=q, limit=limit, offset=offset,
        conversation_id=conversation_id, contact_id=contact_id,
    )


@router.get("/messages/{mailbox_id}/{provider_message_id}")
async def one_message(
    mailbox_id: int,
    provider_message_id: str,
    body: bool = Query(True, description="Include the body and the whole thread"),
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """One message with its thread; a truncated body is refetched from Gmail."""
    payload = await mailbox_message_service.read_message(
        db, mailbox_id, provider_message_id, with_body=body
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="That message is not in the mirror yet.")
    await db.commit()
    return payload


@router.post("/messages/{mailbox_id}/{provider_message_id}/flags")
async def set_flags(
    mailbox_id: int,
    provider_message_id: str,
    payload: FlagsIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Mark read/unread or star/unstar — in Gmail, then in the mirror."""
    if payload.read is None and payload.starred is None:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    result = await mailbox_message_service.set_flags(
        db, mailbox_id, provider_message_id, read=payload.read, starred=payload.starred
    )
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=str(result.get("error")))
    await db.commit()
    return result


@router.post("/messages/{mailbox_id}/{provider_message_id}/move")
async def move_message(
    mailbox_id: int,
    provider_message_id: str,
    payload: MoveIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Move between Inbox / Sent / Spam / Drafts / Trash (Gmail labels)."""
    result = await mailbox_message_service.move(
        db, mailbox_id, provider_message_id, payload.folder
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=str(result.get("error")))
    await db.commit()
    return result


@router.post("/messages/{mailbox_id}/{provider_message_id}/delete")
async def delete_message(
    mailbox_id: int,
    provider_message_id: str,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Move to Trash. Gmail's Delete is reversible, and so is this."""
    result = await mailbox_message_service.delete(db, mailbox_id, provider_message_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=str(result.get("error")))
    await db.commit()
    return result


@router.post("/messages/{mailbox_id}/{provider_message_id}/reply")
async def reply_to_message(
    mailbox_id: int,
    provider_message_id: str,
    payload: ReplyIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Reply — and the reply leaves from the account that got the original.

    That is the rule the operator set and it is enforced here rather than
    guessed: the mailbox id in the path *is* the mailbox the message landed in,
    so the From address cannot drift onto a Brevo sender or "another Gmail".
    """
    result = await mailbox_message_service.send_reply(
        db, mailbox_id, provider_message_id,
        body=payload.body, html=payload.html, to_all=payload.to_all,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=str(result.get("error")))
    await db.commit()
    return result


@router.post("/refresh")
async def refresh(
    payload: Optional[RefreshIn] = None,
    mailbox_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Pull new mail now instead of waiting for the poll.

    Runs the *reply* sync and the mailbox mirror together, so pressing refresh
    in the mail app does what pressing it in Gmail does: new mail appears, and
    anything that is a CRM reply is threaded into its conversation too.
    """
    full = bool(payload and payload.full)
    mailboxes = await mailbox_service.list_mailboxes(db)
    if mailbox_id is not None:
        mailboxes = [m for m in mailboxes if m.id == int(mailbox_id)]
        if not mailboxes:
            raise HTTPException(status_code=404, detail="That mailbox is not connected.")
    results = []
    for mailbox in mailboxes:
        try:
            reply_summary = await mailbox_service.sync_mailbox(db, mailbox)
        except Exception as exc:  # noqa: BLE001 — report, never 500
            reply_summary = {"success": False, "errors": [str(exc)[:200]]}
        mirror = (
            await mailbox_message_service.mirror_mailbox(db, mailbox, force_full=full)
            if full else {"stored": reply_summary.get("mirrored") or 0}
        )
        mirror_errors = [str(error)[:300] for error in (mirror.get("errors") or []) if error]
        if mirror.get("error"):
            mirror_errors.insert(0, str(mirror["error"])[:300])
        if mirror.get("success") is False and not mirror_errors:
            mirror_errors.append("Mailbox mirror did not complete")
        results.append({
            "mailbox_id": mailbox.id,
            "email": mailbox.email_address,
            "replies": int(reply_summary.get("stored") or 0),
            "rescued": int(reply_summary.get("rescued") or 0),
            "mirrored": int(mirror.get("stored") or 0),
            "errors": (reply_summary.get("errors") or []) + mirror_errors,
        })
    await db.commit()
    return {
        "success": all(not r["errors"] for r in results),
        "accounts": results,
        "replies": sum(r["replies"] for r in results),
        "mirrored": sum(r["mirrored"] for r in results),
        "overview": await mailbox_message_service.accounts_overview(db),
    }
