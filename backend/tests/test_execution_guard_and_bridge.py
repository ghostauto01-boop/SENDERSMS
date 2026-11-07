"""Tests for Execution Guard, SigV4 credential separation, Campaign #25/#26 + 54 opener follow-up reconciliation, idempotency ledger, and hardened arena_mcp_bridge."""

from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import urllib.error

import pytest

from app.mcp import execution_guard
from app.models.campaign import Campaign
from app.models.campaign_followup import CampaignFollowUp
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.scheduled import ScheduledMessage


def _load_bridge_module():
    bridge_path = Path(__file__).resolve().parents[2] / "tools" / "arena-connector" / "arena_mcp_bridge.py"
    spec = importlib.util.spec_from_file_location("arena_mcp_bridge", bridge_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_redaction_and_sigv4_contamination_detection():
    headers = {
        "Authorization": "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20261107/us-east-1/s3/aws4_request",
        "X-Amz-Date": "20261107T120000Z",
        "X-Amz-Security-Token": "super-secret-session-token",
        "Content-Type": "application/json",
    }
    redacted = execution_guard.redact_headers(headers)
    assert "AKIA" not in json.dumps(redacted)
    assert "super-secret-session-token" not in json.dumps(redacted)
    assert redacted["Authorization"] == "AWS4-HMAC-SHA256 [REDACTED_SIGV4]"

    contamination = execution_guard.detect_sigv4_contamination(headers)
    assert contamination is not None
    assert contamination["contaminated"] is True
    assert "x-amz-date" in contamination["amz_headers_present"]


@pytest.mark.asyncio
async def test_mcp_endpoint_rejects_sigv4_headers_and_exposes_health(api_client):
    # 1. SigV4 header leaked onto /mcp is cleanly rejected with JSON-RPC -32001
    bad_mcp = await api_client.post(
        "/mcp",
        headers={"Authorization": "AWS4-HMAC-SHA256 Credential=AKIA123/s3/aws4_request"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert bad_mcp.status_code == 400
    err = bad_mcp.json()["error"]
    assert err["code"] == -32001
    assert "Bearer" in err["message"]

    # 2. Unauthenticated /mcp/health checks clock skew
    now_ts = str(int(datetime.now(timezone.utc).timestamp()))
    h_res = await api_client.get("/mcp/health", headers={"X-Client-Timestamp": now_ts})
    assert h_res.status_code == 200
    assert h_res.json()["ok"] is True
    assert h_res.json()["credential_isolation_ok"] is True

    # 3. Authenticated /api/v1/mcp/execution-health returns full redacted diagnostics
    exec_res = await api_client.get(
        "/api/v1/mcp/execution-health",
        headers={"Authorization": "Bearer secret-token-value-12345", "X-Client-Timestamp": now_ts},
    )
    assert exec_res.status_code == 200
    body_str = json.dumps(exec_res.json())
    assert "secret-token-value-12345" not in body_str
    assert exec_res.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_reconcile_campaigns_25_26_and_54_scheduled_followups(api_client, api_db):
    """Reconciles active/scheduled Campaigns #25 & #26 and 54 pending opener follow-up messages so interrupted agents never replay them."""
    c25 = Campaign(
        id=25,
        name="Campaign #25 Active Outreach",
        channel="sms",
        status="running",
        total_contacts=120,
        messages_sent=66,
        messages_delivered=64,
        messages_failed=2,
    )
    c26 = Campaign(
        id=26,
        name="Campaign #26 Scheduled Batch",
        channel="sms",
        status="scheduled",
        total_contacts=80,
        messages_sent=0,
        scheduled_start_at=datetime.now(timezone.utc) + timedelta(hours=3),
    )
    api_db.add_all([c25, c26])
    await api_db.flush()

    api_db.add(
        CampaignFollowUp(
            campaign_id=25,
            step_order=1,
            name="Opener Follow-up Step 1",
            channel="sms",
            message_text="Quick follow-up on our note!",
            delay_minutes=1440,
            is_active=True,
        )
    )
    future_due = datetime.now(timezone.utc) + timedelta(hours=2)
    for idx in range(54):
        api_db.add(
            ScheduledMessage(
                phone_number=f"+234803000{idx:04d}",
                body="Scheduled opener follow-up",
                schedule_at=future_due,
                status="pending",
            )
        )
    await api_db.commit()

    rec_res = await api_client.get("/api/v1/campaigns/reconcile?campaign_ids=25,26")
    assert rec_res.status_code == 200, rec_res.text
    rec = rec_res.json()
    assert rec["active_or_pending_campaigns"] == 2
    assert rec["scheduled_messages"]["pending_count"] == 54

    by_id = {c["id"]: c for c in rec["campaigns"]}
    assert by_id[25]["already_active_or_sent"] is True
    assert by_id[25]["followup_rules_active"] == 1
    assert "DO_NOT_REPLAY_START" in by_id[25]["safe_action"]
    assert by_id[26]["already_active_or_sent"] is True
    assert "DO_NOT_REPLAY_START" in by_id[26]["safe_action"]


@pytest.mark.asyncio
async def test_campaign_idempotency_key_and_expected_status_guard(
    api_client, api_db, sms_gateway_configured, stub_queue
):
    # Seed audience list with one consented contact
    clist = ContactList(name="Idempotent Audience")
    contact = Contact(
        first_name="Ada",
        phone_number="+2348031234567",
        has_consented=True,
        consent_status="opted_in",
    )
    api_db.add_all([clist, contact])
    await api_db.flush()
    api_db.add(ContactListMember(list_id=clist.id, contact_id=contact.id))
    await api_db.commit()

    # 1. Duplicate POST /api/v1/campaigns/ with same X-Idempotency-Key creates ONLY ONE row
    payload = {
        "name": "Guarded Launch",
        "channel": "sms",
        "list_id": clist.id,
        "message_body": "Hello {{first_name}}, reply STOP to opt out.",
    }
    r1 = await api_client.post(
        "/api/v1/campaigns/",
        json=payload,
        headers={"X-Idempotency-Key": "create-guarded-launch-001"},
    )
    r2 = await api_client.post(
        "/api/v1/campaigns/",
        json=payload,
        headers={"X-Idempotency-Key": "create-guarded-launch-001"},
    )
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 201, r2.text
    assert r1.json()["id"] == r2.json()["id"]
    camp_id = r1.json()["id"]

    # 2. Starting with wrong expected_status fails with 409 before mutating
    conflict = await api_client.post(f"/api/v1/campaigns/{camp_id}/start?expected_status=paused")
    assert conflict.status_code == 409

    # 3. Starting with X-Idempotency-Key enqueues ONCE even when replayed
    s1 = await api_client.post(
        f"/api/v1/campaigns/{camp_id}/start?expected_status=draft",
        headers={"X-Idempotency-Key": "start-guarded-launch-001"},
    )
    s2 = await api_client.post(
        f"/api/v1/campaigns/{camp_id}/start",
        headers={"X-Idempotency-Key": "start-guarded-launch-001"},
    )
    assert s1.status_code == 200, s1.text
    assert s2.status_code == 200, s2.text
    assert s2.json().get("replayed") is True
    assert len(stub_queue) == 1


def test_arena_mcp_bridge_retries_reads_only_and_never_replays_mutations(monkeypatch):
    bridge_mod = _load_bridge_module()

    assert bridge_mod.is_safe_to_retry({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) is True
    assert (
        bridge_mod.is_safe_to_retry(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "reconcile_campaign_state", "arguments": {"campaign_ids": "25,26"}},
            }
        )
        is True
    )
    assert (
        bridge_mod.is_safe_to_retry(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "start_campaign", "arguments": {"campaign_id": 25}},
            }
        )
        is False
    )

    monkeypatch.setattr(bridge_mod.time, "sleep", lambda *_: None)
    calls = {"read": 0, "write": 0}
    outputs: list[dict] = []
    monkeypatch.setattr(bridge_mod, "_write", lambda payload: outputs.append(payload))

    bridge = bridge_mod.Bridge(
        url="https://sendersms.example.test/connectors/arena/mcp",
        headers={"Content-Type": "application/json", "Authorization": "Bearer test-secret-token"},
    )

    # Read-only call fails once with URLError then succeeds on retry #2
    def _flaky_read_urlopen(req, timeout=120):
        calls["read"] += 1
        if calls["read"] == 1:
            raise urllib.error.URLError("temporary DNS reset")

        class _Resp:
            status = 200
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"jsonrpc": "2.0", "id": 10, "result": {"tools": []}}).encode()

        return _Resp()

    monkeypatch.setattr(bridge_mod.urllib.request, "urlopen", _flaky_read_urlopen)
    bridge.send({"jsonrpc": "2.0", "id": 10, "method": "tools/list"})
    assert calls["read"] == 2
    assert outputs[-1]["id"] == 10
    assert "result" in outputs[-1]

    # Mutating call (start_campaign) fails on URLError -> MUST NOT retry (attempts == 1) and flags reconcile_required=True
    def _failing_write_urlopen(req, timeout=120):
        calls["write"] += 1
        raise urllib.error.URLError("socket closed mid-flight")

    monkeypatch.setattr(bridge_mod.urllib.request, "urlopen", _failing_write_urlopen)
    bridge.send(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "start_campaign", "arguments": {"campaign_id": 25}},
        }
    )
    assert calls["write"] == 1
    assert outputs[-1]["error"]["data"]["reconcile_required"] is True
