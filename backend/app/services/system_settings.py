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

# CallGate (phone calls) — same keys the Settings -> Calls page writes.
# DB values take precedence over the CALLGATE_* env vars so the whole setup
# can be done from the app UI without touching the server environment.
CALLGATE_BASE_URL = "callgate.base_url"
CALLGATE_USERNAME = "callgate.username"
CALLGATE_PASSWORD = "callgate.password"
CALLGATE_WEBHOOK_SECRET = "callgate.webhook_secret"
CALLGATE_WEBHOOK_REGISTERED = "callgate.webhook_registered"
# "callgate" (place the call on the handset via API) or "direct" (tel: link).
CALLGATE_DIAL_MODE = "callgate.dial_mode"

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


async def get_callgate_settings(db: AsyncSession) -> dict:
    """Effective CallGate config: DB (Settings UI) wins, env is fallback."""
    from app.config import settings

    base = await get_setting(db, CALLGATE_BASE_URL) or (settings.CALLGATE_BASE_URL or "")
    username = await get_setting(db, CALLGATE_USERNAME) or (settings.CALLGATE_USERNAME or "")
    # Password/secret may be stored encrypted — decrypt, else env fallback.
    from app.security.encryption import decrypt_value

    def _maybe_decrypt(raw: str | None, fallback: str) -> str:
        if raw:
            try:
                dec = decrypt_value(raw)
                if dec:
                    return dec
            except Exception:
                pass
            if not raw.startswith("enc:"):
                return raw
        return fallback or ""

    password = _maybe_decrypt(await get_setting(db, CALLGATE_PASSWORD),
                              settings.CALLGATE_PASSWORD or "")
    secret = _maybe_decrypt(await get_setting(db, CALLGATE_WEBHOOK_SECRET),
                            settings.CALLGATE_WEBHOOK_SECRET or "")
    dial_mode = (await get_setting(db, CALLGATE_DIAL_MODE, "callgate") or "callgate").strip().lower()
    if dial_mode not in ("callgate", "direct"):
        dial_mode = "callgate"
    base = (base or "").strip().rstrip("/")
    return {
        "base_url": base,
        "username": (username or "").strip(),
        "password": password or "",
        "webhook_secret": secret or "",
        "dial_mode": dial_mode,
        "configured": bool(base and username and password),
    }


async def set_callgate_settings(
    db: AsyncSession,
    *,
    base_url: str | None = None,
    username: str | None = None,
    password: str | None = None,
    webhook_secret: str | None = None,
    dial_mode: str | None = None,
) -> dict:
    """Persist CallGate config written from Settings -> Calls."""
    from app.security.encryption import encrypt_value

    if base_url is not None:
        await set_setting(db, CALLGATE_BASE_URL, base_url.strip().rstrip("/"),
                           category="callgate", description="CallGate API base URL (phone)")
    if username is not None:
        await set_setting(db, CALLGATE_USERNAME, username.strip(),
                           category="callgate", description="CallGate username")
    if password is not None:
        # Stored encrypted at rest; decrypted on read by callers.
        try:
            stored = encrypt_value(password) if password else ""
        except Exception:
            stored = password
        await set_setting(db, CALLGATE_PASSWORD, stored,
                           category="callgate", description="CallGate password (encrypted)")
    if webhook_secret is not None:
        try:
            stored = encrypt_value(webhook_secret) if webhook_secret else ""
        except Exception:
            stored = webhook_secret
        await set_setting(db, CALLGATE_WEBHOOK_SECRET, stored,
                           category="callgate", description="CallGate webhook signing key (encrypted)")
    if dial_mode is not None:
        mode = (dial_mode or "").strip().lower()
        if mode in ("callgate", "direct"):
            await set_setting(db, CALLGATE_DIAL_MODE, mode,
                               category="callgate", description="How calls are placed")
    return await get_callgate_settings(db)


async def get_callgate_password(db: AsyncSession) -> str:
    """CallGate password with DB-encrypted value decrypted, env fallback."""
    from app.config import settings
    from app.security.encryption import decrypt_value

    raw = await get_setting(db, CALLGATE_PASSWORD)
    if raw:
        try:
            dec = decrypt_value(raw)
            if dec:
                return dec
        except Exception:
            pass
        # Stored plain (encryption unavailable) — still usable.
        if not raw.startswith("enc:"):
            return raw
    return settings.CALLGATE_PASSWORD or ""


async def get_callgate_secret(db: AsyncSession) -> str:
    """CallGate webhook signing secret with DB-encrypted value decrypted."""
    from app.config import settings
    from app.security.encryption import decrypt_value

    raw = await get_setting(db, CALLGATE_WEBHOOK_SECRET)
    if raw:
        try:
            dec = decrypt_value(raw)
            if dec:
                return dec
        except Exception:
            pass
        if not raw.startswith("enc:"):
            return raw
    return settings.CALLGATE_WEBHOOK_SECRET or ""


# --- Inbuilt browser notifications (VAPID keypair, generated once) ---
VAPID_PUBLIC_KEY = "push.vapid_public"
VAPID_PRIVATE_KEY = "push.vapid_private"


async def get_vapid_keys(db: AsyncSession) -> dict:
    """Return the VAPID keypair, generating + persisting it on first use.

    Env vars win when both are set (stable keys across fresh databases);
    otherwise a keypair is generated once and stored in the DB so restarts
    and redeploys never invalidate existing subscriptions.
    """
    from app.config import settings

    if settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY:
        return {"public": settings.VAPID_PUBLIC_KEY.strip(),
                "private": settings.VAPID_PRIVATE_KEY.strip(),
                "source": "env"}

    pub = await get_setting(db, VAPID_PUBLIC_KEY)
    priv = await get_setting(db, VAPID_PRIVATE_KEY)
    if pub and priv:
        return {"public": pub, "private": priv, "source": "db"}

    # First run: generate a P-256 keypair.
    import base64

    from py_vapid import Vapid

    v = Vapid()
    v.generate_keys()
    raw_priv = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    nums = v.public_key.public_numbers()
    raw_pub = b"\x04" + nums.x.to_bytes(32, "big") + nums.y.to_bytes(32, "big")

    def _b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    pub, priv = _b64(raw_pub), _b64(raw_priv)
    await set_setting(db, VAPID_PUBLIC_KEY, pub, category="push",
                       description="VAPID public key for browser notifications")
    await set_setting(db, VAPID_PRIVATE_KEY, priv, category="push",
                       description="VAPID private key (server only, never exposed)")
    await db.flush()
    logger.info("Generated new VAPID keypair for browser notifications")
    return {"public": pub, "private": priv, "source": "generated"}
