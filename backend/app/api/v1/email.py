"""Email channel API — senders, sending, inbox, suppression and analytics.

This router is the email twin of the SMS surface. It is deliberately a *separate
router* (mounted at ``/api/v1/email``) rather than extra parameters on the SMS
endpoints: the SMS pages can then keep calling their own URLs unchanged, and the
Email Manager page has one obvious place for everything it needs.

What lives here:

* ``/accounts``       — the many Brevo API keys (add, test, default, disable)
* ``/send``           — one-off / bulk / scheduled email from the UI
* ``/preview``        — render a subject + body for a real contact
* ``/history``        — every email message, with retry
* ``/inbox/*``        — the two-way email inbox threads
* ``/suppression``    — the email do-not-contact list
* ``/events``         — raw Brevo webhook events
* ``/overview``,``/stats``,``/reference`` — the Email Manager dashboard

Credentials never leave the server: ``serialize_account`` returns a masked key.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.conversation import Conversation, Message
from app.models.email import EmailEvent
from app.models.email import EmailAccount, EmailSuppression
from app.models.scheduled import ScheduledMessage
from app.models.template import Template
from app.models.user import User
from app.security.encryption import decrypt_value
from app.utils.urls import public_base_url
from app.schemas.email import (
    EmailAccountIn,
    EmailComposerTestIn,
    EmailAccountPatch,
    EmailPreviewIn,
    EmailReplyIn,
    EmailSendIn,
    EmailSuppressionIn,
    EmailTestSendIn,
)
from app.security.auth import get_current_user
from app.services import email_service

router = APIRouter()

MESSAGE_STATUSES = ("queued", "sending", "sent", "delivered", "failed", "retrying", "cancelled")


# ===========================================================================
# Serializers
# ===========================================================================


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _message_dict(message: Message, contact: Optional[Contact] = None,
                  account: Optional[EmailAccount] = None) -> dict:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "contact_id": message.contact_id,
        "campaign_id": message.campaign_id,
        "ads_campaign_id": message.ads_campaign_id,
        "direction": message.direction,
        "channel": message.channel or "email",
        "subject": message.subject,
        "body": message.body,
        "html_body": message.html_body,
        "from_address": message.from_address,
        "to_address": message.to_address,
        "email_account_id": message.email_account_id,
        "email_account_name": account.name if account else None,
        "status": message.status,
        "provider": message.provider,
        "provider_message_id": message.provider_message_id,
        "open_count": message.open_count or 0,
        "click_count": message.click_count or 0,
        "opened_at": _iso(message.opened_at),
        "clicked_at": _iso(message.clicked_at),
        "bounced_at": _iso(message.bounced_at),
        "bounced_hard": bool(message.bounced_hard),
        "is_auto_reply": bool(message.is_auto_reply),
        "attachments": email_service.attachment_summary(message.attachments),
        "cc": email_service.clean_addresses(getattr(message, "cc_addresses", None)),
        "bcc": email_service.clean_addresses(getattr(message, "bcc_addresses", None)),
        "rfc_message_id": message.rfc_message_id,
        "in_reply_to": message.in_reply_to,
        "retry_count": message.retry_count or 0,
        "last_error": message.last_error,
        "sent_at": _iso(message.sent_at),
        "delivered_at": _iso(message.delivered_at),
        "failed_at": _iso(message.failed_at),
        "created_at": _iso(message.created_at),
        "contact_name": (contact.first_name or contact.last_name) if contact else None,
        "contact_email": contact.email if contact else message.to_address,
        "contact_phone": contact.phone_number if contact else None,
    }


async def _accounts_by_id(db: AsyncSession) -> dict[int, EmailAccount]:
    rows = (await db.execute(select(EmailAccount))).scalars().all()
    return {a.id: a for a in rows}


# ===========================================================================
# Senders / Brevo accounts
# ===========================================================================


@router.get("/accounts")
async def list_accounts(
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Every saved Brevo key, with its usage. The keys themselves are masked."""
    accounts = await email_service.list_accounts(db)
    items = []
    for account in accounts:
        await email_service.reset_daily_counter(account)
        stats = await email_service.account_stats(db, account.id)
        items.append(email_service.serialize_account(account, stats=stats))
    await db.flush()
    return {"total": len(items), "items": items}


@router.post("/accounts", status_code=201)
async def create_account(
    data: EmailAccountIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Add a Brevo API key + the From name/address it may send as."""
    if not (data.api_key or "").strip():
        raise HTTPException(422, "Paste the Brevo API key")
    if not email_service.normalize_email(data.from_email):
        raise HTTPException(422, "Enter a valid From email address")

    account = await email_service.create_account(
        db,
        name=data.name,
        from_name=data.from_name,
        from_email=data.from_email,
        api_key=data.api_key or "",
        reply_to=data.reply_to,
        daily_limit=data.daily_limit,
        is_active=data.is_active,
        is_default=data.is_default,
        track_opens=data.track_opens,
        track_clicks=data.track_clicks,
    )
    await email_service.ensure_webhook_token(db, account)
    await db.commit()
    await db.refresh(account)
    return email_service.serialize_account(account)


@router.patch("/accounts/{account_id}")
async def update_account(
    account_id: int,
    data: EmailAccountPatch,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    account = await email_service.get_account(db, account_id)
    if account is None:
        raise HTTPException(404, "Email account not found")

    updates = data.model_dump(exclude_unset=True)
    api_key = updates.pop("api_key", None)
    if api_key is not None and api_key.strip():
        from app.security.encryption import encrypt_value

        account.api_key_encrypted = encrypt_value(api_key.strip())
        account.connection_status = "unknown"
        account.last_error = None
    if "from_email" in updates and updates["from_email"]:
        if not email_service.normalize_email(updates["from_email"]):
            raise HTTPException(422, "Enter a valid From email address")
    for key, value in updates.items():
        if key == "reply_to":
            value = email_service.normalize_email(value) or None
        setattr(account, key, value)
    if updates.get("is_default"):
        await email_service.set_default_account(db, account)
    await db.commit()
    await db.refresh(account)
    return email_service.serialize_account(account)


@router.delete("/accounts/{account_id}", status_code=204)
async def delete_account(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    account = await email_service.get_account(db, account_id)
    if account is None:
        raise HTTPException(404, "Email account not found")
    used = (
        await db.execute(
            select(func.count()).select_from(Campaign).where(Campaign.email_account_id == account_id)
        )
    ).scalar() or 0
    if used:
        raise HTTPException(
            409,
            f"{used} campaign(s) still send through this account. Point them at another "
            "sender or switch this account off instead of deleting it.",
        )
    was_default = bool(account.is_default)
    await db.delete(account)
    await db.flush()
    if was_default:
        await email_service.ensure_default_account(db)
    await db.commit()


@router.post("/accounts/{account_id}/default")
async def make_default(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    account = await email_service.get_account(db, account_id)
    if account is None:
        raise HTTPException(404, "Email account not found")
    await email_service.set_default_account(db, account)
    await db.commit()
    await db.refresh(account)
    return email_service.serialize_account(account)


@router.post("/accounts/{account_id}/test")
async def test_account(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Ask Brevo whether this key works (``GET /v3/account``)."""
    account = await email_service.get_account(db, account_id)
    if account is None:
        raise HTTPException(404, "Email account not found")
    result = await email_service.test_account(db, account)
    await db.commit()
    return {
        "success": bool(result.get("success")),
        "error": result.get("error"),
        "account": email_service.serialize_account(account),
    }


@router.post("/accounts/{account_id}/test-send")
async def test_send(
    account_id: int,
    data: EmailTestSendIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Send a real one-off email so deliverability can be eyeballed."""
    account = await email_service.get_account(db, account_id)
    if account is None:
        raise HTTPException(404, "Email account not found")
    result = await email_service.send_test_email(db, account, data.to, subject=data.subject)
    await db.commit()
    return {"success": bool(result.get("success")), "error": result.get("error")}


@router.post("/senders-preview")
async def senders_preview(
    data: dict,
    cu: User = Depends(get_current_user),
):
    """What Brevo says about a key the user just typed (nothing is saved yet).

    Answers the question that decides deliverability: which From addresses has
    this key already verified, and is the domain behind them authenticated?
    Using a warm, verified sender is the single biggest thing a sender can do.
    """
    from app.providers.brevo import list_domains, list_senders

    api_key = (data or {}).get("api_key") or ""
    if not api_key.strip():
        raise HTTPException(422, "Paste the Brevo API key first")

    senders_result = await list_senders(api_key.strip())
    domains_result = await list_domains(api_key.strip())

    domains = domains_result.get("domains") or []
    by_domain = {str(d.get("domain") or "").lower(): d for d in domains}

    senders = []
    # ``list_senders`` returns "senders"; the account endpoint re-labels it as
    # "items". Accept either so this probe cannot silently return nothing.
    for item in (senders_result.get("senders") or senders_result.get("items") or []):
        address = (item.get("email") or item.get("Email") or "").strip()
        if not address:
            continue
        domain = address.split("@")[-1].lower()
        info = by_domain.get(domain) or {}
        senders.append({
            "email": address,
            "name": item.get("name") or item.get("Name") or "",
            "active": bool(item.get("active", True)),
            "domain": domain,
            "domain_verified": bool(
                info.get("verified") or info.get("authenticated")
            ),
            "spf": bool(info.get("spf")),
            "dkim": bool(info.get("dkim")),
            # A verified address on an authenticated domain is the one to use.
            "recommended": bool(
                (info.get("verified") or info.get("authenticated")) and info.get("dkim")
            ),
        })

    error = None
    if not senders_result.get("success"):
        error = senders_result.get("error") or "Brevo rejected this API key"
    elif not senders:
        error = (
            "Brevo accepted the key but has no verified sender in it yet. Add and "
            "verify a sender (or a whole domain) in Brevo → Senders & IP first."
        )

    return {
        "success": not error,
        "error": error,
        "senders": senders,
        "domains": domains,
        "domains_error": None if domains_result.get("success") else domains_result.get("error"),
    }


@router.post("/test-send")
async def composer_test_send(
    data: EmailComposerTestIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Mail what is in the composer to one address, before it goes to a list."""
    account = await email_service.get_account(db, data.email_account_id)
    if account is None:
        account = await email_service.get_default_account(db)
    if account is None:
        raise HTTPException(400, "No email sender configured yet")

    subject, body, html = data.subject or "", data.body or "", data.html_body
    attachments = data.attachments
    if data.template_id:
        template = (
            await db.execute(select(Template).where(Template.id == data.template_id))
        ).scalar_one_or_none()
        if template is None:
            raise HTTPException(404, "Template not found")
        subject = subject or template.subject or ""
        body = body or template.body or ""
        html = html or template.html_body
        if not attachments:
            attachments = email_service.load_attachments(template.attachments)

    contact = None
    if data.contact_id:
        contact = (
            await db.execute(select(Contact).where(Contact.id == data.contact_id))
        ).scalar_one_or_none()

    result = await email_service.send_composer_test(
        db,
        account=account,
        to_address=data.to,
        subject=subject,
        text_body=body,
        html_body=html,
        attachments=attachments,
        contact=contact,
    )
    if not result.get("success"):
        raise HTTPException(400, result.get("error") or "The test email failed")
    return result


@router.get("/accounts/{account_id}/senders")
async def verified_senders(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """The From addresses Brevo has actually verified for this key."""
    from app.providers.brevo import list_senders

    account = await email_service.get_account(db, account_id)
    if account is None:
        raise HTTPException(404, "Email account not found")
    from app.security.encryption import decrypt_value

    result = await list_senders(decrypt_value(account.api_key_encrypted or ""))
    if not result.get("success"):
        raise HTTPException(400, result.get("error") or "Brevo rejected the API key")
    return {"items": result.get("senders") or result.get("raw") or []}


# ===========================================================================
# Overview / analytics
# ===========================================================================


@router.get("/overview")
async def overview(
    days: int = Query(default=30, ge=1, le=365),
    account_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Everything the Email Manager landing tab shows, in one round trip."""
    totals = await email_service.email_totals(db, days=days, account_id=account_id)

    accounts = []
    for account in await email_service.list_accounts(db):
        stats = await email_service.account_stats(db, account.id)
        accounts.append(email_service.serialize_account(account, stats=stats))

    unread = (
        await db.execute(
            select(func.count()).select_from(Conversation).where(
                func.coalesce(Conversation.channel, "sms") == "email",
                Conversation.unread_count > 0,
            )
        )
    ).scalar() or 0

    # Recent EMAIL MANAGER (ads) campaigns — the campaigns users now create on
    # the Email Manager's Campaigns tab. Legacy list-blast campaigns still
    # live on the Campaigns page and are intentionally not listed here.
    from app.models.ads import AdsAssignment, AdsCampaign
    from app.services import ads_service as ads_svc

    campaigns = (
        await db.execute(
            select(AdsCampaign)
            .where(AdsCampaign.channel == "email")
            .order_by(AdsCampaign.id.desc())
            .limit(8)
        )
    ).scalars().all()
    campaign_stats: dict[int, dict] = {}
    for ads_campaign in campaigns:
        campaign_stats[ads_campaign.id] = await ads_svc._counts_for(
            db,
            [AdsAssignment.campaign_id == ads_campaign.id],
            campaign_id=ads_campaign.id,
        )

    events = (
        await db.execute(
            select(EmailEvent).order_by(EmailEvent.id.desc()).limit(12)
        )
    ).scalars().all()

    suppressed = (await db.execute(select(func.count()).select_from(EmailSuppression))).scalar() or 0

    return {
        "days": days,
        "totals": totals,
        "series": totals.get("series", []),
        "accounts": accounts,
        "default_account_id": next((a["id"] for a in accounts if a["is_default"]), None),
        "unread_conversations": unread,
        "suppressed_total": suppressed,
        "recent_campaigns": [
            {
                "id": c.id,
                "name": c.name,
                "status": c.status,
                "subject": c.subject,
                "messages_sent": campaign_stats.get(c.id, {}).get("sent", 0),
                "messages_delivered": campaign_stats.get(c.id, {}).get("delivered", 0),
                "messages_failed": campaign_stats.get(c.id, {}).get("failed", 0),
                "replies": campaign_stats.get(c.id, {}).get("replies", 0),
                "created_at": _iso(c.created_at),
            }
            for c in campaigns
        ],
        "recent_events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "email_address": e.email_address,
                "subject": e.subject,
                "detail": e.detail,
                "created_at": _iso(e.created_at),
            }
            for e in events
        ],
    }


@router.get("/stats")
async def stats(
    days: int = Query(default=30, ge=1, le=365),
    account_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    return await email_service.email_totals(db, days=days, account_id=account_id)


@router.get("/reference")
async def reference(
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Pick-list data for the email UI (senders, templates, lists, counts)."""
    accounts = [
        {
            "id": a.id,
            "name": a.name,
            "from_email": a.from_email,
            "from_name": a.from_name,
            "is_default": bool(a.is_default),
            "is_active": bool(a.is_active),
        }
        for a in await email_service.list_accounts(db)
    ]
    templates = (
        await db.execute(
            select(Template)
            .where(func.coalesce(Template.channel, "sms") == "email")
            .order_by(Template.name)
        )
    ).scalars().all()
    lists = (await db.execute(select(ContactList).order_by(ContactList.name))).scalars().all()

    emailable = (
        await db.execute(
            select(func.count())
            .select_from(Contact)
            .where(
                Contact.is_email_opted_out == False,  # noqa: E712
                Contact.is_email_undeliverable == False,  # noqa: E712
                func.coalesce(Contact.email, "").like("%@%"),
            )
        )
    ).scalar() or 0

    return {
        "accounts": accounts,
        "default_account_id": next((a["id"] for a in accounts if a["is_default"]), None),
        "templates": [
            {"id": t.id, "name": t.name, "subject": t.subject, "category": t.category}
            for t in templates
        ],
        "lists": [
            {"id": l.id, "name": l.name, "contact_count": l.contact_count or 0}
            for l in lists
        ],
        "emailable_contacts": emailable,
    }


@router.post("/preview")
async def preview(
    data: EmailPreviewIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Render a subject/body for one contact so the variable registry is visible."""
    subject, body, html = data.subject or "", data.body or "", data.html_body
    template_attachments: list = []
    if data.template_id:
        template = (
            await db.execute(select(Template).where(Template.id == data.template_id))
        ).scalar_one_or_none()
        if template is None:
            raise HTTPException(404, "Template not found")
        subject = data.subject or template.subject or ""
        body = data.body or template.body or ""
        html = html or template.html_body
        # Show the files the send would actually carry when none were passed.
        template_attachments = email_service.load_attachments(template.attachments)
    contact: Optional[Contact] = None
    if data.contact_id:
        contact = (
            await db.execute(select(Contact).where(Contact.id == data.contact_id))
        ).scalar_one_or_none()
        if contact is None:
            raise HTTPException(404, "Contact not found")
    if contact is None:
        return {
            "subject": subject,
            "text": body,
            "html": html or email_service.text_to_html(body),
            "attachments": email_service.attachment_summary(
                data.attachments or template_attachments
            ),
        }
    rendered = await email_service.render_email(
        db, contact, subject=subject, text_body=body, html_body=html
    )
    rendered["attachments"] = email_service.attachment_summary(
        data.attachments or template_attachments
    )
    return rendered


# ===========================================================================
# Sending
# ===========================================================================


def _resolve_account_or_400(db_accounts: dict, requested: Optional[int]) -> Optional[int]:
    if requested:
        return requested
    return None


@router.post("/send")
async def send_email(
    data: EmailSendIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Send now, send to a list, or schedule — the Email Manager's Send tab.

    Targets, in priority order: one contact, one raw address, or a whole list.
    A template can supply the subject/body; the explicit fields win.
    """
    from app.api.v1.send import _parse_schedule_at

    subject, body, html = data.subject or "", data.body or "", data.html_body
    template = None
    if data.template_id:
        template = (
            await db.execute(select(Template).where(Template.id == data.template_id))
        ).scalar_one_or_none()
        if template is None:
            raise HTTPException(404, "Template not found")
        subject = subject or template.subject or ""
        body = body or template.body or ""
        html = html or template.html_body
    if not (body or "").strip() and (html or "").strip():
        body = email_service.html_to_text(html)
    if not (body or "").strip():
        raise HTTPException(422, "The email body is empty")
    if not subject.strip():
        raise HTTPException(422, "An email needs a subject line")

    # Explicit uploads win; otherwise the template's stored attachments apply.
    attachments = email_service.clean_attachments(data.attachments)
    if not attachments and template is not None:
        attachments = email_service.load_attachments(getattr(template, "attachments", None))
    # A list send is bulk mail. A template that opted into unsubscribe headers is
    # treated the same way, because that is what the author asked for.
    bulk = data.list_id is not None or bool(
        template is not None and getattr(template, "include_unsubscribe", True)
    )
    account = await email_service.get_account(db, data.email_account_id)
    if account is None:
        account = await email_service.get_default_account(db)
    if account is None:
        raise HTTPException(
            400,
            "No email sender configured yet. Add a Brevo API key and From address on the "
            "Senders tab first.",
        )
    if data.schedule_at:
        scheduled_at = _parse_schedule_at(data.schedule_at)
        target_contact_id = data.contact_id
        if target_contact_id is None and data.email:
            existing = (
                await db.execute(
                    select(Contact).where(func.lower(Contact.email) == (data.email or "").lower())
                )
            ).scalars().first()
            target_contact_id = existing.id if existing else None
        scheduled = ScheduledMessage(
            contact_id=target_contact_id,
            # Kept on the scheduled row so the inline poller can attach them
            # hours later without the browser having to be open.
            attachments=email_service.dump_attachments(attachments),
            cc_addresses=", ".join(email_service.clean_addresses(data.cc)) or None,
            bcc_addresses=", ".join(email_service.clean_addresses(data.bcc)) or None,
            to_address=None if target_contact_id else (data.email or None),
            list_id=data.list_id,
            body=body,
            channel="email",
            subject=subject,
            html_body=html,
            email_account_id=account.id,
            schedule_at=scheduled_at,
            status="pending",
        )
        db.add(scheduled)
        await db.commit()
        await db.refresh(scheduled)
        return {
            "success": True,
            "scheduled": True,
            "scheduled_message_id": scheduled.id,
            "schedule_at": _iso(scheduled.schedule_at),
            "email_account_id": account.id,
        }

    targets: list[Contact] = []
    if data.contact_id:
        contact = (
            await db.execute(select(Contact).where(Contact.id == data.contact_id))
        ).scalar_one_or_none()
        if contact is None:
            raise HTTPException(404, "Contact not found")
        targets = [contact]
    elif data.email:
        address = email_service.normalize_email(data.email)
        if not address:
            raise HTTPException(422, "Enter a valid email address")
        contact = (
            await db.execute(select(Contact).where(func.lower(Contact.email) == address))
        ).scalars().first()
        if contact is None:
            contact = Contact(
                email=address,
                phone_number=f"email:{address}"[:20],
                source="email_send",
            )
            db.add(contact)
            await db.flush()
        targets = [contact]
    elif data.list_id:
        members = (
            await db.execute(
                select(Contact)
                .join(ContactListMember, ContactListMember.contact_id == Contact.id)
                .where(ContactListMember.list_id == data.list_id)
                .limit(500)
            )
        ).scalars().all()
        if not members:
            raise HTTPException(400, "That list has no contacts")
        targets = members
    else:
        raise HTTPException(422, "Choose a contact, an email address or a list")

    sent, skipped, failed, message_ids = 0, 0, 0, []
    for contact in targets:
        problem = await email_service.contact_email_problem(db, contact)
        if problem:
            skipped += 1
            continue
        message, result = await email_service.send_now(
            db, contact, subject=subject, text_body=body, html_body=html, account=account,
            attachments=attachments,
            bulk=bulk,
            cc=data.cc,
            bcc=data.bcc,
        )
        if message is None:
            skipped += 1
            continue
        message_ids.append(message.id)
        if result.get("success"):
            sent += 1
        else:
            failed += 1
    await db.commit()

    return {
        "success": sent > 0,
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
        "total": len(targets),
        "message_ids": message_ids,
        "email_account_id": account.id,
    }


@router.get("/history")
async def history(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=200),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    direction: Optional[str] = None,
    account_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Every email message (in and out), newest first."""
    query = select(Message).where(Message.channel == "email")
    if status_filter:
        query = query.where(Message.status == status_filter)
    if direction in ("incoming", "outgoing"):
        query = query.where(Message.direction == direction)
    if account_id:
        query = query.where(Message.email_account_id == account_id)
    if campaign_id:
        query = query.where(Message.campaign_id == campaign_id)
    if search:
        like = f"%{search.strip()}%"
        query = query.where(
            or_(Message.subject.ilike(like), Message.to_address.ilike(like),
                Message.from_address.ilike(like), Message.body.ilike(like))
        )

    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar() or 0
    rows = (
        await db.execute(
            query.order_by(Message.id.desc()).offset((page - 1) * per_page).limit(per_page)
        )
    ).scalars().all()

    accounts = await _accounts_by_id(db)
    contacts = {
        c.id: c
        for c in (
            await db.execute(select(Contact).where(Contact.id.in_([r.contact_id for r in rows] or [0])))
        ).scalars().all()
    }
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [
            _message_dict(m, contacts.get(m.contact_id), accounts.get(m.email_account_id))
            for m in rows
        ],
    }


@router.post("/messages/{message_id}/retry")
async def retry_message(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Re-queue a failed email and try Brevo again."""
    message = (
        await db.execute(select(Message).where(Message.id == message_id))
    ).scalar_one_or_none()
    if message is None or (message.channel or "sms") != "email":
        raise HTTPException(404, "Email message not found")
    if message.status not in ("failed", "retrying"):
        raise HTTPException(400, f"This message is '{message.status}' — only failed sends can be retried")

    message.status = "queued"
    message.retry_count = 0
    message.last_error = None
    message.failed_at = None
    await db.commit()

    from app.tasks.queue import try_enqueue
    from app.tasks.sms_tasks import send_sms

    if not try_enqueue(send_sms, message.id):
        # No broker (single-process deployment): settle it inline so the button
        # is never a no-op.
        from app.tasks.sms_tasks import _send_one

        await _send_one(message.id, final_on_failure=True)
        await db.refresh(message)
    return {"success": True, "status": message.status, "message": _message_dict(message)}


# ===========================================================================
# Inbox
# ===========================================================================


@router.get("/inbox/conversations")
async def list_conversations(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=30, ge=1, le=100),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Email threads, newest activity first."""
    query = select(Conversation).where(func.coalesce(Conversation.channel, "sms") == "email")
    if status_filter and status_filter != "all":
        query = query.where(Conversation.status == status_filter)
    if search:
        like = f"%{search.strip()}%"
        contact_ids = (
            select(Contact.id).where(
                or_(Contact.email.ilike(like), Contact.first_name.ilike(like),
                    Contact.last_name.ilike(like))
            )
        )
        query = query.where(
            or_(
                Conversation.contact_id.in_(contact_ids),
                Conversation.subject.ilike(like),
                Conversation.last_message_preview.ilike(like),
            )
        )

    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar() or 0
    conversations = (
        await db.execute(
            query.order_by(
                Conversation.last_message_at.desc().nullslast(), Conversation.id.desc()
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).scalars().all()

    contact_ids = [c.contact_id for c in conversations]
    contacts = {
        c.id: c
        for c in (
            await db.execute(select(Contact).where(Contact.id.in_(contact_ids or [0])))
        ).scalars().all()
    }
    accounts = await _accounts_by_id(db)

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [
            {
                "id": c.id,
                "contact_id": c.contact_id,
                "contact_name": (
                    f"{contacts[c.contact_id].first_name or ''} {contacts[c.contact_id].last_name or ''}".strip()
                    if c.contact_id in contacts
                    else None
                )
                or (contacts[c.contact_id].email if c.contact_id in contacts else None),
                "contact_email": contacts[c.contact_id].email if c.contact_id in contacts else None,
                "contact_phone": contacts[c.contact_id].phone_number if c.contact_id in contacts else None,
                "subject": c.subject,
                "preview": c.last_message_preview,
                "status": c.status,
                "unread_count": c.unread_count or 0,
                "message_count": c.message_count or 0,
                "last_message_at": _iso(c.last_message_at),
                "email_account_id": c.email_account_id,
                "email_account_name": (
                    accounts[c.email_account_id].name if c.email_account_id in accounts else None
                ),
                "campaign_id": c.campaign_id,
                "ads_campaign_id": c.ads_campaign_id,
            }
            for c in conversations
        ],
    }


@router.get("/inbox/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    conversation = (
        await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                func.coalesce(Conversation.channel, "sms") == "email",
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(404, "Email conversation not found")

    messages = (
        await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.asc(), Message.id.asc())
        )
    ).scalars().all()

    contact = (
        await db.execute(select(Contact).where(Contact.id == conversation.contact_id))
    ).scalar_one_or_none()
    accounts = await _accounts_by_id(db)
    custom_fields = {}
    tags: list[str] = []
    email_aliases: list[str] = []
    if contact is not None:
        try:
            parsed_fields = json.loads(contact.custom_fields or "{}")
            custom_fields = parsed_fields if isinstance(parsed_fields, dict) else {}
        except (TypeError, ValueError):
            custom_fields = {}
        tags = sorted({row.tag.name for row in (contact.tags or []) if row.tag and row.tag.name})
        email_aliases = await email_service.contact_email_aliases(db, contact.id)

    return {
        "conversation": {
            "id": conversation.id,
            "contact_id": conversation.contact_id,
            "subject": conversation.subject,
            "status": conversation.status,
            "unread_count": conversation.unread_count or 0,
            "email_account_id": conversation.email_account_id,
            "email_account_name": (
                accounts[conversation.email_account_id].name
                if conversation.email_account_id in accounts
                else None
            ),
        },
        "contact": {
            "id": contact.id,
            "name": (
                f"{contact.first_name or ''} {contact.last_name or ''}".strip() or contact.email
            ),
            "email": contact.email,
            "email_aliases": email_aliases,
            "phone_number": contact.phone_number,
            "business_name": contact.business_name,
            "city": contact.city,
            "state": contact.state,
            "country": contact.country,
            "website": contact.website,
            "industry": contact.industry,
            "source": contact.source,
            "lead_status": contact.lead_status,
            "notes": contact.notes,
            "custom_fields": custom_fields,
            "tags": tags,
            "is_email_opted_out": bool(contact.is_email_opted_out),
            "is_email_undeliverable": bool(contact.is_email_undeliverable),
            "email_status": contact.email_status,
        } if contact else None,
        "messages": [
            _message_dict(m, contact, accounts.get(m.email_account_id)) for m in messages
        ],
    }


@router.post("/inbox/conversations/{conversation_id}/reply")
async def reply(
    conversation_id: int,
    data: EmailReplyIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Reply inside an email thread, from the same sender the thread started on."""
    conversation = (
        await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                func.coalesce(Conversation.channel, "sms") == "email",
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(404, "Email conversation not found")

    contact = (
        await db.execute(select(Contact).where(Contact.id == conversation.contact_id))
    ).scalar_one_or_none()
    if contact is None:
        raise HTTPException(404, "Contact not found")

    body, html, subject = data.body or "", data.html_body, data.subject
    if data.template_id:
        template = (
            await db.execute(select(Template).where(Template.id == data.template_id))
        ).scalar_one_or_none()
        if template is None:
            raise HTTPException(404, "Template not found")
        body = body or template.body or ""
        html = html or template.html_body
        subject = subject or template.subject
    if not (body or "").strip() and (html or "").strip():
        # A reply composed as pure HTML (images, links, formatting) still needs
        # a text part, so derive one rather than refusing to send.
        body = email_service.html_to_text(html)
    if not (body or "").strip():
        raise HTTPException(422, "Write a reply first")
    if not (subject or "").strip():
        subject = f"Re: {conversation.subject or ''}".strip()

    account = await email_service.get_account(db, data.email_account_id)
    if account is None:
        account = await email_service.get_account(db, conversation.email_account_id)
    if account is None:
        account = await email_service.get_default_account(db)

    latest_inbound = (
        await db.execute(
            select(Message.from_address)
            .where(
                Message.conversation_id == conversation.id,
                Message.channel == "email",
                Message.direction == "incoming",
                Message.from_address.isnot(None),
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    reply_address = email_service.normalize_email(latest_inbound) or email_service.normalize_email(contact.email)

    message, result = await email_service.send_now(
        db, contact, subject=subject, text_body=body, html_body=html, account=account,
        attachments=data.attachments,
        cc=data.cc,
        bcc=data.bcc,
        recipient_address=reply_address,
    )
    if message is None:
        raise HTTPException(400, "This contact cannot be emailed (opted out or suppressed)")
    conversation.status = "active"
    conversation.unread_count = 0
    await db.commit()
    return {
        "success": bool(result.get("success")),
        "error": result.get("error"),
        "message": _message_dict(message, contact, account),
    }


@router.post("/inbox/conversations/{conversation_id}/read")
async def mark_read(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    conversation = (
        await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                func.coalesce(Conversation.channel, "sms") == "email",
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(404, "Email conversation not found")
    conversation.unread_count = 0
    if conversation.status == "unread":
        conversation.status = "active"
    await db.commit()
    return {"success": True}


@router.post("/inbox/conversations/{conversation_id}/status")
async def set_status(
    conversation_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    conversation = (
        await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                func.coalesce(Conversation.channel, "sms") == "email",
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(404, "Email conversation not found")
    new_status = str(payload.get("status") or "").strip().lower()
    allowed = {"active", "unread", "interested", "not_interested", "closed"}
    if new_status not in allowed:
        raise HTTPException(422, f"status must be one of {sorted(allowed)}")
    conversation.status = new_status
    if new_status != "unread":
        conversation.unread_count = 0
    await db.commit()
    return {"success": True, "status": conversation.status}


@router.get("/inbox/unread-count")
async def unread_count(
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    total = (
        await db.execute(
            select(func.coalesce(func.sum(Conversation.unread_count), 0)).where(
                func.coalesce(Conversation.channel, "sms") == "email"
            )
        )
    ).scalar() or 0
    threads = (
        await db.execute(
            select(func.count()).select_from(Conversation).where(
                func.coalesce(Conversation.channel, "sms") == "email",
                Conversation.unread_count > 0,
            )
        )
    ).scalar() or 0
    return {"unread": int(total), "threads": int(threads)}


# ===========================================================================
# Suppression
# ===========================================================================


@router.get("/suppression")
async def list_suppression(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    query = select(EmailSuppression)
    if search:
        query = query.where(EmailSuppression.email_address.ilike(f"%{search.strip()}%"))
    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar() or 0
    rows = (
        await db.execute(
            query.order_by(EmailSuppression.id.desc()).offset((page - 1) * per_page).limit(per_page)
        )
    ).scalars().all()
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [
            {
                "id": s.id,
                "email_address": s.email_address,
                "contact_id": s.contact_id,
                "reason": s.reason,
                "source": s.source,
                "hard_bounce": bool(s.hard_bounce),
                "opt_out_keyword": s.opt_out_keyword,
                "created_at": _iso(s.created_at),
            }
            for s in rows
        ],
    }


@router.post("/suppression", status_code=201)
async def add_suppression(
    data: EmailSuppressionIn,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    entry = await email_service.suppress_email(
        db, data.email_address, reason=data.reason, source=data.source or "manual"
    )
    if entry is None:
        raise HTTPException(422, "Enter a valid email address")
    await db.commit()
    await db.refresh(entry)
    return {
        "id": entry.id,
        "email_address": entry.email_address,
        "reason": entry.reason,
        "source": entry.source,
        "hard_bounce": bool(entry.hard_bounce),
        "created_at": _iso(entry.created_at),
    }


@router.delete("/suppression/{entry_id}", status_code=204)
async def remove_suppression(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    entry = (
        await db.execute(select(EmailSuppression).where(EmailSuppression.id == entry_id))
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(404, "Suppression entry not found")
    await email_service.unsuppress_email(db, entry)
    await db.commit()


# ===========================================================================
# Events
# ===========================================================================


@router.get("/events")
async def events(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    event_type: Optional[str] = None,
    account_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """The raw Brevo event log — what actually happened to each email."""
    query = select(EmailEvent)
    if event_type:
        query = query.where(EmailEvent.event_type == event_type)
    if account_id:
        query = query.where(EmailEvent.account_id == account_id)
    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar() or 0
    rows = (
        await db.execute(
            query.order_by(EmailEvent.id.desc()).offset((page - 1) * per_page).limit(per_page)
        )
    ).scalars().all()
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "account_id": e.account_id,
                "message_id": e.message_id,
                "contact_id": e.contact_id,
                "campaign_id": e.campaign_id,
                "email_address": e.email_address,
                "subject": e.subject,
                "link": e.link,
                "detail": e.detail,
                "created_at": _iso(e.created_at),
            }
            for e in rows
        ],
    }


# ===========================================================================
# Unsubscribe (public) — the List-Unsubscribe target
# ===========================================================================


@router.get("/unsubscribe")
async def unsubscribe_page(
    e: str = Query(..., description="email address"),
    t: str = Query(..., description="signature"),
    db: AsyncSession = Depends(get_db),
):
    """Public one-click unsubscribe target used by the List-Unsubscribe header.

    Deliberately unauthenticated: a recipient clicking the link in Gmail is not
    logged in here, and the HMAC signature is what proves the request is real.
    """
    from fastapi.responses import HTMLResponse

    if not email_service.unsubscribe_token_valid(e, t):
        return HTMLResponse(
            "<h1>Link expired</h1><p>This unsubscribe link is not valid. "
            "Reply to the email with the word UNSUBSCRIBE instead.</p>",
            status_code=400,
        )
    await email_service.unsubscribe_contact(
        db, contact_id=None, address=e, reason="One-click unsubscribe", source="link"
    )
    await db.commit()
    return HTMLResponse(
        "<h1>You are unsubscribed</h1>"
        f"<p>{e} will not receive further email from us.</p>",
    )


@router.post("/unsubscribe")
async def unsubscribe_post(
    e: str = Query(...),
    t: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """``List-Unsubscribe=One-Click`` POST target (RFC 8058)."""
    if not email_service.unsubscribe_token_valid(e, t):
        raise HTTPException(400, "Invalid unsubscribe link")
    await email_service.unsubscribe_contact(
        db, contact_id=None, address=e, reason="One-click unsubscribe", source="link"
    )
    await db.commit()
    return {"success": True, "email_address": email_service.normalize_email(e)}


# ===========================================================================
# Deliverability
# ===========================================================================


@router.get("/accounts/{account_id}/deliverability")
async def deliverability(
    account_id: int,
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Everything that decides whether mail lands in the inbox or in spam.

    Domain authentication (the part only Brevo can answer), the app-side
    standing of this sender, and the recipient-side signals that actually move
    deliverability: bounce rate, spam complaints, unsubscribe rate and
    engagement.
    """
    from app.providers.brevo import list_domains

    account = await email_service.get_account(db, account_id)
    if account is None:
        raise HTTPException(404, "Email account not found")

    api_key = decrypt_value(account.api_key_encrypted or "")
    domains: dict = {"success": False, "error": "No API key on this account", "domains": []}
    if api_key:
        domains = await list_domains(api_key)

    since = datetime.now(timezone.utc) - timedelta(days=days)
    sent = (
        await db.execute(
            select(func.count()).select_from(Message).where(
                Message.channel == "email",
                Message.direction == "outgoing",
                Message.email_account_id == account.id,
                Message.created_at >= since,
            )
        )
    ).scalar() or 0
    bounced = (
        await db.execute(
            select(func.count()).select_from(Message).where(
                Message.channel == "email",
                Message.email_account_id == account.id,
                Message.bounced_at.isnot(None),
                Message.created_at >= since,
            )
        )
    ).scalar() or 0
    opened = (
        await db.execute(
            select(func.count()).select_from(Message).where(
                Message.channel == "email",
                Message.direction == "outgoing",
                Message.email_account_id == account.id,
                Message.open_count > 0,
                Message.created_at >= since,
            )
        )
    ).scalar() or 0
    clicked = (
        await db.execute(
            select(func.count()).select_from(Message).where(
                Message.channel == "email",
                Message.direction == "outgoing",
                Message.email_account_id == account.id,
                Message.click_count > 0,
                Message.created_at >= since,
            )
        )
    ).scalar() or 0
    unsubscribed = (
        await db.execute(
            select(func.count()).select_from(EmailSuppression).where(
                EmailSuppression.created_at >= since,
            )
        )
    ).scalar() or 0

    def rate(part: int, whole: int) -> float:
        return round(part / whole * 100, 2) if whole else 0.0

    from_domain = (account.from_email or "").split("@")[-1].lower()
    domain_row = next(
        (d for d in (domains.get("domains") or []) if (d.get("domain") or "").lower() == from_domain),
        None,
    )

    checks = [
        {
            "key": "domain_authenticated",
            "label": "Sending domain authenticated (SPF/DKIM)",
            "ok": bool(domain_row and (domain_row.get("authenticated") or domain_row.get("verified"))),
            "detail": (
                "Brevo has verified this domain."
                if domain_row and (domain_row.get("authenticated") or domain_row.get("verified"))
                else f"Authenticate {from_domain or 'your domain'} in Brevo (Senders & IP → Domains). "
                     "Unauthenticated domains land in spam."
            ),
        },
        {
            "key": "sender_default",
            "label": "A default sender is set",
            "ok": bool(account.is_default),
            "detail": "Campaigns without an explicit sender use this one."
            if account.is_default
            else "Another sender is the default; this one is only used when picked.",
        },
        {
            "key": "reply_to",
            "label": "A reply-to address is configured",
            "ok": bool(account.reply_to),
            "detail": "Replies come back to the address you chose."
            if account.reply_to
            else "Set a reply-to so replies reach a monitored mailbox (and unsubscribes "
                 "have somewhere to go).",
        },
        {
            "key": "tracking",
            "label": "Open/click tracking enabled",
            "ok": bool(account.track_opens or account.track_clicks),
            "detail": "Engagement is measured, which is what keeps a sender healthy."
            if (account.track_opens or account.track_clicks)
            else "Without tracking there is no evidence of engagement.",
        },
        {
            "key": "bounce_rate",
            "label": "Bounce rate under 2%",
            "ok": rate(bounced, sent) < 2.0,
            "detail": f"{rate(bounced, sent)}% of {sent} sends bounced in the last {days} days.",
        },
        {
            "key": "public_url",
            "label": "Public web address configured (one-click unsubscribe)",
            "ok": bool(public_base_url()),
            "detail": (
                "List-Unsubscribe carries a real https link, which Gmail/Yahoo rate highly."
                if public_base_url()
                else "Set PUBLIC_BASE_URL in the server environment. Until then bulk mail "
                     "only offers the mailto unsubscribe and no clickable link can be built."
            ),
        },
        {
            "key": "engagement",
            "label": "Recipients are engaging (opens)",
            "ok": rate(opened, sent) >= 10.0 or sent == 0,
            "detail": f"{rate(opened, sent)}% opened, {rate(clicked, sent)}% clicked.",
        },
    ]

    return {
        "account": email_service.serialize_account(account),
        "days": days,
        "domains": domains.get("domains") or [],
        "domains_error": None if domains.get("success") else domains.get("error"),
        "metrics": {
            "sent": sent,
            "bounced": bounced,
            "opened": opened,
            "clicked": clicked,
            "unsubscribed": unsubscribed,
            "bounce_rate": rate(bounced, sent),
            "open_rate": rate(opened, sent),
            "click_rate": rate(clicked, sent),
        },
        "checks": checks,
        "score": round(
            100 * sum(1 for c in checks if c["ok"]) / max(len(checks), 1)
        ),
    }


# ===========================================================================
# Engagement — the "did they open it, what did they click" view
# ===========================================================================


@router.get("/messages/{message_id}/events")
async def message_events(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Everything Brevo reported about one email, newest first.

    This is the per-message equivalent of Brevo's recipient activity: the
    delivery, every open, every link that was clicked (with the URL), bounces
    and complaints.
    """
    message = (
        await db.execute(select(Message).where(Message.id == message_id))
    ).scalar_one_or_none()
    if message is None:
        raise HTTPException(404, "Message not found")

    rows = (
        await db.execute(
            select(EmailEvent)
            .where(EmailEvent.message_id == message_id)
            .order_by(EmailEvent.id.desc())
            .limit(200)
        )
    ).scalars().all()

    return {
        "message_id": message_id,
        "summary": {
            "status": message.status,
            "sent_at": _iso(message.sent_at),
            "delivered_at": _iso(message.delivered_at),
            "first_opened_at": _iso(message.opened_at),
            "first_clicked_at": _iso(message.clicked_at),
            "bounced_at": _iso(message.bounced_at),
            "open_count": message.open_count or 0,
            "click_count": message.click_count or 0,
            # Opens/clicks are counted by Brevo; a mail-client privacy proxy can
            # inflate opens, so the UI says so rather than over-claiming.
            "unique_links_clicked": len({e.link for e in rows if e.link}),
        },
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "link": e.link,
                "detail": e.detail,
                "created_at": _iso(e.created_at),
            }
            for e in rows
        ],
    }


@router.get("/contacts/{contact_id}/engagement")
async def contact_engagement(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """One contact's email history in the shape Brevo shows it.

    Every message, how it ended, opens/clicks per message, and the links that
    were actually clicked — so a prospect who is reading but not replying is
    visible instead of invisible.
    """
    contact = (
        await db.execute(select(Contact).where(Contact.id == contact_id))
    ).scalar_one_or_none()
    if contact is None:
        raise HTTPException(404, "Contact not found")

    messages = (
        await db.execute(
            select(Message)
            .where(Message.channel == "email", Message.contact_id == contact_id)
            .order_by(Message.id.desc())
            .limit(50)
        )
    ).scalars().all()

    sent = sum(1 for m in messages if m.direction == "outgoing")
    opened = sum(1 for m in messages if (m.open_count or 0) > 0)
    clicked = sum(1 for m in messages if (m.click_count or 0) > 0)
    bounced = sum(1 for m in messages if m.bounced_at is not None)

    links = (
        await db.execute(
            select(EmailEvent.link, func.count())
            .where(
                EmailEvent.contact_id == contact_id,
                EmailEvent.event_type == "clicked",
                EmailEvent.link.isnot(None),
            )
            .group_by(EmailEvent.link)
            .order_by(func.count().desc())
            .limit(20)
        )
    ).all()

    events = (
        await db.execute(
            select(EmailEvent)
            .where(EmailEvent.contact_id == contact_id)
            .order_by(EmailEvent.id.desc())
            .limit(40)
        )
    ).scalars().all()

    def rate(part: int, whole: int) -> float:
        return round(part / whole * 100, 1) if whole else 0.0

    return {
        "contact_id": contact_id,
        "email": contact.email,
        "is_email_opted_out": bool(contact.is_email_opted_out),
        "is_email_undeliverable": bool(contact.is_email_undeliverable),
        "email_status": contact.email_status,
        "emails_sent": contact.emails_sent or sent,
        "last_emailed_at": _iso(contact.last_emailed_at),
        "totals": {
            "sent": sent,
            "opened": opened,
            "clicked": clicked,
            "bounced": bounced,
            "open_rate": rate(opened, sent),
            "click_rate": rate(clicked, sent),
        },
        "links": [{"url": url, "clicks": clicks} for url, clicks in links],
        "messages": [
            {
                "id": m.id,
                "subject": m.subject,
                "direction": m.direction,
                "status": m.status,
                "open_count": m.open_count or 0,
                "click_count": m.click_count or 0,
                "created_at": _iso(m.created_at),
                "campaign_id": m.campaign_id,
            }
            for m in messages
        ],
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "link": e.link,
                "subject": e.subject,
                "created_at": _iso(e.created_at),
            }
            for e in events
        ],
    }
