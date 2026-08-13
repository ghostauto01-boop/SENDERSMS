"""Durable key/value runtime state backed by the system_settings table.

Runtime state used to live in files next to the source (.sim_number,
.last_poll, .webhook_done). On Render and in containers the filesystem is
ephemeral, so every redeploy silently reset the SIM selection and re-ran
webhook registration; with more than one instance the files also disagreed.
Storing this in the database makes it survive restarts and stay consistent
across workers.
"""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system import SystemSetting

logger = logging.getLogger(__name__)

# Known keys
SIM_NUMBER = "gateway.sim_number"
LAST_POLL = "gateway.last_poll"
WEBHOOK_REGISTERED = "gateway.webhook_registered"
NOTIFY_MUTED_SENDERS = "notifications.muted_senders"

# Carrier/service short codes & sender IDs that only ever send automated
# balance/top-up alerts (MTN, Airtel, 9mobile, Glo, banks, ...). Nobody wants
# a Pushover ping for each one, so they are muted by default. The list is
# stored in the DB and editable from Settings -> Pushover.
DEFAULT_MUTED_SENDERS = ["MTN", "AIRTEL"]


async def get_setting(db: AsyncSession, key: str, default: Optional[str] = None) -> Optional[str]:
    """Read a raw setting value."""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None or row.value is None:
        return default
    return row.value


async def set_setting(
    db: AsyncSession,
    key: str,
    value: str,
    category: str = "gateway",
    description: Optional[str] = None,
) -> None:
    """Create or update a setting."""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemSetting(
            key=key, value=str(value), category=category, description=description
        )
        db.add(row)
    else:
        row.value = str(value)
        if description:
            row.description = description
    await db.flush()


async def get_int(db: AsyncSession, key: str, default: int) -> int:
    raw = await get_setting(db, key)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("Setting %s is not an int (%r); using default %s", key, raw, default)
        return default


async def get_float(db: AsyncSession, key: str, default: float) -> float:
    raw = await get_setting(db, key)
    if raw is None:
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


async def get_sim_number(db: AsyncSession) -> int:
    """Currently selected SIM slot (1 or 2)."""
    return await get_int(db, SIM_NUMBER, 1)


async def set_sim_number(db: AsyncSession, sim: int) -> int:
    sim = max(1, min(2, int(sim)))
    await set_setting(
        db, SIM_NUMBER, str(sim), category="gateway", description="Active SIM slot"
    )
    return sim


async def get_muted_notify_senders(db: AsyncSession) -> list[str]:
    """Senders that must NOT trigger a Pushover notification.

    Alphanumeric sender IDs are stored uppercased (``MTN``, ``AIRTEL``), so we
    compare case-insensitively. The value is a comma-separated list kept in
    ``system_settings``; when unset it falls back to the carrier defaults so
    the noise is muted out of the box.
    """
    raw = await get_setting(db, NOTIFY_MUTED_SENDERS, ",".join(DEFAULT_MUTED_SENDERS))
    muted: list[str] = []
    for part in (raw or "").split(","):
        s = part.strip().upper()
        if s and s not in muted:
            muted.append(s)
    return muted


async def set_muted_notify_senders(db: AsyncSession, senders: list[str]) -> list[str]:
    """Persist the muted-sender list (stored as a comma-separated string)."""
    clean: list[str] = []
    for part in senders:
        s = (part or "").strip().upper()
        if s and s not in clean:
            clean.append(s)
    await set_setting(
        db,
        NOTIFY_MUTED_SENDERS,
        ",".join(clean),
        category="notifications",
        description="Sender IDs that never trigger Pushover alerts (comma-separated)",
    )
    return clean
