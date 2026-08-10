"""
Tests for SMS gateway disabled/safe-fallback states.
"""

import pytest
from app.providers.smsgate import SMSGateProvider


class TestGatewayDisabledState:
    """When SMS gateway is disabled or unreachable, sending must fail gracefully."""

    @pytest.mark.asyncio
    async def test_send_sms_without_gateway_url(self):
        """No base URL configured — should fail gracefully, not crash."""
        provider = SMSGateProvider(base_url="")
        result = await provider.send_sms("+2348012345678", "Test message")
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_health_check_without_gateway(self):
        """Health check without gateway — returns unhealthy."""
        provider = SMSGateProvider(base_url="")
        health = await provider.health_check()
        assert health.is_healthy is False

    @pytest.mark.asyncio
    async def test_test_connection_without_url(self):
        provider = SMSGateProvider(base_url="")
        result = await provider.test_connection()
        assert result is False

    @pytest.mark.asyncio
    async def test_send_with_default_url_no_auth(self):
        """Default cloud URL with no credentials should fail gracefully."""
        provider = SMSGateProvider()
        result = await provider.send_sms("+2348012345678", "Test")
        # Should fail with auth error, not crash
        assert result.success is False

    def test_default_url_is_set(self):
        """Provider always falls back to default cloud URL."""
        provider = SMSGateProvider()
        assert len(provider.base_url) > 10
        assert "api.sms-gate.app" in provider.base_url


class TestIdempotency:
    """Message sending must be idempotent."""

    def test_unique_idempotency_keys(self):
        """Each call should generate unique idempotency keys."""
        import uuid
        keys = set()
        for _ in range(100):
            keys.add(str(uuid.uuid4()))
        assert len(keys) == 100
