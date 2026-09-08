"""Shared template lookups.

``Template.is_active`` is an Integer-backed boolean (0/1) for historical
reasons. Every active-template lookup MUST go through
:func:`get_active_template`, which compares against ``1`` — spelling it
``== True`` renders ``WHERE is_active = true``, and PostgreSQL rejects that
against an integer column (``operator does not exist: integer = boolean``)
while SQLite silently accepts it. That divergence is exactly what broke the
inbox template preview in production while every SQLite test stayed green.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.template import Template


async def get_active_template(db: AsyncSession, template_id: int | None) -> Template | None:
    """Fetch an active template by id, or ``None`` (missing/inactive)."""
    if template_id is None:
        return None
    return (
        await db.execute(
            select(Template).where(
                Template.id == template_id,
                # Integer column: compare with 1, never `== True` (see above).
                Template.is_active == 1,
            )
        )
    ).scalar_one_or_none()
