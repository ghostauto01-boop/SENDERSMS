"""Tests for SMS-Gate webhook signature verification.

Regression cover for the unauthenticated-webhook vulnerability: anyone could
POST a forged sms:received event and inject fake conversations.
"""

import hashlib
import hmac
import time

import pytest

from app.providers.smsgate import SMSGateProvider

SECRET = "test-signing-key"


def sign(body: bytes, timestamp: str, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body + timestamp.encode(), hashlib.sha256).hexdigest()


class TestWebhookSignature:
    def test_valid_signature_accepted(self):
        body = b'{"event":"sms:received","payload":{"message":"hi"}}'
        ts = str(int(time.time()))
        assert SMSGateProvider.validate_webhook_signature(body, sign(body, ts), ts, SECRET)

    def test_tampered_body_rejected(self):
        body = b'{"event":"sms:received","payload":{"message":"hi"}}'
        ts = str(int(time.time()))
        signature = sign(body, ts)
        tampered = b'{"event":"sms:received","payload":{"message":"INJECTED"}}'
        assert not SMSGateProvider.validate_webhook_signature(tampered, signature, ts, SECRET)

    def test_wrong_secret_rejected(self):
        body = b'{"event":"system:ping"}'
        ts = str(int(time.time()))
        signature = sign(body, ts, "attacker-guess")
        assert not SMSGateProvider.validate_webhook_signature(body, signature, ts, SECRET)

    def test_missing_signature_rejected(self):
        body = b'{"event":"system:ping"}'
        ts = str(int(time.time()))
        assert not SMSGateProvider.validate_webhook_signature(body, "", ts, SECRET)

    def test_missing_secret_rejected(self):
        body = b'{"event":"system:ping"}'
        ts = str(int(time.time()))
        assert not SMSGateProvider.validate_webhook_signature(body, sign(body, ts), ts, "")

    def test_replayed_old_timestamp_rejected(self):
        """A captured-and-replayed request must not stay valid forever."""
        body = b'{"event":"sms:received"}'
        old = str(int(time.time()) - 3600)
        assert not SMSGateProvider.validate_webhook_signature(body, sign(body, old), old, SECRET)

    def test_future_timestamp_rejected(self):
        body = b'{"event":"sms:received"}'
        future = str(int(time.time()) + 3600)
        assert not SMSGateProvider.validate_webhook_signature(
            body, sign(body, future), future, SECRET
        )

    def test_non_numeric_timestamp_rejected(self):
        body = b'{"event":"sms:received"}'
        assert not SMSGateProvider.validate_webhook_signature(body, "deadbeef", "not-a-time", SECRET)

    def test_signature_is_case_insensitive_hex(self):
        body = b'{"event":"system:ping"}'
        ts = str(int(time.time()))
        assert SMSGateProvider.validate_webhook_signature(
            body, sign(body, ts).upper(), ts, SECRET
        )

    def test_string_body_accepted(self):
        """Callers passing str instead of bytes should still verify."""
        body = '{"event":"system:ping"}'
        ts = str(int(time.time()))
        signature = sign(body.encode(), ts)
        assert SMSGateProvider.validate_webhook_signature(body, signature, ts, SECRET)
