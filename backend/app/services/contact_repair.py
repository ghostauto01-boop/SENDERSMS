"""One-time repairs of contact rows written by older code."""

from __future__ import annotations

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact

#: What older code stored as a "phone number" for a contact that only had an email address
#: (the address, prefixed and cut to 20 characters), back when the column was NOT NULL.
PLACEHOLDER_PREFIX = "email:"


async def repair_placeholder_phones(db: AsyncSession) -> int:
    """Clear the invented ``email:<address>`` phone numbers; returns how many rows changed.

    Only rows that still have an email address to be reached by are touched (a row with neither
    is left exactly as it is -- there is nothing better to put there). Real numbers are never
    changed, and running it again changes nothing.
    """
    result = await db.execute(
        update(Contact)
        .where(
            Contact.phone_number.like(f"{PLACEHOLDER_PREFIX}%"),
            Contact.email.isnot(None),
            func.trim(Contact.email) != "",
        )
        .values(phone_number=None)
        .execution_options(synchronize_session=False)
    )
    await db.flush()
    return int(result.rowcount or 0)
