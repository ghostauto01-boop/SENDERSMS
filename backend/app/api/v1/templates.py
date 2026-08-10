"""
Templates API routes.
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

router = APIRouter()


@router.get("/")
async def list_templates(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    search: Optional[str] = None,
    category: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all templates."""
    query = select(Template)

    if search:
        query = query.where(Template.name.ilike(f"%{search}%"))
    if category:
        query = query.where(Template.category == category)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Template.updated_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    templates = result.scalars().all()

    return {"total": total, "items": [TemplateOut.model_validate(t) for t in templates]}


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
    return template


@router.post("/", status_code=201, response_model=TemplateOut)
async def create_template(
    data: TemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a template."""
    char_count, segment_count = count_sms_segments(data.body)

    template = Template(
        name=data.name,
        category=data.category,
        body=data.body,
        char_count=char_count,
        segment_count=segment_count,
    )
    db.add(template)
    await db.flush()
    await db.refresh(template)
    return template


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
    if "body" in update_data:
        char_count, segment_count = count_sms_segments(update_data["body"])
        update_data["char_count"] = char_count
        update_data["segment_count"] = segment_count

    for key, value in update_data.items():
        setattr(template, key, value)
    await db.flush()
    await db.refresh(template)
    return template


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
        body=original.body,
        char_count=original.char_count,
        segment_count=original.segment_count,
    )
    db.add(new_template)
    await db.flush()
    await db.refresh(new_template)
    return new_template


@router.post("/preview")
async def preview_template(
    body: str = Query(...),
    first_name: str = "John",
    last_name: str = "Doe",
    business_name: str = "Acme Ltd",
    phone_number: str = "08012345678",
    city: str = "Lagos",
    state: str = "Lagos",
    website: str = "https://example.com",
    industry: str = "Technology",
):
    """Preview a template with sample data."""
    preview = body
    preview = preview.replace("{{first_name}}", first_name)
    preview = preview.replace("{{last_name}}", last_name)
    preview = preview.replace("{{business_name}}", business_name)
    preview = preview.replace("{{phone_number}}", phone_number)
    preview = preview.replace("{{city}}", city)
    preview = preview.replace("{{state}}", state)
    preview = preview.replace("{{website}}", website)
    preview = preview.replace("{{industry}}", industry)

    char_count, segment_count = count_sms_segments(preview)

    return {
        "preview": preview,
        "char_count": char_count,
        "segment_count": segment_count,
    }
