"""Connected-mailbox API — Gmail / IMAP inboxes this app reads and writes.

Mounted at ``/api/v1/mailbox``. This is the surface behind the **Replies** tab in
the Email Manager: connect a mailbox, watch it sync, install the "never send it
to Spam" filters, and see the reply-routing diagnosis that explains why replies
were disappearing.

Why a mailbox connection exists at all
--------------------------------------
Sending through Brevo with a ``@gmail.com`` From address cannot authenticate:
Brevo does not control gmail.com's SPF or DKIM, so the message fails DMARC. Gmail
still delivers most of it, but it taints the *thread* — and the prospect's reply
is the thing that lands in Spam, on the operator's side. Reading the mailbox
directly fixes the visibility half of that, and sending replies back out through
the same mailbox fixes the authentication half.

Security notes
--------------
* The stored credential (an OAuth refresh token, or an IMAP app password) is
  encrypted at rest with ``CREDENTIAL_ENCRYPTION_KEY`` and is **never** returned
  by any endpoint — :func:`serialize_mailbox` omits it entirely.
* The Google ``state`` parameter is a signed, expiring value carrying the user
  id, so the callback cannot be driven by a forged redirect and cannot attach
  someone else's mailbox to your account.
* Every route requires a logged-in operator. There is no public mailbox surface:
  the Brevo inbound webhook stays in ``app/api/v1/webhooks.py``.
"""

import base64
import hashlib
import hmac
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.email_inbox import EmailMailbox
from app.models.user import User
from app.providers import gmail as gmail_provider
from app.security.auth import get_current_user
from app.services import mailbox_service
from app.utils.urls import public_base_url

logger = logging.getLogger(__name__)

router = APIRouter()

STATE_TTL_SECONDS = 600
CALLBACK_PATH = "/api/v1/mailbox/google/callback"


# ===========================================================================
# Google OAuth plumbing
# ===========================================================================


def _sign(value: str) -> str:
    secret = (settings.SECRET_KEY or "sendersms").encode()
    return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()[:32]


def _make_state(user_id: int | None) -> str:
    """A signed, short-lived ``state`` for the Google round-trip."""
    issued = int(time.time())
    body = f"{user_id or 0}.{issued}.{int(time.time() * 1000) % 100000}"
    raw = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return f"{raw}.{_sign(body)}"


def _read_state(state: str | None) -> tuple[Optional[int], Optional[str]]:
    """Return ``(user_id, error)`` — the error is a sentence the operator sees."""
    if not state or "." not in state:
        return None, "The Google sign-in link expired. Start the connection again."
    raw, _, signature = state.rpartition(".")
    try:
        body = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
    except Exception:  # noqa: BLE001 — a malformed state is simply rejected
        return None, "The Google sign-in link was malformed. Start the connection again."
    if not hmac.compare_digest(_sign(body), signature):
        return None, "The Google sign-in link did not come from this app. Start again."
    parts = body.split(".")
    if len(parts) < 2 or not parts[1].isdigit():
        return None, "The Google sign-in link was malformed. Start the connection again."
    if int(time.time()) - int(parts[1]) > STATE_TTL_SECONDS:
        return None, "The Google sign-in link expired. Start the connection again."
    user_id = int(parts[0]) or None
    return user_id, None


def _google_client() -> tuple[str | None, str | None]:
    client_id = (settings.GOOGLE_CLIENT_ID or "").strip() or None
    secret = (settings.GOOGLE_CLIENT_SECRET or "").strip() or None
    return client_id, secret


def _redirect_uri() -> str:
    base = public_base_url()
    if not base:
        raise HTTPException(
            status_code=503,
            detail=(
                "PUBLIC_BASE_URL is not set, so the app cannot tell Google where to send you "
                "back. Set PUBLIC_BASE_URL to the public address of this deployment "
                "(for example https://crm.example.com) and restart. Until then, connect the "
                "mailbox with an IMAP app password instead — that path needs no redirect URI."
            ),
        )
    return f"{base}{CALLBACK_PATH}"


def _frontend_url(**params: str) -> str:
    base = public_base_url() or ""
    query = "&".join(f"{k}={v}" for k, v in params.items() if v)
    target = f"{base}/email-manager?section=Replies"
    return f"{target}&{query}" if query else target


def _result_page(title: str, body: str, *, ok: bool, url: str) -> HTMLResponse:
    """The page Google drops the operator on. Short, then it closes itself."""
    colour = "#16a34a" if ok else "#dc2626"
    icon = "&#10003;" if ok else "&#10007;"
    return HTMLResponse(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
 body{{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
      background:#f8fafc;color:#0f172a;margin:0;padding:48px 20px;display:flex;
      justify-content:center}}
 .card{{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:32px;
       max-width:520px;width:100%;box-shadow:0 10px 30px rgba(15,23,42,.06)}}
 .mark{{width:44px;height:44px;border-radius:50%;background:{colour}1a;color:{colour};
       display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:700}}
 h1{{font-size:19px;margin:14px 0 6px}} p{{font-size:14px;line-height:1.6;color:#475569;margin:0}}
 a{{display:inline-block;margin-top:20px;background:#2563eb;color:#fff;text-decoration:none;
   padding:10px 16px;border-radius:10px;font-size:14px;font-weight:600}}
 code{{background:#f1f5f9;padding:1px 5px;border-radius:5px;font-size:13px}}
</style></head><body><div class="card">
<div class="mark">{icon}</div>
<h1>{title}</h1><p>{body}</p>
<a href="{url}">Open the Email Manager</a>
<script>setTimeout(function(){{window.location.href="{url}";}},2500);</script>
</div></body></html>"""
    )


# ===========================================================================
# Request bodies
# ===========================================================================


def _clean_address(value: str, field: str) -> str:
    """Validate an email address without pulling in ``email-validator``.

    The rest of the app stores addresses as plain strings and validates them by
    hand, so this keeps one convention (and one less dependency) rather than
    introducing ``EmailStr`` just here.
    """
    address = (value or "").strip().strip("<>").lower()
    if "@" not in address or address.count("@") != 1:
        raise HTTPException(status_code=422, detail=f"{field} must be a single email address.")
    local, _, domain = address.partition("@")
    if not local or "." not in domain or not domain.split(".")[-1]:
        raise HTTPException(status_code=422, detail=f"{field} does not look like an email address.")
    return address


class MailboxConnectIn(BaseModel):
    """Connect a mailbox with an IMAP **app password** (no OAuth app needed)."""

    email_address: str
    app_password: str = Field(min_length=6, max_length=200)
    name: Optional[str] = Field(default=None, max_length=150)
    folders: Optional[str] = Field(default=None, max_length=500)
    import_all: bool = False
    rescue_from_spam: bool = True
    never_spam_filter: bool = False
    send_replies: bool = True
    poll_interval: Optional[int] = Field(default=None, ge=15, le=3600)
    client_id: Optional[str] = Field(default=None, max_length=255)
    client_secret: Optional[str] = Field(default=None, max_length=255)


class MailboxPatchIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=150)
    folders: Optional[str] = Field(default=None, max_length=500)
    import_all: Optional[bool] = None
    rescue_from_spam: Optional[bool] = None
    never_spam_filter: Optional[bool] = None
    send_replies: Optional[bool] = None
    poll_interval: Optional[int] = Field(default=None, ge=15, le=3600)
    is_active: Optional[bool] = None
    app_password: Optional[str] = Field(default=None, min_length=6, max_length=200)


class MailboxTestSendIn(BaseModel):
    to_address: str
    subject: str = Field(default="SENDERSMS mailbox test", max_length=300)
    body: str = Field(default="This message was sent from your connected mailbox by SENDERSMS.",
                      max_length=4000)


# ===========================================================================
# Routes
# ===========================================================================


@router.get("/report")
async def reply_routing_report(
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """The diagnosis panel: why replies go missing, and what each path offers."""
    return await mailbox_service.reply_routing_report(db)


@router.get("")
@router.get("/")
async def list_mailboxes(
    include_inactive: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    rows = await mailbox_service.list_mailboxes(db, include_inactive=include_inactive)
    client_id, client_secret = _google_client()
    return {
        "items": [mailbox_service.serialize_mailbox(m) for m in rows],
        "count": len(rows),
        "google_oauth_ready": bool(client_id and client_secret),
        "poll_interval": settings.GMAIL_POLL_INTERVAL,
    }


@router.get("/google/start")
async def google_start(
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """The Google consent URL for "Connect with Google"."""
    client_id, _ = _google_client()
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Google OAuth is not configured on this deployment: GOOGLE_CLIENT_ID and "
                "GOOGLE_CLIENT_SECRET are missing. Create an OAuth client at "
                "console.cloud.google.com (Desktop app), enable the Gmail API, add "
                f"{public_base_url() or 'https://your-app'}{CALLBACK_PATH} as an authorized "
                "redirect URI, and put both values in the environment. Or connect with an IMAP "
                "app password instead — that works without any Google project."
            ),
        )
    url = gmail_provider.google_authorize_url(
        client_id, _redirect_uri(), _make_state(cu.id)
    )
    return {"url": url, "redirect_uri": _redirect_uri(),
            "scope": gmail_provider.GMAIL_SCOPE}


@router.get("/google/callback")
async def google_callback(
    request_code: Optional[str] = Query(default=None, alias="code"),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    error_description: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Google sends the operator's browser back here with an authorization code.

    No ``get_current_user`` dependency: the user id travels inside the signed
    ``state`` instead, because the cookie may not be readable if the operator
    completed the consent in a fresh browser profile.
    """
    user_id, state_error = _read_state(state)
    if state_error:
        return _result_page("That link is no longer valid", state_error, ok=False,
                            url=_frontend_url(mailbox="error"))
    if error:
        detail = error_description or error
        if error == "access_denied":
            detail = ("You cancelled the Google sign-in. Nothing was changed — connect again "
                      "whenever you are ready.")
        return _result_page("Google did not grant access", detail, ok=False,
                            url=_frontend_url(mailbox="error"))
    if not request_code:
        return _result_page("Google sent no code", "The redirect arrived without an "
                            "authorization code. Try connecting again.", ok=False,
                            url=_frontend_url(mailbox="error"))

    client_id, client_secret = _google_client()
    if not client_id or not client_secret:
        return _result_page(
            "OAuth is not configured",
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are missing on this server, so the code "
            "cannot be exchanged. Connect with an IMAP app password instead.",
            ok=False, url=_frontend_url(mailbox="error"),
        )

    exchanged = await gmail_provider.google_exchange_code(
        client_id, client_secret, request_code, _redirect_uri()
    )
    if not exchanged.get("success"):
        return _result_page("Google rejected the code", str(exchanged.get("error")), ok=False,
                            url=_frontend_url(mailbox="error"))

    token_result = await gmail_provider.google_refresh_access_token(
        client_id, client_secret, str(exchanged["refresh_token"])
    )
    if not token_result.get("success"):
        return _result_page("Could not read the mailbox", str(token_result.get("error")),
                            ok=False, url=_frontend_url(mailbox="error"))
    profile = await gmail_provider.gmail_profile(str(token_result["access_token"]))
    if not profile.get("success"):
        return _result_page("Could not read the mailbox", str(profile.get("error")), ok=False,
                            url=_frontend_url(mailbox="error"))

    address = str(profile.get("email") or "").strip().lower()
    if not address:
        return _result_page("Google did not say which address", "The profile call returned no "
                            "email address.", ok=False, url=_frontend_url(mailbox="error"))

    existing = await mailbox_service.find_mailbox_for_address(db, address)
    if existing is not None:
        from app.security.encryption import encrypt_value

        existing.provider = "gmail_api"
        existing.credential_encrypted = encrypt_value(str(exchanged["refresh_token"]))[:2000]
        existing.client_id = client_id
        existing.is_active = True
        existing.last_error = None
        mailbox = existing
        verb = "reconnected"
    else:
        mailbox = await mailbox_service.create_mailbox(
            db,
            name=f"Replies — {address}",
            email_address=address,
            provider="gmail_api",
            credential=str(exchanged["refresh_token"]),
            client_id=client_id,
            client_secret=client_secret,
            poll_interval=settings.GMAIL_POLL_INTERVAL,
            rescue_from_spam=settings.GMAIL_RESCUE_FROM_SPAM,
            send_replies=settings.GMAIL_SEND_REPLIES,
            never_spam_filter=False,
        )
        verb = "connected"

    # Prove the connection immediately: create the label, then import anything
    # already sitting in the inbox and in Spam. The operator sees real replies on
    # the very first screen instead of an empty list.
    try:
        await mailbox_service.check_credentials(db, mailbox)
        summary = await mailbox_service.sync_mailbox(db, mailbox)
        imported = int(summary.get("stored") or 0)
    except Exception as exc:  # noqa: BLE001 — a first-sync failure must not lose the connection
        logger.warning("MAILBOX first sync failed for %s: %s", address, exc)
        imported = 0

    await db.commit()

    detail = (
        f"<code>{address}</code> is {verb}. "
        + (f"{imported} repl{'y' if imported == 1 else 'ies'} imported on the first sync."
           if imported else
           "No replies were waiting right now — the app checks this mailbox every "
           f"{mailbox.poll_interval or settings.GMAIL_POLL_INTERVAL} seconds from here on.")
        + "<br><br>Next: turn on <b>Never send it to Spam</b> for your prospects so Google stops "
          "filing their replies away."
    )
    return _result_page("Mailbox connected", detail, ok=True,
                        url=_frontend_url(mailbox=str(mailbox.id), address=address))


@router.post("")
@router.post("/")
async def connect_mailbox(
    payload: MailboxConnectIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Connect a mailbox with an IMAP app password (works with no Google project)."""
    address = _clean_address(payload.email_address, "email_address")
    password = payload.app_password.replace(" ", "")

    if "@gmail.com" in address or "@googlemail.com" in address:
        if len(password) < 16:
            raise HTTPException(
                status_code=400,
                detail=(
                    "That does not look like a Google app password — your normal Gmail password "
                    "will be rejected by IMAP. Turn on 2-Step Verification, then create a "
                    "16-character app password at myaccount.google.com/apppasswords and paste "
                    "that here (spaces are optional)."
                ),
            )

    existing = await mailbox_service.find_mailbox_for_address(db, address)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"{address} is already connected. Edit that mailbox instead.",
        )

    checked = await gmail_provider.imap_check(address, password)
    if not checked.get("success"):
        raise HTTPException(status_code=400, detail=str(checked.get("error")))

    provider_kind = "gmail_api" if (payload.client_id and payload.client_secret) else "imap"
    mailbox = await mailbox_service.create_mailbox(
        db,
        name=payload.name or f"Replies — {address}",
        email_address=address,
        provider=provider_kind,
        credential=password,
        client_id=payload.client_id,
        client_secret=payload.client_secret,
        folders=payload.folders,
        import_all=payload.import_all,
        rescue_from_spam=payload.rescue_from_spam,
        never_spam_filter=payload.never_spam_filter,
        send_replies=payload.send_replies,
        poll_interval=payload.poll_interval or settings.GMAIL_POLL_INTERVAL,
    )
    await db.commit()
    await db.refresh(mailbox)

    summary = {"success": True, **checked}
    try:
        summary = await mailbox_service.sync_mailbox(db, mailbox)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("MAILBOX first sync failed for %s: %s", address, exc)
        summary = {"success": False, "errors": [str(exc)[:300]], **checked}

    return {"mailbox": mailbox_service.serialize_mailbox(mailbox), "first_sync": summary}


@router.get("/{mailbox_id}")
async def read_mailbox(
    mailbox_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    mailbox = await _require(db, mailbox_id)
    return {"mailbox": mailbox_service.serialize_mailbox(mailbox)}


@router.patch("/{mailbox_id}")
async def patch_mailbox(
    mailbox_id: int,
    payload: MailboxPatchIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    mailbox = await _require(db, mailbox_id)
    data = payload.model_dump(exclude_unset=True)
    password = data.pop("app_password", None)
    if password:
        from app.security.encryption import encrypt_value

        mailbox.credential_encrypted = encrypt_value(password.replace(" ", ""))[:2000]
        mailbox.last_error = None
    for field, value in data.items():
        if value is not None:
            setattr(mailbox, field, value)
    await db.commit()
    await db.refresh(mailbox)
    return {"mailbox": mailbox_service.serialize_mailbox(mailbox)}


@router.delete("/{mailbox_id}")
async def disconnect_mailbox(
    mailbox_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Disconnect. Removes the Gmail filters this app created; keeps the inbox."""
    mailbox = await _require(db, mailbox_id)
    removed = 0
    try:
        removed = await mailbox_service.remove_filters(db, mailbox)
    except Exception as exc:  # noqa: BLE001 — filters are best-effort on the way out
        logger.warning("MAILBOX filter cleanup failed: %s", exc)
    address = mailbox.email_address
    await db.delete(mailbox)
    await db.commit()
    return {"success": True, "email_address": address, "filters_removed": removed,
            "detail": ("Imported replies stay in the email inbox; only the connection and its "
                       "stored credential were removed. Revoke the app password or the Google "
                       "grant separately if you want it dead everywhere.")}


@router.post("/{mailbox_id}/check")
async def check_mailbox(
    mailbox_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Verify the stored credential still works, without a full sync."""
    mailbox = await _require(db, mailbox_id)
    result = await mailbox_service.check_credentials(db, mailbox)
    await db.commit()
    return result


@router.post("/{mailbox_id}/sync")
async def sync_mailbox_now(
    mailbox_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Import replies right now, ignoring the poll interval."""
    mailbox = await _require(db, mailbox_id)
    summary = await mailbox_service.sync_mailbox(db, mailbox)
    await db.commit()
    return summary


@router.post("/{mailbox_id}/filters")
async def manage_filters(
    mailbox_id: int,
    install: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Install or remove the Gmail *Never send it to Spam* filters.

    ``removeLabelIds: ["SPAM"]`` is exactly what Gmail's own filter editor writes
    when you tick "Never send it to Spam" — there is no separate flag.
    """
    mailbox = await _require(db, mailbox_id)
    if mailbox.provider != "gmail_api":
        raise HTTPException(
            status_code=400,
            detail=(
                "Gmail filters need the Gmail API. This mailbox is connected over IMAP, which "
                "cannot create filters. Reconnect it with Google sign-in to use this, or keep "
                "the automatic Spam rescue on — that moves replies back out of Spam after they "
                "land, which needs nothing but IMAP."
            ),
        )
    if install:
        result = await mailbox_service.ensure_never_spam_filters(db, mailbox)
        mailbox.never_spam_filter = bool(result.get("success"))
    else:
        removed = await mailbox_service.remove_filters(db, mailbox)
        mailbox.never_spam_filter = False
        result = {"success": True, "removed": removed, "filters": []}
    await db.commit()
    return result


@router.post("/{mailbox_id}/test-send")
async def test_send(
    mailbox_id: int,
    payload: MailboxTestSendIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Send one message out of the mailbox, to prove replies will leave correctly."""
    mailbox = await _require(db, mailbox_id)
    result = await mailbox_service.send_via_mailbox(
        db, mailbox,
        to_address=_clean_address(payload.to_address, "to_address"),
        to_name=None,
        subject=payload.subject,
        text=payload.body,
        html=None,
        from_name=mailbox.name,
    )
    await db.commit()
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=str(result.get("error")))
    return result


async def _require(db: AsyncSession, mailbox_id: int) -> EmailMailbox:
    mailbox = await mailbox_service.get_mailbox(db, mailbox_id)
    if mailbox is None:
        raise HTTPException(status_code=404, detail="That mailbox is not connected.")
    return mailbox
