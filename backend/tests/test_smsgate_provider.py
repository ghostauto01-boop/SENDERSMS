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
        """Real sms:received envelope — payload uses sender/message, not from/text.

        https://docs.sms-gate.app/features/webhooks/
        """
        raw = {
            "deviceId": "dev-1",
            "event": "sms:received",
            "id": "wh-evt-1",
            "webhookId": "wh-1",
            "payload": {
                "messageId": "msg-456",
                "message": "Hello",
                "sender": "+2348012345678",
                "recipient": "+2349099999999",
                "simNumber": 1,
                "receivedAt": "2024-01-15T12:00:00Z",
            },
        }
        msg = self.provider.parse_inbound_message(raw)
        assert msg.provider_message_id == "msg-456"
        assert msg.from_number == "+2348012345678"
        assert msg.to_number == "+2349099999999"
        assert msg.body == "Hello"
        assert msg.received_at.year == 2024

    def test_parse_inbound_mms_downloaded(self):
        raw = {
            "event": "mms:downloaded",
            "payload": {
                "messageId": "mms-1", "sender": "+2348012345678",
                "subject": "Photo", "body": "look",
                "attachments": [{"name": "img.jpg", "mimeType": "image/jpeg"}],
            },
        }
        msg = self.provider.parse_inbound_message(raw)
        assert "[Photo]" in msg.body and "look" in msg.body
        assert "attachment: img.jpg" in msg.body

    def test_parse_delivery(self):
        """Status is carried by the event NAME; payloads have no `status` key."""
        raw = {"event": "sms:delivered",
               "payload": {"messageId": "msg-789", "deliveredAt": "2024-01-15T12:00:00Z"}}
        st = self.provider.parse_delivery_status(raw)
        assert st.status == "delivered"
        assert st.provider_message_id == "msg-789"

    def test_parse_failed(self):
        st = self.provider.parse_delivery_status(
            {"event": "sms:failed", "payload": {"messageId": "x", "reason": "no service"}}
        )
        assert st.status == "failed"
        assert st.error == "no service"

    def test_parse_cancelled(self):
        st = self.provider.parse_delivery_status(
            {"event": "sms:cancelled", "payload": {"messageId": "x"}}
        )
        assert st.status == "cancelled"

    def test_parse_delivery_polled_state(self):
        """Falls back to the `state` field when polling instead of webhooking."""
        st = self.provider.parse_delivery_status({"id": "x", "state": "Delivered"})
        assert st.status == "delivered"

    def test_default_events_cover_every_consumed_event(self):
        """Every event the webhook route handles must actually be registered."""
        from app.providers.smsgate import DEFAULT_EVENTS
        from app.api.v1.webhooks import INBOUND_EVENTS, STATUS_EVENTS
        for e in tuple(INBOUND_EVENTS) + tuple(STATUS_EVENTS):
            assert e in DEFAULT_EVENTS, f"{e} handled but never registered"

    def test_default_url(self):
        p = SMSGateProvider()
        assert "api.sms-gate.app" in p.base_url
