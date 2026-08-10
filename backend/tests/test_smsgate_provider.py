import pytest
from app.providers.smsgate import SMSGateProvider
from app.providers.base import InboundMessage, DeliveryStatus

class TestSMSGateProvider:
    def setup_method(self):
        self.provider = SMSGateProvider(username="testuser", password="testpass")

    def test_provider_initialization(self):
        assert self.provider.username == "testuser"
        assert "api.sms-gate.app" in self.provider.base_url

    def test_supports_webhooks(self): assert self.provider.supports_webhooks() is True
    def test_supports_polling(self): assert self.provider.supports_polling() is True
    def test_sim_number_default(self): assert self.provider.sim_number == 1

    def test_send_sms_no_url(self):
        p = SMSGateProvider(base_url="", username="u", password="p")
        import asyncio
        loop = asyncio.new_event_loop()
        actual = loop.run_until_complete(p.send_sms("+2348012345678", "Test"))
        loop.close()
        assert actual.success is False

    def test_auth_present(self):
        auth = self.provider._auth()
        assert auth is not None
        assert len(auth) > 5

    def test_parse_inbound(self):
        raw = {"id": "msg-456", "from": "+2348012345678", "text": "Hello", "sentAt": "2024-01-15T12:00:00Z"}
        msg = self.provider.parse_inbound_message(raw)
        assert msg.provider_message_id == "msg-456"
        assert msg.from_number == "+2348012345678"
        assert msg.body == "Hello"

    def test_parse_delivery(self):
        raw = {"id": "msg-789", "status": "delivered", "phone": "+2348012345678"}
        st = self.provider.parse_delivery_status(raw)
        assert st.status == "delivered"

    def test_parse_failed(self):
        st = self.provider.parse_delivery_status({"id":"x","status":"failed"})
        assert st.status == "failed"

    def test_default_url(self):
        p = SMSGateProvider()
        assert "api.sms-gate.app" in p.base_url
