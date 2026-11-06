"""Multi-Phone SMS Gateway Service.

Allows operators to create, connect, test, and route across multiple SMS sender
phones directly from App Settings while permanently preserving the primary SMS
sender configured via Render environment variables (``SMSGATE_*``).

Security invariants:
  * Passwords and webhook signing secrets are encrypted at rest via Fernet.
  * Raw credentials NEVER leave the server — API responses carry masked hints
    and ``has_password`` / ``has_webhook_secret`` booleans only.
  * The Render environment sender (``id="env"``) is non-destructive and
    read-only: adding, editing, pausing, or deleting database phones can NEVER
    stop or overwrite the Render sender.
"""

from __future__ import annotations

from datetime import datetime, timezone
import itertools
import logging
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.gateway import GatewaySetting
from app.models.system import SystemSetting
from app.providers.smsgate import (
    DEFAULT_BASE,
    register_webhook_direct,
    register_webhook_with_credentials,
    send_sms_with_credentials,
    test_connection_direct,
    test_connection_with_credentials,
)
from app.security.encryption import decrypt_value, encrypt_value, mask_value
from app.services.system_settings import get_sim_number
from app.utils.urls import WEBHOOK_PATH, public_base_url, webhook_url

logger = logging.getLogger(__name__)

ROUTING_MODE_KEY = "sms_gateway.routing_mode"
SIM_FAILOVER_KEY = "sms_gateway.sim_failover"
VALID_ROUTING_MODES = ("render_first", "failover", "round_robin", "default_phone")

_rr_counter = itertools.count(0)


def _safe_decrypt(cipher: Optional[str]) -> str:
    if not cipher:
        return ""
    try:
        return decrypt_value(cipher) or ""
    except Exception:
        return cipher


async def _get_sys_val(db: AsyncSession, key: str) -> Optional[str]:
    row = (await db.execute(select(SystemSetting).where(SystemSetting.key == key))).scalar_one_or_none()
    return row.value if row and row.value is not None else None


async def _set_sys_val(db: AsyncSession, key: str, value: str, description: str = "") -> None:
    row = (await db.execute(select(SystemSetting).where(SystemSetting.key == key))).scalar_one_or_none()
    if row is None:
        db.add(SystemSetting(key=key, value=value, category="sms_gateway", description=description))
    else:
        row.value = value
    await db.flush()


async def get_routing_config(db: AsyncSession) -> dict[str, Any]:
    mode = (await _get_sys_val(db, ROUTING_MODE_KEY) or "render_first").strip().lower()
    if mode not in VALID_ROUTING_MODES:
        mode = "render_first"
    sim_failover_raw = await _get_sys_val(db, SIM_FAILOVER_KEY)
    sim_failover = (
        True
        if sim_failover_raw is None
        else str(sim_failover_raw).strip().lower() in ("1", "true", "yes", "on")
    )
    return {"routing_mode": mode, "sim_failover": sim_failover}


async def set_routing_config(
    db: AsyncSession,
    *,
    routing_mode: Optional[str] = None,
    sim_failover: Optional[bool] = None,
) -> dict[str, Any]:
    if routing_mode is not None:
        cleaned = routing_mode.strip().lower()
        if cleaned not in VALID_ROUTING_MODES:
            raise ValueError(f"Unsupported routing mode '{routing_mode}'. Choose from {VALID_ROUTING_MODES}.")
        await _set_sys_val(db, ROUTING_MODE_KEY, cleaned, "Multi-phone SMS routing strategy")
    if sim_failover is not None:
        await _set_sys_val(db, SIM_FAILOVER_KEY, "true" if sim_failover else "false", "Automatic SIM 1/2 failover")
    await db.commit()
    return await get_routing_config(db)


def phone_webhook_url(phone_id: int | str) -> str:
    base = public_base_url()
    if not base:
        return ""
    if str(phone_id) == "env":
        return webhook_url() or ""
    return f"{base}{WEBHOOK_PATH}/phone/{phone_id}"


def serialize_db_phone(row: GatewaySetting) -> dict[str, Any]:
    pw_plain = _safe_decrypt(row.password_encrypted)
    wh_plain = _safe_decrypt(row.webhook_secret_encrypted)
    return {
        "id": str(row.id),
        "db_id": row.id,
        "name": row.name,
        "provider": row.provider or "smsgate",
        "mode": getattr(row, "mode", None) or "cloud",
        "is_render_env": False,
        "protected": False,
        "is_enabled": bool(row.is_enabled),
        "is_default": bool(getattr(row, "is_default", False)),
        "configured": bool((row.username or "").strip() and pw_plain),
        "phone_number": getattr(row, "phone_number", None) or "",
        "device_id": getattr(row, "device_id", None) or "",
        "base_url": row.base_url or DEFAULT_BASE,
        "username": row.username or "",
        "password_masked": mask_value(pw_plain) if pw_plain else "",
        "has_password": bool(pw_plain),
        "sim_number": row.sim_number or 1,
        "priority": getattr(row, "priority", 10) or 10,
        "daily_limit": getattr(row, "daily_limit", 0) or 0,
        "notes": getattr(row, "notes", None) or "",
        "webhook_url": row.webhook_url or phone_webhook_url(row.id),
        "has_webhook_secret": bool(wh_plain),
        "webhook_secret_masked": mask_value(wh_plain) if wh_plain else "",
        "connection_status": row.connection_status or "unknown",
        "last_successful_connection": (
            row.last_successful_connection.isoformat() if row.last_successful_connection else None
        ),
        "last_error": row.last_error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def build_render_env_phone(db: AsyncSession, *, has_db_default: bool = False) -> dict[str, Any]:
    sim = await get_sim_number(db)
    configured = bool(settings.smsgate_configured)
    return {
        "id": "env",
        "db_id": None,
        "name": "Render Primary Sender",
        "provider": "smsgate",
        "mode": "cloud",
        "is_render_env": True,
        "protected": True,
        "is_enabled": configured,
        "is_default": configured and not has_db_default,
        "configured": configured,
        "phone_number": "",
        "device_id": "",
        "base_url": settings.SMSGATE_BASE_URL or DEFAULT_BASE,
        "username": settings.SMSGATE_USERNAME or "",
        "password_masked": mask_value(settings.SMSGATE_PASSWORD or "") if settings.SMSGATE_PASSWORD else "",
        "has_password": bool(settings.SMSGATE_PASSWORD),
        "sim_number": sim,
        "priority": 1,
        "daily_limit": 0,
        "notes": "Managed via Render environment variables (SMSGATE_*). Always protected.",
        "webhook_url": webhook_url() or "",
        "has_webhook_secret": bool(settings.SMSGATE_WEBHOOK_SECRET),
        "webhook_secret_masked": (
            mask_value(settings.SMSGATE_WEBHOOK_SECRET) if settings.SMSGATE_WEBHOOK_SECRET else ""
        ),
        "connection_status": "online" if configured else "unconfigured",
        "last_successful_connection": None,
        "last_error": None,
        "created_at": None,
    }


async def list_sender_phones(db: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(GatewaySetting).order_by(GatewaySetting.priority.asc(), GatewaySetting.id.asc())
        )
    ).scalars().all()
    has_db_default = any(bool(getattr(r, "is_default", False)) and bool(r.is_enabled) for r in rows)
    out: list[dict[str, Any]] = []
    if settings.smsgate_configured:
        out.append(await build_render_env_phone(db, has_db_default=has_db_default))
    out.extend(serialize_db_phone(r) for r in rows)
    return out


async def create_sender_phone(db: AsyncSession, data: dict[str, Any]) -> dict[str, Any]:
    name = (data.get("name") or "").strip() or "Connected Android Phone"
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    if not username or not password:
        raise ValueError("Both SMS-Gate username and password are required to connect a phone.")

    is_default = bool(data.get("is_default", False))
    if is_default:
        await db.execute(update(GatewaySetting).values(is_default=False))

    wh_secret = (data.get("webhook_secret") or "").strip()
    raw_sim = data.get("sim_number") if data.get("sim_number") is not None else data.get("sim_slot")
    sim = 2 if int(raw_sim or 1) == 2 else 1

    row = GatewaySetting(
        name=name[:100],
        provider="smsgate",
        mode=((data.get("mode") or "cloud").strip().lower() or "cloud")[:30],
        is_enabled=bool(data.get("is_enabled", True)),
        is_default=is_default,
        phone_number=((data.get("phone_number") or "").strip() or None),
        device_id=((data.get("device_id") or "").strip() or None),
        base_url=((data.get("base_url") or "").strip() or DEFAULT_BASE)[:500],
        username=username[:255],
        password_encrypted=encrypt_value(password),
        sim_number=sim,
        priority=max(1, int(data.get("priority") or 10)),
        daily_limit=max(0, int(data.get("daily_limit") or 0)),
        notes=((data.get("notes") or "").strip()[:500] or None),
        webhook_secret_encrypted=encrypt_value(wh_secret) if wh_secret else None,
        connection_status="unknown",
    )
    db.add(row)
    await db.flush()
    row.webhook_url = phone_webhook_url(row.id) or None
    await db.commit()
    await db.refresh(row)
    return serialize_db_phone(row)


async def update_sender_phone(db: AsyncSession, phone_id: str, data: dict[str, Any]) -> dict[str, Any]:
    if str(phone_id) == "env":
        raise ValueError(
            "The Render environment sender is managed by Render env vars. "
            "Use 'Connect Phone' to add phones alongside it."
        )
    try:
        pid = int(phone_id)
    except ValueError as exc:
        raise LookupError("Sender phone not found") from exc

    row = (await db.execute(select(GatewaySetting).where(GatewaySetting.id == pid))).scalar_one_or_none()
    if row is None:
        raise LookupError("Sender phone not found")

    if data.get("is_default") is True:
        await db.execute(update(GatewaySetting).where(GatewaySetting.id != pid).values(is_default=False))
        row.is_default = True
    elif data.get("is_default") is False:
        row.is_default = False

    if "name" in data and data["name"] is not None and str(data["name"]).strip():
        row.name = str(data["name"]).strip()[:100]
    if "mode" in data and data["mode"] is not None:
        row.mode = (str(data["mode"]).strip().lower() or "cloud")[:30]
    if "is_enabled" in data and data["is_enabled"] is not None:
        row.is_enabled = bool(data["is_enabled"])
    if "phone_number" in data and data["phone_number"] is not None:
        row.phone_number = str(data["phone_number"]).strip() or None
    if "device_id" in data and data["device_id"] is not None:
        row.device_id = str(data["device_id"]).strip() or None
    if "base_url" in data and data["base_url"] is not None:
        row.base_url = (str(data["base_url"]).strip() or DEFAULT_BASE)[:500]
    if "username" in data and data["username"] is not None and str(data["username"]).strip():
        row.username = str(data["username"]).strip()[:255]
    if "password" in data and data["password"]:
        row.password_encrypted = encrypt_value(str(data["password"]).strip())
    if "webhook_secret" in data and data["webhook_secret"] is not None:
        secret_val = str(data["webhook_secret"]).strip()
        row.webhook_secret_encrypted = encrypt_value(secret_val) if secret_val else None
    if "sim_number" in data and data["sim_number"] is not None:
        row.sim_number = 2 if int(data["sim_number"]) == 2 else 1
    if "priority" in data and data["priority"] is not None:
        row.priority = max(1, int(data["priority"]))
    if "daily_limit" in data and data["daily_limit"] is not None:
        row.daily_limit = max(0, int(data["daily_limit"]))
    if "notes" in data and data["notes"] is not None:
        row.notes = str(data["notes"]).strip()[:500] or None

    await db.commit()
    await db.refresh(row)
    return serialize_db_phone(row)


async def delete_sender_phone(db: AsyncSession, phone_id: str) -> None:
    if str(phone_id) == "env":
        raise ValueError("The Render primary sender is protected and cannot be deleted.")
    try:
        pid = int(phone_id)
    except ValueError as exc:
        raise LookupError("Sender phone not found") from exc

    row = (await db.execute(select(GatewaySetting).where(GatewaySetting.id == pid))).scalar_one_or_none()
    if row is None:
        raise LookupError("Sender phone not found")
    await db.delete(row)
    await db.commit()


async def test_sender_phone(
    db: AsyncSession,
    phone_id: str,
    *,
    test_recipient: Optional[str] = None,
    test_message: Optional[str] = None,
) -> dict[str, Any]:
    if str(phone_id) == "env":
        if test_recipient:
            from app.providers.smsgate import send_sms_direct

            sim = await get_sim_number(db)
            r = await send_sms_direct(
                test_recipient.strip(),
                (test_message or "SENDERSMS test message from Render Primary Sender").strip(),
                sim,
            )
            return {
                "success": bool(r.get("success")),
                "online": bool(r.get("success")),
                "sent_test_sms": True,
                "message": "Test SMS sent via Render Primary Sender" if r.get("success") else (r.get("error") or "Failed"),
            }
        r = await test_connection_direct()
        ok = bool(r.get("success") and r.get("online"))
        return {
            "success": ok,
            "online": ok,
            "message": "Render Primary Sender online" if ok else (r.get("message") or "Offline / check credentials"),
        }

    try:
        pid = int(phone_id)
    except ValueError as exc:
        raise LookupError("Sender phone not found") from exc
    row = (await db.execute(select(GatewaySetting).where(GatewaySetting.id == pid))).scalar_one_or_none()
    if row is None:
        raise LookupError("Sender phone not found")

    pw = _safe_decrypt(row.password_encrypted)
    if test_recipient:
        r = await send_sms_with_credentials(
            test_recipient.strip(),
            (test_message or f"SENDERSMS test SMS from {row.name}").strip(),
            base_url=row.base_url or DEFAULT_BASE,
            username=row.username or "",
            password=pw,
            sim=row.sim_number or 1,
            device_id=row.device_id,
        )
        ok = bool(r.get("success"))
        row.connection_status = "online" if ok else "offline"
        if ok:
            row.last_successful_connection = datetime.now(timezone.utc)
            row.last_error = None
        else:
            row.last_error = r.get("error") or "Send failed"
        await db.commit()
        return {
            "success": ok,
            "online": ok,
            "sent_test_sms": True,
            "message": f"Test SMS sent via {row.name}" if ok else (r.get("error") or "Failed"),
        }

    r = await test_connection_with_credentials(
        base_url=row.base_url or DEFAULT_BASE,
        username=row.username or "",
        password=pw,
        sim=row.sim_number or 1,
    )
    ok = bool(r.get("success") and r.get("online"))
    row.connection_status = "online" if ok else "offline"
    if ok:
        row.last_successful_connection = datetime.now(timezone.utc)
        row.last_error = None
    else:
        row.last_error = r.get("message") or f"HTTP {r.get('http')}"
    await db.commit()
    return {
        "success": ok,
        "online": ok,
        "message": f"{row.name} connected" if ok else (row.last_error or "Phone offline or invalid credentials"),
    }


async def register_sender_phone_webhook(
    db: AsyncSession,
    phone_id: str,
    *,
    custom_url: Optional[str] = None,
) -> dict[str, Any]:
    if str(phone_id) == "env":
        target = (custom_url or webhook_url() or "").strip()
        if not target:
            raise ValueError("PUBLIC_BASE_URL is not configured.")
        return await register_webhook_direct(target)

    try:
        pid = int(phone_id)
    except ValueError as exc:
        raise LookupError("Sender phone not found") from exc
    row = (await db.execute(select(GatewaySetting).where(GatewaySetting.id == pid))).scalar_one_or_none()
    if row is None:
        raise LookupError("Sender phone not found")

    target = (custom_url or phone_webhook_url(row.id) or "").strip()
    if not target:
        raise ValueError("PUBLIC_BASE_URL is not configured; set it or supply a webhook URL.")
    pw = _safe_decrypt(row.password_encrypted)
    res = await register_webhook_with_credentials(
        target,
        base_url=row.base_url or DEFAULT_BASE,
        username=row.username or "",
        password=pw,
    )
    if res.get("success"):
        row.webhook_url = target
        await db.commit()
    return res


async def list_valid_webhook_secrets(db: AsyncSession, *, phone_id: Optional[int] = None) -> list[str]:
    """Return every valid HMAC signing secret (Render primary + active DB phones)."""
    secrets_out: list[str] = []
    if phone_id is not None:
        row = (
            await db.execute(select(GatewaySetting).where(GatewaySetting.id == phone_id))
        ).scalar_one_or_none()
        if row and row.webhook_secret_encrypted:
            dec = _safe_decrypt(row.webhook_secret_encrypted)
            if dec:
                secrets_out.append(dec)
        if settings.SMSGATE_WEBHOOK_SECRET and settings.SMSGATE_WEBHOOK_SECRET not in secrets_out:
            secrets_out.append(settings.SMSGATE_WEBHOOK_SECRET)
        return secrets_out

    if settings.SMSGATE_WEBHOOK_SECRET:
        secrets_out.append(settings.SMSGATE_WEBHOOK_SECRET)
    rows = (await db.execute(select(GatewaySetting))).scalars().all()
    for r in rows:
        dec = _safe_decrypt(r.webhook_secret_encrypted)
        if dec and dec not in secrets_out:
            secrets_out.append(dec)
    return secrets_out


async def has_any_usable_sms_sender(db: AsyncSession) -> bool:
    if settings.smsgate_configured:
        return True
    rows = (
        await db.execute(select(GatewaySetting).where(GatewaySetting.is_enabled == True))  # noqa: E712
    ).scalars().all()
    return any(bool((r.username or "").strip() and _safe_decrypt(r.password_encrypted)) for r in rows)


async def dispatch_sms_multi_phone(
    db: AsyncSession,
    phone_number: str,
    body: str,
    *,
    preferred_gateway_id: Optional[int] = None,
    sim_override: Optional[int] = None,
) -> dict[str, Any]:
    """Dispatch an outbound SMS across configured sender phones while ALWAYS
    preserving the exact 3-arg ``send_sms_direct(phone, body, sim)`` contract
    whenever the Render primary sender is used.
    """
    from app.providers import smsgate as smsgate_mod

    routing = await get_routing_config(db)
    mode = routing["routing_mode"]
    sim_failover = routing["sim_failover"]
    default_sim = sim_override if sim_override in (1, 2) else await get_sim_number(db)

    db_rows = [
        r
        for r in (
            await db.execute(
                select(GatewaySetting)
                .where(GatewaySetting.is_enabled == True)  # noqa: E712
                .order_by(GatewaySetting.priority.asc(), GatewaySetting.id.asc())
            )
        ).scalars().all()
        if (r.username or "").strip() and _safe_decrypt(r.password_encrypted)
    ]

    # Fast default path: when no DB phones are configured (or mode is render_first
    # with Render configured and no explicit campaign gateway_setting_id), call
    # send_sms_direct(phone, body, sim) directly so existing behaviour & mocks stay 100% identical!
    render_candidate = {"kind": "render", "id": "env", "sim": default_sim} if settings.smsgate_configured else None

    candidates: list[dict[str, Any]] = []
    if preferred_gateway_id is not None:
        explicit = next((r for r in db_rows if r.id == preferred_gateway_id), None)
        if explicit is not None:
            candidates.append({"kind": "db", "row": explicit})
        if render_candidate:
            candidates.append(render_candidate)
    elif not db_rows:
        # Preserve legacy behaviour even when env vars are mocked/unconfigured in tests
        candidates.append({"kind": "render", "id": "env", "sim": default_sim})
    elif mode == "render_first":
        if render_candidate:
            candidates.append(render_candidate)
        candidates.extend({"kind": "db", "row": r} for r in db_rows)
    elif mode == "default_phone":
        preferred_rows = [r for r in db_rows if getattr(r, "is_default", False)]
        other_rows = [r for r in db_rows if not getattr(r, "is_default", False)]
        candidates.extend({"kind": "db", "row": r} for r in preferred_rows)
        if render_candidate:
            candidates.append(render_candidate)
        candidates.extend({"kind": "db", "row": r} for r in other_rows)
    elif mode == "round_robin":
        pool: list[dict[str, Any]] = []
        if render_candidate:
            pool.append(render_candidate)
        pool.extend({"kind": "db", "row": r} for r in db_rows)
        idx = next(_rr_counter) % len(pool)
        candidates = pool[idx:] + pool[:idx]
    else:  # failover
        if render_candidate:
            candidates.append(render_candidate)
        candidates.extend({"kind": "db", "row": r} for r in db_rows)

    errors: list[str] = []

    async def _send_via_candidate(cand: dict[str, Any]) -> dict[str, Any]:
        if cand["kind"] == "render":
            sim1 = cand["sim"]
            r1 = await smsgate_mod.send_sms_direct(phone_number, body, sim1)
            if r1.get("success") or not sim_failover:
                return {**r1, "sender_phone_id": "env", "sender_source": "render_env"}
            other_sim = 2 if sim1 == 1 else 1
            r2 = await smsgate_mod.send_sms_direct(phone_number, body, other_sim)
            if r2.get("success"):
                return {**r2, "sender_phone_id": "env", "sender_source": "render_env"}
            return {
                "success": False,
                "error": f"SIM{sim1}: {r1.get('error', '')} | SIM{other_sim}: {r2.get('error', '')}"[:500],
                "raw": r2.get("raw") or r1.get("raw"),
                "sender_phone_id": "env",
                "sender_source": "render_env",
            }

        row: GatewaySetting = cand["row"]
        pw = _safe_decrypt(row.password_encrypted)
        sim1 = sim_override if sim_override in (1, 2) else (row.sim_number or 1)
        r1 = await send_sms_with_credentials(
            phone_number,
            body,
            base_url=row.base_url or DEFAULT_BASE,
            username=row.username or "",
            password=pw,
            sim=sim1,
            device_id=row.device_id,
        )
        if r1.get("success"):
            row.connection_status = "online"
            row.last_successful_connection = datetime.now(timezone.utc)
            row.last_error = None
            return {**r1, "sender_phone_id": str(row.id), "sender_source": "db_phone"}
        if sim_failover:
            other_sim = 2 if sim1 == 1 else 1
            r2 = await send_sms_with_credentials(
                phone_number,
                body,
                base_url=row.base_url or DEFAULT_BASE,
                username=row.username or "",
                password=pw,
                sim=other_sim,
                device_id=row.device_id,
            )
            if r2.get("success"):
                row.connection_status = "online"
                row.last_successful_connection = datetime.now(timezone.utc)
                row.last_error = None
                return {**r2, "sender_phone_id": str(row.id), "sender_source": "db_phone"}
        row.connection_status = "offline"
        row.last_error = r1.get("error") or "Send failed"
        return {**r1, "sender_phone_id": str(row.id), "sender_source": "db_phone"}

    last_result: dict[str, Any] = {"success": False, "error": "No SMS sender configured"}
    for cand in candidates:
        res = await _send_via_candidate(cand)
        if res.get("success"):
            return res
        errors.append(f"{cand.get('id') or cand['row'].name}: {res.get('error', 'failed')}")
        last_result = res

    if len(errors) > 1:
        last_result = {**last_result, "error": " | ".join(errors)[:500]}
    return last_result
