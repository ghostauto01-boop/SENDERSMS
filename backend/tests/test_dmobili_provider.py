"""Tests for the Dmobili.com (Pace platform) gateway adapter.

Covers response parsing (the wire format is private, so the parser must be
defensive), DLR mapping, number formatting, and the module-level API's
no-credentials behaviour.
"""

import pytest

from app.config import settings
from app.providers.dmobili import (
    DmobiliProvider,
    format_number,
    map_dlr_status,
    parse_send_response,
)


class TestFormatNumber:
    def test_strips_plus_from_e164(self):
        assert format_number("+2348031234567") == "2348031234567"

    def test_local_leading_zero_becomes_234(self):
        assert format_number("08031234567") == "2348031234567"

    def test_double_zero_prefix(self):
        assert format_number("002348031234567") == "2348031234567"

    def test_already_prefixed(self):
        assert format_number("2348031234567") == "2348031234567"


class TestMapDlrStatus:
    @pytest.mark.parametrize("raw,expected", [
        ("DELIVERED", "delivered"),
        ("delivered", "delivered"),
        ("PENDING", "sent"),
        ("EXPIRED", "failed"),
        ("REJECTED", "failed"),
        ("UNDELIVERABLE", "failed"),
        ("HANDSET_ERRORS", "failed"),
        ("USER_ERRORS", "failed"),
        ("OPERATOR_ERRORS", "failed"),
        ("", "unknown"),
        ("weird-state", "unknown"),
    ])
    def test_vocabulary(self, raw, expected):
        assert map_dlr_status(raw) == expected


class TestParseSendResponse:
    def test_json_success_with_message_id(self):
        r = parse_send_response(200, '{"status": "success", "message_id": "abc123"}')
        assert r["success"] is True
        assert r["provider_message_id"] == "abc123"

    def test_json_success_flag_only(self):
        r = parse_send_response(200, '{"success": true, "status": "1"}')
        assert r["success"] is True

    def test_json_error_field(self):
        r = parse_send_response(200, '{"error": "Insufficient credit"}')
        assert r["success"] is False
        assert "Insufficient credit" in r["error"]

    def test_json_failed_status(self):
        r = parse_send_response(200, '{"status": "failed", "message": "bad sender id"}')
        assert r["success"] is False
        assert "bad sender id" in r["error"]

    def test_code_detail_success(self):
        r = parse_send_response(200, "1701:MSG-98765")
        assert r["success"] is True
        assert r["provider_message_id"] == "MSG-98765"

    def test_code_detail_failure(self):
        r = parse_send_response(200, "1804:Insufficient credit")
        assert r["success"] is False
        assert "1804" in r["error"]

    def test_plain_text_error_vocabulary(self):
        r = parse_send_response(200, "ERROR: Invalid sender id")
        assert r["success"] is False

    def test_plain_id_body(self):
        r = parse_send_response(200, "8f2f6b1c-0d9e-4f5a-9b8c-7d6e5f4a3b2c")
        assert r["success"] is True
        assert r["provider_message_id"] == "8f2f6b1c-0d9e-4f5a-9b8c-7d6e5f4a3b2c"

    def test_http_500(self):
        r = parse_send_response(500, "gateway exploded")
        assert r["success"] is False
        assert "500" in r["error"]

    def test_http_401(self):
        r = parse_send_response(401, "unauthorized")
        assert r["success"] is False
        assert "Authentication" in r["error"]

    def test_empty_body_fails(self):
        r = parse_send_response(200, "")
        assert r["success"] is False


class TestNoCredentials:
    @pytest.mark.asyncio
    async def test_send_refused_without_credentials(self, monkeypatch):
        monkeypatch.setattr(settings, "DMOBILI_USERNAME", None, raising=False)
        monkeypatch.setattr(settings, "DMOBILI_PASSWORD", None, raising=False)
        monkeypatch.setattr(settings, "DMOBILI_API_TOKEN", None, raising=False)
        from app.providers.dmobili import send_sms_direct

        r = await send_sms_direct("+2348031234567", "hello")
        assert r["success"] is False
        assert "credentials" in r["error"].lower()

    @pytest.mark.asyncio
    async def test_test_connection_reports_missing_credentials(self, monkeypatch):
        monkeypatch.setattr(settings, "DMOBILI_USERNAME", None, raising=False)
        monkeypatch.setattr(settings, "DMOBILI_PASSWORD", None, raising=False)
        monkeypatch.setattr(settings, "DMOBILI_API_TOKEN", None, raising=False)
        from app.providers.dmobili import test_connection_direct

        r = await test_connection_direct()
        assert r["success"] is False


class TestProviderClass:
    def test_parse_inbound_message(self):
        p = DmobiliProvider()
        msg = p.parse_inbound_message({
            "from": "+2348031111111",
            "to": "2348099999999",
            "message": "Hello back",
            "message_id": "in-1",
            "timestamp": "1700000000",
        })
        assert msg.from_number == "+2348031111111"
        assert msg.body == "Hello back"
        assert msg.provider_message_id == "in-1"
        assert msg.received_at is not None

    def test_parse_delivery_status(self):
        p = DmobiliProvider()
        st = p.parse_delivery_status({"message_id": "m1", "status": "DELIVERED"})
        assert st.status == "delivered"
        assert st.provider_message_id == "m1"

    def test_supports_flags(self):
        p = DmobiliProvider()
        assert p.supports_webhooks() is True

    def test_callback_secret_check(self, monkeypatch):
        monkeypatch.setattr(settings, "DMOBILI_WEBHOOK_SECRET", "s3cret", raising=False)
        assert DmobiliProvider.validate_callback_secret("s3cret") is True
        assert DmobiliProvider.validate_callback_secret("wrong") is False
        assert DmobiliProvider.validate_callback_secret("") is False
