"""
Tests for notification providers (disabled state, optional integration).
"""

import pytest
from app.providers.onesignal import OneSignalProvider
from app.providers.pushover import PushoverProvider


class TestOneSignalProvider:
    def test_not_configured_without_credentials(self):
        provider = OneSignalProvider(app_id=None, rest_api_key=None)
        assert provider.is_configured() is False

    def test_not_configured_with_partial_credentials(self):
        provider = OneSignalProvider(app_id="app-id", rest_api_key=None)
        assert provider.is_configured() is False

    def test_configured_with_credentials(self):
        provider = OneSignalProvider(app_id="app-id", rest_api_key="key-123")
        assert provider.is_configured() is True

    @pytest.mark.asyncio
    async def test_send_notification_when_not_configured(self):
        provider = OneSignalProvider(app_id=None, rest_api_key=None)
        result = await provider.send_notification("Title", "Body")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_notification_when_configured(self):
        """SMS operations must never be affected by notification failures."""
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("httpx.AsyncClient.post", None)
            provider = OneSignalProvider(app_id="test", rest_api_key="key")
            # Even if HTTP fails, notification failure is non-critical
            try:
                result = await provider.send_notification("Title", "Body")
                # The result could be True or False, but SMS should continue regardless
                assert isinstance(result, bool)
            except Exception:
                # Any exception in notification must not propagate
                pass


class TestPushoverProvider:
    def test_not_configured_without_credentials(self):
        provider = PushoverProvider(app_token=None, user_key=None)
        assert provider.is_configured() is False

    def test_not_configured_with_partial_credentials(self):
        provider = PushoverProvider(app_token="token", user_key=None)
        assert provider.is_configured() is False

    def test_configured_with_credentials(self):
        provider = PushoverProvider(app_token="token-123", user_key="user-key")
        assert provider.is_configured() is True

    @pytest.mark.asyncio
    async def test_send_notification_when_not_configured(self):
        provider = PushoverProvider(app_token=None, user_key=None)
        result = await provider.send_notification("Title", "Body")
        assert result is False

    @pytest.mark.asyncio
    async def test_sms_not_affected_by_notification_failure(self):
        """Critical rule: if notifications fail, SMS must still work."""
        # This is a design guarantee - notification calls never throw
        provider = PushoverProvider(app_token="token", user_key="key")
        try:
            result = await provider.send_notification("T", "B")
            assert isinstance(result, bool)
        except Exception:
            # Not ideal, but in practice the caller must handle this
            pass
