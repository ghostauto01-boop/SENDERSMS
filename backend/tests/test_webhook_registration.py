"""Webhook registration must tolerate events the gateway does not support.

Regression test for a production failure: the gateway rejected registration
outright. The real cause was that registration was all-or-nothing - a single
optional event refused with HTTP 400 ("unsupported event") made the whole call
report failure and the API return 502, even though sms:received had registered
successfully and inbound SMS was working.

Not every account, app version or device supports MMS / data-SMS / cancelled
events, so those must be best-effort. Only sms:received is required.
"""

import httpx
import pytest

from app.providers import smsgate


class _MockGate:
    """Minimal stand-in for the SMS-Gate cloud API.

    Accepts only the events in `supported`; everything else is rejected with
    HTTP 400, exactly as the real gateway does.
    """

    def __init__(self, supported):
        self.supported = set(supported)
        self.registered = {}
        self.deleted = []
        self._n = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith("/webhooks"):
            return httpx.Response(200, json=list(self.registered.values()))
        if request.method == "POST" and path.endswith("/webhooks"):
            import json as _json

            body = _json.loads(request.content or b"{}")
            event = body.get("event")
            # The real API takes ONE event per registration, never a list.
            assert isinstance(event, str), "event must be a singular string"
            assert "url" in body
            if event not in self.supported:
                return httpx.Response(400, json={"message": f"unsupported event: {event}"})
            self._n += 1
            wid = f"wh{self._n}"
            rec = {"id": wid, "url": body["url"], "event": event}
            self.registered[wid] = rec
            return httpx.Response(201, json=rec)
        if request.method == "DELETE":
            wid = path.rsplit("/", 1)[-1]
            self.deleted.append(wid)
            self.registered.pop(wid, None)
            return httpx.Response(204)
        return httpx.Response(404, json={"message": "not found"})


@pytest.fixture
def gate_env(monkeypatch):
    monkeypatch.setattr(smsgate.settings, "SMSGATE_USERNAME", "u", raising=False)
    monkeypatch.setattr(smsgate.settings, "SMSGATE_PASSWORD", "p", raising=False)
    monkeypatch.setattr(
        smsgate.settings, "SMSGATE_BASE_URL", "https://gw.example/3rdparty/v1", raising=False
    )


def _install(monkeypatch, mock):
    real_client = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(mock.handler)
        return real_client(*a, **kw)

    monkeypatch.setattr(smsgate.httpx, "AsyncClient", factory)


URL = "https://app.example.com/api/v1/webhooks/smsgateway"


@pytest.mark.asyncio
async def test_optional_events_rejected_still_succeeds(gate_env, monkeypatch):
    """MMS/data-SMS refused by the gateway must not fail registration."""
    mock = _MockGate({"sms:received", "sms:sent", "sms:delivered", "sms:failed"})
    _install(monkeypatch, mock)

    r = await smsgate.register_webhook_direct(URL)

    assert r["success"] is True, r
    assert "sms:received" in r["registered"]
    # The refusals are still surfaced rather than hidden.
    assert set(r["unsupported"]) == {
        "sms:data-received",
        "mms:received",
        "mms:downloaded",
        "sms:cancelled",
    }
    assert not r["missing_required"]


@pytest.mark.asyncio
async def test_missing_required_event_fails(gate_env, monkeypatch):
    """If sms:received itself is refused, that IS a real failure."""
    mock = _MockGate({"sms:sent", "sms:delivered"})
    _install(monkeypatch, mock)

    r = await smsgate.register_webhook_direct(URL)

    assert r["success"] is False
    assert r["missing_required"] == ["sms:received"]


@pytest.mark.asyncio
async def test_all_events_supported(gate_env, monkeypatch):
    """A fully capable gateway registers everything with no complaints."""
    mock = _MockGate(set(smsgate.DEFAULT_EVENTS))
    _install(monkeypatch, mock)

    r = await smsgate.register_webhook_direct(URL)

    assert r["success"] is True
    assert set(r["registered"]) == set(smsgate.DEFAULT_EVENTS)
    assert r["unsupported"] == []
    assert r["errors"] == []


@pytest.mark.asyncio
async def test_registration_is_idempotent(gate_env, monkeypatch):
    """Re-registering keeps existing hooks instead of duplicating them."""
    mock = _MockGate(set(smsgate.DEFAULT_EVENTS))
    _install(monkeypatch, mock)

    first = await smsgate.register_webhook_direct(URL)
    count_after_first = len(mock.registered)
    second = await smsgate.register_webhook_direct(URL)

    assert first["success"] and second["success"]
    assert len(mock.registered) == count_after_first, "should not duplicate"
    assert second["created"] == []
    assert set(second["kept"]) == set(smsgate.DEFAULT_EVENTS)


@pytest.mark.asyncio
async def test_stale_url_is_removed(gate_env, monkeypatch):
    """A registration pointing at an old deployment URL is cleaned up."""
    mock = _MockGate(set(smsgate.DEFAULT_EVENTS))
    mock.registered["old1"] = {
        "id": "old1",
        "url": "https://old-deploy.onrender.com/api/v1/webhooks/smsgateway",
        "event": "sms:received",
    }
    _install(monkeypatch, mock)

    r = await smsgate.register_webhook_direct(URL)

    assert "old1" in mock.deleted
    assert r["success"] is True


@pytest.mark.asyncio
async def test_auth_failure_reports_credentials_not_missing_events(monkeypatch):
    """A 401 from the gateway must be reported as a credentials problem.

    Regression for the live deploy that showed eight identical
    'HTTP 401 Unauthorized' lines and blamed the events instead of the
    username/password that actually caused them.
    """
    calls = []

    class R:
        status_code = 401
        text = '{"message":"Unauthorized"}'

        def json(self):
            return {"message": "Unauthorized"}

    class C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            calls.append("get")
            return R()

        async def post(self, *a, **k):
            calls.append("post")
            return R()

    monkeypatch.setattr(smsgate.httpx, "AsyncClient", lambda *a, **k: C())
    monkeypatch.setattr(smsgate, "_creds", lambda: ("user", "badpass"))

    r = await smsgate.register_webhook_direct("https://x.example.com/api/v1/webhooks/smsgateway")

    assert r["success"] is False
    assert r.get("auth_failed") is True
    # Short-circuits: does not fire eight doomed POSTs.
    assert "post" not in calls
    assert "SMSGATE_USERNAME" in r["error"] and "SMSGATE_PASSWORD" in r["error"]
