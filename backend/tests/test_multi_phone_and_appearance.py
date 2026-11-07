"""Tests for Multi-Phone SMS Sender Pool (preserving Render Primary) and Settings Appearance Studio."""

import hashlib
import hmac
import json
import time

import pytest

from app.config import settings
from app.services import gateway_service


@pytest.mark.asyncio
async def test_multi_phone_crud_never_breaks_or_exposes_render_primary(
    api_client, api_db, sms_gateway_configured
):
    """Adding, editing, testing, and deleting DB phones keeps Render Primary intact and never leaks raw secrets."""
    # 1. Initial GET /settings/gateway includes Render Primary Sender
    gw_res = await api_client.get("/api/v1/settings/gateway")
    assert gw_res.status_code == 200
    gw = gw_res.json()
    assert gw["configured"] is True
    assert gw["render_sender_active"] is True
    assert any(p["id"] == "env" and p["protected"] is True for p in gw["phones"])

    # 2. Attempting to delete or overwrite Render Primary ("env") is rejected
    del_env = await api_client.delete("/api/v1/settings/gateway/phones/env")
    assert del_env.status_code == 400
    put_env = await api_client.put("/api/v1/settings/gateway/phones/env", json={"name": "Hack"})
    assert put_env.status_code == 400

    # 3. Connect two additional phones with their own credentials & signing keys
    p1_res = await api_client.post(
        "/api/v1/settings/gateway/phones",
        json={
            "name": "Abuja Sales Phone #2",
            "mode": "cloud",
            "phone_number": "+2348031112222",
            "username": "abuja_u2",
            "password": "super-secret-password-2",
            "webhook_secret": "whsec-phone-two-secret",
            "sim_number": 2,
            "priority": 5,
            "is_default": True,
        },
    )
    assert p1_res.status_code == 200, p1_res.text
    p1 = p1_res.json()["phone"]
    assert p1["name"] == "Abuja Sales Phone #2"
    assert p1["sim_number"] == 2
    assert p1["has_password"] is True
    assert p1["has_webhook_secret"] is True
    # Raw secret must NEVER appear in API JSON
    raw_json = json.dumps(p1_res.json())
    assert "super-secret-password-2" not in raw_json
    assert "whsec-phone-two-secret" not in raw_json

    # 4. Render Primary is STILL active alongside the new phone
    phones_list = p1_res.json()["phones"]
    assert len(phones_list) == 2
    assert any(p["id"] == "env" and p["is_enabled"] is True for p in phones_list)
    assert settings.SMSGATE_USERNAME == "user"
    assert settings.SMSGATE_PASSWORD == "pass"

    # 5. Per-phone webhook signature verification accepts the new phone's signing key
    payload_bytes = json.dumps(
        {
            "event": "sms:received",
            "id": "evt-multi-phone-1",
            "payload": {
                "messageId": "msg-mp-1",
                "message": "Interested in pricing",
                "sender": "+2348059998888",
                "recipient": "+2348031112222",
                "simNumber": 2,
            },
        }
    ).encode("utf-8")
    ts = str(int(time.time()))
    sig = hmac.new(
        b"whsec-phone-two-secret",
        payload_bytes + ts.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    wh_res = await api_client.post(
        f"/api/v1/webhooks/smsgateway/phone/{p1['db_id']}",
        content=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": ts,
            "X-Signature": sig,
        },
    )
    assert wh_res.status_code == 200, wh_res.text
    assert wh_res.json().get("ok") is True

    # 6. Deleting the extra phone leaves Render Primary untouched
    del_p1 = await api_client.delete(f"/api/v1/settings/gateway/phones/{p1['id']}")
    assert del_p1.status_code == 200
    remaining = del_p1.json()["phones"]
    assert len(remaining) == 1
    assert remaining[0]["id"] == "env"


@pytest.mark.asyncio
async def test_multi_phone_routing_render_first_and_failover(
    api_db, sms_gateway_configured, monkeypatch
):
    """render_first uses Render Primary (3-arg send_sms_direct) first and fails over to connected DB phones if Render fails."""
    phone_row = await gateway_service.create_sender_phone(
        api_db,
        {
            "name": "Backup Android #2",
            "username": "backup_user",
            "password": "backup_password",
            "sim_number": 1,
            "is_enabled": True,
        },
    )
    assert phone_row["configured"] is True

    render_calls: list[tuple] = []
    db_phone_calls: list[dict] = []

    async def _fake_render_send(phone, body, sim=1):
        render_calls.append((phone, body, sim))
        return {"success": False, "error": "Primary phone offline"}

    async def _fake_cred_send(phone, body, **kwargs):
        db_phone_calls.append({"phone": phone, "body": body, **kwargs})
        return {"success": True, "provider_message_id": "db-msg-001", "status": "sent"}

    from app.providers import smsgate as smsgate_mod

    monkeypatch.setattr(smsgate_mod, "send_sms_direct", _fake_render_send)
    monkeypatch.setattr(gateway_service, "send_sms_with_credentials", _fake_cred_send)

    res = await gateway_service.dispatch_sms_multi_phone(
        api_db, "+2348030001111", "Hello from failover pool"
    )
    assert res["success"] is True
    assert res["provider_message_id"] == "db-msg-001"
    assert res["sender_source"] == "db_phone"
    # Render primary tried SIM 1 + SIM 2 failover first, then cleanly failed over to Backup Android #2
    assert len(render_calls) == 2
    assert len(db_phone_calls) == 1
    assert db_phone_calls[0]["username"] == "backup_user"


@pytest.mark.asyncio
async def test_appearance_preferences_validation_and_persistence(api_client):
    get_default = await api_client.get("/api/v1/settings/appearance")
    assert get_default.status_code == 200
    assert get_default.json()["palette"] == "emerald"

    save_res = await api_client.put(
        "/api/v1/settings/appearance",
        json={
            "mode": "dark",
            "palette": "sapphire",
            "density": "compact",
            "radius": "soft",
            "font": "inter",
        },
    )
    assert save_res.status_code == 200, save_res.text
    saved = save_res.json()
    assert saved["mode"] == "dark"
    assert saved["palette"] == "sapphire"
    assert saved["density"] == "compact"
    assert saved["radius"] == "soft"
    assert saved["font"] == "inter"

    bad_res = await api_client.put("/api/v1/settings/appearance", json={"palette": "neon-rainbow"})
    assert bad_res.status_code == 400
