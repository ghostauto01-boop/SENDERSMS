"""Gateway dispatch — pick the active SMS gateway and route traffic to it.

The app ships with two interchangeable SMS gateways:

* ``smsgate`` — SMS-Gate.app (Android phone bridge, the original gateway)
* ``dmobili`` — Dmobili.com / Pace Bulk SMS HTTP API (two-way gateway)

Every send/status call site goes through this module instead of importing a
provider directly, so switching gateways is a single Settings toggle and the
rest of the codebase never has to know which one is live. The default is
``smsgate``; behaviour is byte-for-byte identical to the single-gateway app
until the operator actively switches (and ``dmobili`` is configured).
"""

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

ACTIVE_GATEWAY_KEY = "gateway.active_provider"

# Gateways the dispatcher knows about. Order = display order in Settings.
KNOWN_PROVIDERS = ("smsgate", "dmobili")


def provider_configured(provider: str) -> bool:
    """Whether the given provider has usable credentials in the environment."""
    if provider == "smsgate":
        return settings.smsgate_configured
    if provider == "dmobili":
        return settings.dmobili_configured
    return False


async def get_active_gateway(db: AsyncSession) -> str:
    """The provider all outbound SMS currently goes through.

    Falls back to ``smsgate`` whenever the stored value is missing or stale,
    so an invalid toggle can never take the whole app offline.
    """
    from app.services.system_settings import get_setting

    raw = await get_setting(db, ACTIVE_GATEWAY_KEY, "smsgate")
    raw = (raw or "smsgate").strip().lower()
    return raw if raw in KNOWN_PROVIDERS else "smsgate"


async def set_active_gateway(db: AsyncSession, provider: str) -> str:
    """Persist the active gateway. Raises ValueError for unknown providers."""
    from app.services.system_settings import set_setting

    p = (provider or "").strip().lower()
    if p not in KNOWN_PROVIDERS:
        raise ValueError(f"Unknown gateway {provider!r}; expected one of {', '.join(KNOWN_PROVIDERS)}")
    await set_setting(
        db, ACTIVE_GATEWAY_KEY, p,
        category="gateway",
        description="Active SMS gateway for all outbound traffic",
    )
    logger.info("Active SMS gateway switched to %s", p)
    return p


async def send_sms_dispatch(
    db: AsyncSession,
    phone: str,
    body: str,
    sim: int = 1,
    sender_id: Optional[str] = None,
) -> tuple[str, dict]:
    """Send one SMS through the ACTIVE gateway.

    Returns ``(provider_name, result)`` where ``result`` is shaped exactly
    like ``app.providers.smsgate.send_sms_direct``'s return value
    (``success`` / ``provider_message_id`` / ``status`` / ``error`` /
    ``raw``), so callers handle both gateways identically.

    ``sim`` only applies to SMS-Gate.app (its phone bridge has SIM slots);
    Dmobili routes by account configuration instead.
    """
    gw = await get_active_gateway(db)
    if gw == "dmobili" and provider_configured("dmobili"):
        from app.providers.dmobili import send_sms_direct as dmobili_send

        return "dmobili", await dmobili_send(phone, body, sender_id)

    if gw == "dmobili" and not provider_configured("dmobili"):
        # Misconfiguration guard: never silently text through a different
        # gateway than the one the operator selected — that changes sender
        # IDs, costs and compliance posture. Fail loudly instead.
        logger.error("Active gateway is dmobili but it has no credentials; refusing to send")
        return "dmobili", {
            "success": False,
            "error": "Dmobili is selected as the active gateway but has no credentials. Configure DMOBILI_* or switch back to SMS-Gate in Settings.",
        }

    from app.providers.smsgate import send_sms_direct as smsgate_send

    return "smsgate", await smsgate_send(phone, body, sim)


async def poll_status_dispatch(db: AsyncSession, ids_by_provider: dict[str, list[str]]) -> list[dict]:
    """Poll delivery statuses grouped by provider.

    ``ids_by_provider`` maps provider name -> list of provider message ids.
    Returns the combined ``[{"provider_message_id", "status"}]`` list.
    """
    results: list[dict] = []
    smsgate_ids = ids_by_provider.get("smsgate") or []
    dmobili_ids = ids_by_provider.get("dmobili") or []

    if smsgate_ids:
        from app.providers.smsgate import poll_status_for_ids

        results.extend(await poll_status_for_ids(smsgate_ids))
    if dmobili_ids:
        from app.providers.dmobili import poll_status_for_ids as dmobili_poll

        results.extend(await dmobili_poll(dmobili_ids))
    return results


async def test_gateway(provider: str) -> dict:
    """Connection test for a specific gateway (Settings UI)."""
    p = (provider or "smsgate").strip().lower()
    if p == "dmobili":
        from app.providers.dmobili import test_connection_direct

        r = await test_connection_direct()
        return {
            "success": bool(r.get("success")),
            "online": bool(r.get("online")),
            "verified": bool(r.get("verified")),
            "message": r.get("message") or ("Connected" if r.get("success") else "Failed"),
            "balance": r.get("balance"),
            "raw": r,
        }
    from app.providers.smsgate import test_connection_direct as smsgate_test

    r = await smsgate_test()
    return {
        "success": r["success"] and r.get("online", False),
        "online": r.get("online", False),
        "verified": r["success"] and r.get("online", False),
        "message": "Connected" if (r["success"] and r.get("online")) else "Failed — phone offline or bad credentials",
        "raw": r,
    }


async def gateway_health(db: AsyncSession) -> dict:
    """Health of the ACTIVE gateway (used by SMSService.check_gateway_health)."""
    gw = await get_active_gateway(db)
    r = await test_gateway(gw)
    return {
        "is_healthy": r["success"],
        "status": "healthy" if r["success"] else "unhealthy",
        "error": None if r["success"] else r.get("message"),
        "provider": gw,
    }
