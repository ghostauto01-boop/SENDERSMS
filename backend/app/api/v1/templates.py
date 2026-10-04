"""
Templates API routes — one library, two channels.

``channel=sms`` (the default, so every existing caller is unaffected) lists the
text-message library; ``channel=email`` lists the email library, where each row
also carries a subject, an HTML body and an optional sender override.

Email templates deliberately live in the *same* table as SMS templates: the
campaign builder, the ads composer, sequences and follow-ups all already know
how to load "a template", so keeping one table means the email channel gets all
of that behaviour for free instead of re-implementing it.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.template import Template
from app.models.user import User
from app.schemas.template import TemplateCreate, TemplateUpdate, TemplateOut
from app.security.auth import get_current_user
from app.utils.phone import count_sms_segments
from app.utils.templating import render_template

router = APIRouter()

VALID_CHANNELS = ("sms", "email")


def _clean_channel(raw: Optional[str]) -> Optional[str]:
    if raw in (None, "", "all"):
        return None
    if raw not in VALID_CHANNELS:
        raise HTTPException(422, f"channel must be one of {list(VALID_CHANNELS)}")
    return raw


def _store_attachments(items) -> str | None:
    """Validate + serialise attachments for storage (size caps live in the service)."""
    from app.services.email_service import clean_attachments, dump_attachments

    return dump_attachments(clean_attachments(items))


def _template_out(template) -> dict:
    """Template row → API shape, with attachment payloads stripped.

    ``attachments`` is a JSON *string* on the row (a list of dicts on the API),
    so the row is converted column-by-column before validation — handing the ORM
    object straight to TemplateOut tries to validate that string as a list and
    raises a 500 the moment a template has a file attached.
    """
    from app.services.email_service import attachment_summary

    payload = {c.name: getattr(template, c.name) for c in template.__table__.columns}
    # Drop the raw column first — it is a JSON string, and TemplateOut.attachments
    # is a typed list. It is replaced with the safe summary below.
    payload.pop("attachments", None)
    data = TemplateOut.model_validate(payload).model_dump()
    data["attachments"] = attachment_summary(getattr(template, "attachments", None))
    return data


def _email_metrics(subject: str | None, body: str, html: str | None) -> tuple[int, int]:
    """Character/segment counts for the email editor.

    For email the meaningful numbers are the subject length (inboxes truncate
    around 60 characters) and the body length — but the response keeps the same
    two field names the SMS UI already understands.
    """
    text = body or ""
    length = len(text)
    segments = max(1, (len(subject or "") + length) // 1000 + 1) if (subject or length) else 1
    return length, segments


@router.get("/")
async def list_templates(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=200),
    search: Optional[str] = None,
    category: Optional[str] = None,
    channel: Optional[str] = Query(default=None, description="sms | email | all"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List templates for one channel (defaults to SMS)."""
    query = select(Template)

    wanted = _clean_channel(channel)
    if wanted:
        query = query.where(Template.channel == wanted)
    if search:
        query = query.where(Template.name.ilike(f"%{search}%"))
    if category:
        query = query.where(Template.category == category)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Template.updated_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    templates = result.scalars().all()

    return {"total": total, "items": [_template_out(t) for t in templates]}


@router.get("/categories")
async def list_categories(
    channel: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Distinct categories in one channel's library (drives the filter chip)."""
    query = select(Template.category).distinct().where(Template.category.isnot(None))
    wanted = _clean_channel(channel)
    if wanted:
        query = query.where(Template.channel == wanted)
    values = [c for c in (await db.execute(query)).scalars().all() if c]
    return {"items": sorted(values)}


@router.post("/preview")
async def preview_template(
    body: str = Query(""),
    subject: Optional[str] = Query(None),
    html_body: Optional[str] = Query(None),
    channel: str = Query("sms"),
    first_name: str = "John",
    last_name: str = "Doe",
    business_name: str = "Acme Ltd",
    phone_number: str = "08012345678",
    email: str = "john.doe@example.com",
    city: str = "Lagos",
    state: str = "Lagos",
    website: str = "https://example.com",
    industry: str = "Technology",
):
    """Preview a template with sample data.

    Registered *before* ``/{template_id}`` in the router, so "preview" is never
    parsed as a template id.
    """
    sample = dict(
        first_name=first_name,
        last_name=last_name,
        business_name=business_name,
        phone_number=phone_number,
        email=email,
        city=city,
        state=state,
        website=website,
        industry=industry,
    )
    # Same renderer the senders use, so the preview is an honest picture of
    # the outgoing message rather than a second implementation that drifts.
    preview = render_template(body, None, **sample)
    preview_subject = render_template(subject, None, **sample) if subject else None
    preview_html = render_template(html_body, None, **sample) if html_body else None

    char_count, segment_count = count_sms_segments(preview)
    return {
        "preview": preview,
        "subject": preview_subject,
        "html": preview_html,
        "char_count": char_count,
        "segment_count": segment_count,
        "channel": "email" if channel == "email" else "sms",
    }


@router.get("/{template_id}", response_model=TemplateOut)
async def get_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a template."""
    result = await db.execute(select(Template).where(Template.id == template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return _template_out(template)


@router.post("/", status_code=201, response_model=TemplateOut)
async def create_template(
    data: TemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a template (SMS or email)."""
    channel = data.channel if data.channel in VALID_CHANNELS else "sms"
    body = data.body or ""
    if channel == "email" and not (body or data.html_body):
        raise HTTPException(422, "An email template needs a body (or an HTML body)")
    char_count, segment_count = (
        _email_metrics(data.subject, body, data.html_body)
        if channel == "email"
        else count_sms_segments(body)
    )

    template = Template(
        name=data.name,
        category=data.category,
        channel=channel,
        body=body,
        subject=data.subject if channel == "email" else None,
        html_body=data.html_body if channel == "email" else None,
        preheader=data.preheader if channel == "email" else None,
        email_account_id=data.email_account_id if channel == "email" else None,
        # Attachments belong to email templates; SMS templates simply ignore them.
        attachments=(
            _store_attachments(data.attachments) if channel == "email" else None
        ),
        include_unsubscribe=data.include_unsubscribe if channel == "email" else True,
        char_count=char_count,
        segment_count=segment_count,
    )
    db.add(template)
    await db.flush()
    await db.refresh(template)
    return _template_out(template)


@router.put("/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: int,
    data: TemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a template."""
    result = await db.execute(select(Template).where(Template.id == template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    update_data = data.model_dump(exclude_unset=True)
    if "attachments" in update_data:
        update_data["attachments"] = _store_attachments(update_data["attachments"])
    if "body" in update_data or "subject" in update_data or "html_body" in update_data:
        if template.channel == "email":
            char_count, segment_count = _email_metrics(
                update_data.get("subject", template.subject),
                update_data.get("body", template.body),
                update_data.get("html_body", template.html_body),
            )
        else:
            char_count, segment_count = count_sms_segments(update_data.get("body", template.body))
        update_data["char_count"] = char_count
        update_data["segment_count"] = segment_count

    for key, value in update_data.items():
        setattr(template, key, value)
    await db.flush()
    await db.refresh(template)
    return _template_out(template)


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a template."""
    result = await db.execute(select(Template).where(Template.id == template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    await db.delete(template)
    await db.flush()


@router.post("/{template_id}/duplicate", response_model=TemplateOut)
async def duplicate_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Duplicate a template."""
    result = await db.execute(select(Template).where(Template.id == template_id))
    original = result.scalar_one_or_none()
    if not original:
        raise HTTPException(status_code=404, detail="Template not found")

    new_template = Template(
        name=f"{original.name} (Copy)",
        category=original.category,
        channel=original.channel or "sms",
        body=original.body,
        subject=original.subject,
        html_body=original.html_body,
        preheader=original.preheader,
        email_account_id=original.email_account_id,
        attachments=original.attachments,
        include_unsubscribe=original.include_unsubscribe,
        char_count=original.char_count,
        segment_count=original.segment_count,
    )
    db.add(new_template)
    await db.flush()
    await db.refresh(new_template)
    return _template_out(new_template)


@router.post("/{template_id}/to-email")
async def convert_to_email(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Copy an SMS template into the email library (subject left blank)."""
    result = await db.execute(select(Template).where(Template.id == template_id))
    original = result.scalar_one_or_none()
    if not original:
        raise HTTPException(status_code=404, detail="Template not found")
    if original.channel == "email":
        raise HTTPException(400, "That template is already an email template")

    char_count, segment_count = _email_metrics(None, original.body, None)
    clone = Template(
        name=f"{original.name} (Email)",
        category=original.category,
        channel="email",
        body=original.body,
        subject=original.name[:120],
        char_count=char_count,
        segment_count=segment_count,
    )
    db.add(clone)
    await db.flush()
    await db.refresh(clone)
    return _template_out(clone)
