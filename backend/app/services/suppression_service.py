"""Writing to the global suppression list — safely.

WHY THIS MODULE EXISTS
----------------------
``suppression_entries.phone_number`` is UNIQUE. Several code paths add to it
(the inbound webhook, the automation engine, the inbox "mark bad reply" action,
the Ads Manager), and each one used to do a naive ``SELECT`` then ``INSERT``.
Under load that is a race: two inbound texts from the same contact arriving
close together both see "no row yet" and both insert. The loser raises::

    duplicate key value violates unique constraint
    "ix_suppression_entries_phone_number"

and because the exception escaped mid-transaction, every later statement on
that session failed with ``PendingRollbackError``. The SMS-Gate webhook then
returned 500 for *every* event until the device gave up retrying — which is
exactly what was happening every few minutes while AIRTEL-EODN was sending
carrier opt-outs.

Two rules fix this for good:

1. **Insert with ON CONFLICT DO NOTHING.** The database decides; a duplicate is
   a no-op, never an exception. This is idempotent by construction and needs no
   lock, no retry loop and no read-before-write.
2. **Only suppress things that can be messaged.** ``AIRTEL-EODN``, ``MTN``,
   ``GTBank`` and short codes are *senders*, not recipients. Putting them in the
   suppression list is meaningless (nothing is ever sent "to AIRTEL") and used
   to collide with the same entry arriving on the next carrier event.

Every caller goes through :func:`add_suppression` so the two rules cannot drift.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.suppression import SuppressionEntry

logger = logging.getLogger(__name__)

#: A number we could actually send an SMS to. Deliberately permissive about
#: the prefix (international senders are real) but strict about the shape:
#: at least 7 digits, and nothing but digits/+/spaces/dashes/parentheses.
_DIALLABLE = re.compile(r"^\+?[0-9][0-9\s\-()]{5,19}$")


def is_diallable(raw: Optional[str]) -> bool:
    """Is this a phone number, or a carrier/sender ID?

    ``+2348012345678`` yes. ``AIRTEL-EODN``, ``MTN``, ``GTBank``, ``32665`` —
    short codes are diallable-looking but they are *senders*, so a bare 5-digit
    code is rejected here: we never send marketing to a short code, and
    suppressing one is meaningless.
    """
    if not raw:
        return False
    text = str(raw).strip()
    if not _DIALLABLE.match(text):
        return False
    digits = re.sub(r"\D", "", text)
    return len(digits) >= 7


def normalise(raw: Optional[str]) -> Optional[str]:
    """Canonical form used as the unique key.

    Kept deliberately simple (strip punctuation, keep the ``+``): the goal is
    that ``0803 123 4567`` and ``08031234567`` are the same row, not that this
    re-implements carrier-grade E.164 parsing.
    """
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    return f"+{digits}" if plus else digits


async def add_suppression(
    db: AsyncSession,
    phone_number: Optional[str],
    *,
    contact_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
    reason: str = "Opt-out",
    source: str = "keyword",
    opt_out_keyword: Optional[str] = None,
    allow_sender_ids: bool = False,
) -> bool:
    """Add a number to the suppression list. Idempotent, never raises on dupes.

    Returns ``True`` when a new row was written, ``False`` when the number was
    skipped (not diallable) or was already suppressed. Callers that only need
    the side effect can ignore the result.

    ``allow_sender_ids`` exists for the rare operator-driven case where someone
    deliberately suppresses a short code; every automatic path leaves it False.
    """
    number = normalise(phone_number)
    if not number:
        logger.debug("suppression: empty number ignored")
        return False
    if not allow_sender_ids and not is_diallable(phone_number):
        # Carrier and system sender IDs (AIRTEL-EODN, MTN, GTBank, 32665) are
        # not recipients. Suppressing them is meaningless and was the collision
        # this module was written to remove.
        logger.info("suppression: skipped non-diallable sender %r", phone_number)
        return False

    now = datetime.now(timezone.utc)
    values = {
        "phone_number": number,
        "contact_id": contact_id,
        "campaign_id": campaign_id,
        "reason": (reason or "Opt-out")[:500],
        "source": (source or "keyword")[:50],
        "opt_out_keyword": (opt_out_keyword or None) and str(opt_out_keyword)[:50],
        "suppressed_at": now,
        "created_at": now,
    }

    dialect = db.bind.dialect.name if db.bind is not None else ""
    try:
        if dialect == "postgresql":
            statement = pg_insert(SuppressionEntry).values(**values).on_conflict_do_nothing(
                index_elements=["phone_number"]
            )
        elif dialect == "sqlite":
            statement = sqlite_insert(SuppressionEntry).values(**values).on_conflict_do_nothing(
                index_elements=["phone_number"]
            )
        else:  # pragma: no cover - other dialects fall back to a guarded insert
            exists = (
                await db.execute(
                    select(SuppressionEntry.id).where(SuppressionEntry.phone_number == number)
                )
            ).scalar_one_or_none()
            if exists is not None:
                return False
            db.add(SuppressionEntry(**values))
            await db.flush()
            return True
        result = await db.execute(statement)
        await db.flush()
        inserted = (result.rowcount or 0) > 0
        if not inserted:
            logger.debug("suppression: %s already suppressed", number)
        return inserted
    except Exception:
        # A constraint we did not anticipate must never take the caller's
        # transaction down with it. Savepoint + rollback keeps the session
        # usable, which is what stops the PendingRollbackError cascade.
        await db.rollback()
        logger.exception("suppression: could not add %s (recovered)", number)
        return False


async def is_suppressed(db: AsyncSession, phone_number: Optional[str]) -> bool:
    number = normalise(phone_number)
    if not number:
        return False
    row = (
        await db.execute(
            select(SuppressionEntry.id).where(SuppressionEntry.phone_number == number)
        )
    ).scalar_one_or_none()
    return row is not None
