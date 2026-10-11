"""Sprint 2 — C6: the same numbers, wherever you ask.

The QA sweep found ``dashboard_stats``, ``analytics_overview``, ``email_stats`` and
``email_overview`` disagreeing about one database, and ``send_history`` listing email rows.
Reproduced. They differed in four independent ways:

* SCOPE   ``analytics_overview`` is labelled "SMS" but had no channel filter, so email rows
          inflated it; the dashboard was all-time, analytics 30 days, email 29 days (`days - 1`);
* WINDOW  three different cut-offs for "the last 30 days";
* DELIVERED  the dashboard counted ``status == delivered``; the email numbers also counted a
          message with a ``delivered_at`` or an ``opened_at`` (you cannot open mail that was not delivered);
* FAILED  ``overview`` counted ``failed``; ``funnel`` and ``timeseries`` also counted ``cancelled``
          (a message nobody tried to send is not a failed send), so the Analytics page's headline
          disagreed with its own charts.

One module (``services/message_metrics``) now holds the definitions, and every one of those
endpoints asks it. The dataset below has a known answer for every number.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.models.contact import Contact
from app.models.conversation import Conversation, Message


def _ago(**delta):
    return datetime.now(timezone.utc) - timedelta(**delta)


@pytest_asyncio.fixture
async def data(api_db):
    """SMS: 11 outgoing (6 delivered -- one only by delivered_at --, 2 sent, 1 failed, 1 queued,
    1 cancelled) and 3 replies. Email: 8 outgoing (5 delivered -- one only because it was opened --,
    1 sent, 1 failed, 1 queued) and 2 replies. Plus one old (60 days) message on each channel."""
    contact = Contact(first_name="Ada", phone_number="+2348031200001", email="ada@leadco.io")
    api_db.add(contact)
    await api_db.flush()
    convs = {}
    for channel in ("sms", "email"):
        convs[channel] = Conversation(contact_id=contact.id, channel=channel, status="active")
        api_db.add(convs[channel])
    await api_db.flush()
    n = iter(range(1, 1000))

    def msg(channel, direction="outgoing", status="sent", age=None, **kw):
        return Message(
            conversation_id=convs[channel].id, contact_id=contact.id, channel=channel,
            direction=direction, status=status, body="x", idempotency_key=f"m{next(n)}",
            provider="smsgate" if channel == "sms" else "brevo",
            created_at=_ago(**(age or {"hours": 1})), **kw)

    rows = []
    rows += [msg("sms", status="delivered") for _ in range(5)]
    rows += [msg("sms", status="sent", delivered_at=_ago(minutes=30))]          # delivered by its receipt
    rows += [msg("sms", status="sent") for _ in range(2)]
    rows += [msg("sms", status="failed"), msg("sms", status="queued"), msg("sms", status="cancelled")]
    rows += [msg("sms", direction="incoming", status="received") for _ in range(3)]
    rows += [msg("email", status="delivered") for _ in range(4)]
    rows += [msg("email", status="sent", opened_at=_ago(minutes=20), open_count=2)]   # delivered, opened
    rows += [msg("email", status="sent"), msg("email", status="failed"), msg("email", status="queued")]
    rows += [msg("email", direction="incoming", status="received") for _ in range(2)]
    rows += [msg("sms", status="delivered", age={"days": 60}), msg("email", status="delivered", age={"days": 60})]
    api_db.add_all(rows)
    await api_db.flush()
    return contact


SMS = {"sent": 11, "delivered": 6, "failed": 1, "queued": 1, "replies": 3}
EMAIL = {"sent": 8, "delivered": 5, "failed": 1, "queued": 1, "replies": 2}


def _pick(payload, keys):
    return {k: payload[k] for k in keys}


async def _get(client, path, **params):
    response = await client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_the_dashboard_and_the_sms_analytics_agree_for_the_same_scope(api_client, data):
    dash = await _get(api_client, "/api/v1/dashboard/stats", channel="sms", days=30)
    analytics = await _get(api_client, "/api/v1/analytics/overview", channel="sms", days=30)

    assert dash["messages_sent"] == analytics["sent"] == SMS["sent"]
    assert dash["messages_delivered"] == analytics["delivered"] == SMS["delivered"]
    assert dash["messages_failed"] == analytics["failed"] == SMS["failed"]
    assert dash["replies"] == analytics["replies"] == SMS["replies"]
    assert dash["delivery_rate"] == analytics["delivery_rate"]
    assert dash["reply_rate"] == analytics["reply_rate"]


@pytest.mark.asyncio
async def test_sms_analytics_does_not_count_email(api_client, data):
    """It is labelled SMS and the page says "you sent N SMS messages"; email rows used to be in N."""
    analytics = await _get(api_client, "/api/v1/analytics/overview", days=30)   # no channel given

    assert analytics["sent"] == SMS["sent"], "default scope is SMS"
    assert analytics["scope"]["channel"] == "sms"
    everything = await _get(api_client, "/api/v1/analytics/overview", days=30, channel="all")
    assert everything["sent"] == SMS["sent"] + EMAIL["sent"]


@pytest.mark.asyncio
async def test_the_dashboard_and_both_email_endpoints_agree(api_client, data):
    dash = await _get(api_client, "/api/v1/dashboard/stats", channel="email", days=30)
    stats = await _get(api_client, "/api/v1/email/stats", days=30)
    overview = (await _get(api_client, "/api/v1/email/overview", days=30))["totals"]

    for source in (stats, overview):
        assert _pick(source, EMAIL) == EMAIL
    assert dash["messages_sent"] == EMAIL["sent"]
    assert dash["messages_delivered"] == EMAIL["delivered"] and dash["messages_failed"] == EMAIL["failed"]
    assert dash["replies"] == EMAIL["replies"]
    assert stats["opened"] == 1


@pytest.mark.asyncio
async def test_all_channels_is_the_sum_of_the_two(api_client, data):
    everything = await _get(api_client, "/api/v1/dashboard/stats", channel="all", days=30)

    assert everything["messages_sent"] == SMS["sent"] + EMAIL["sent"]
    assert everything["messages_delivered"] == SMS["delivered"] + EMAIL["delivered"]
    assert everything["messages_failed"] == SMS["failed"] + EMAIL["failed"]
    assert everything["replies"] == SMS["replies"] + EMAIL["replies"]


@pytest.mark.asyncio
async def test_the_dashboard_stays_all_time_unless_given_a_window(api_client, data):
    all_time = await _get(api_client, "/api/v1/dashboard/stats", channel="sms")
    windowed = await _get(api_client, "/api/v1/dashboard/stats", channel="sms", days=30)

    assert all_time["messages_sent"] == SMS["sent"] + 1 and all_time["messages_delivered"] == SMS["delivered"] + 1
    assert windowed["messages_sent"] == SMS["sent"]
    assert all_time["scope"]["days"] is None and windowed["scope"]["days"] == 30


@pytest.mark.asyncio
async def test_a_cancelled_message_is_not_a_failed_one_anywhere(api_client, data):
    overview = await _get(api_client, "/api/v1/analytics/overview", channel="sms", days=30)
    funnel = await _get(api_client, "/api/v1/analytics/funnel", channel="sms", days=30)
    series = await _get(api_client, "/api/v1/analytics/timeseries", channel="sms", days=30)

    assert overview["failed"] == funnel["failed"] == sum(p["failed"] for p in series["points"]) == 1
    assert funnel["cancelled"] == 1, "reported on its own, not folded into failures"
    assert funnel["stages"][0]["count"] == sum(p["sent"] for p in series["points"]) == SMS["sent"]
    assert funnel["stages"][1]["count"] == sum(p["delivered"] for p in series["points"]) == SMS["delivered"]


@pytest.mark.asyncio
async def test_every_endpoint_cuts_the_window_at_the_same_instant(api_client, api_db, data):
    """A message just inside / just outside "the last N days" is in or out of ALL of them."""
    from app.services.message_metrics import window_start

    edge = window_start(7)
    convs = (await api_db.execute(__import__("sqlalchemy").select(Conversation))).scalars().all()
    sms_conv = next(c for c in convs if c.channel == "sms")
    for i, when in enumerate((edge + timedelta(seconds=5), edge - timedelta(seconds=5))):
        api_db.add(Message(conversation_id=sms_conv.id, contact_id=data.id, channel="sms", direction="outgoing",
                           status="delivered", body="edge", idempotency_key=f"edge{i}", provider="smsgate",
                           created_at=when))
    await api_db.flush()
    inside_before = (await _get(api_client, "/api/v1/dashboard/stats", channel="sms", days=7))["messages_sent"]

    dash = await _get(api_client, "/api/v1/dashboard/stats", channel="sms", days=7)
    analytics = await _get(api_client, "/api/v1/analytics/overview", channel="sms", days=7)
    series = await _get(api_client, "/api/v1/analytics/timeseries", channel="sms", days=7)

    assert dash["messages_sent"] == analytics["sent"] == sum(p["sent"] for p in series["points"]) == inside_before


@pytest.mark.asyncio
async def test_each_endpoint_says_what_scope_and_definitions_its_numbers_use(api_client, data):
    dash = await _get(api_client, "/api/v1/dashboard/stats", channel="sms", days=30)
    analytics = await _get(api_client, "/api/v1/analytics/overview", days=30)
    stats = await _get(api_client, "/api/v1/email/stats", days=30)

    for payload in (dash, analytics, stats):
        assert payload["scope"]["channel"] in ("sms", "email", "all")
        assert {"sent", "delivered", "failed", "queued", "replies"} <= set(payload["definitions"])
    assert dash["definitions"] == analytics["definitions"] == stats["definitions"], "one wording, everywhere"


@pytest.mark.asyncio
async def test_send_history_lists_sms_by_default_not_email(api_client, data):
    sms = await _get(api_client, "/api/v1/send/history", per_page=100)
    email = await _get(api_client, "/api/v1/send/history", per_page=100, channel="email")
    everything = await _get(api_client, "/api/v1/send/history", per_page=100, channel="all")

    assert sms["total"] == SMS["sent"] + 1          # + the old one; history has no window
    assert email["total"] == EMAIL["sent"] + 1
    assert everything["total"] == sms["total"] + email["total"]
    assert all(item.get("channel", "sms") == "sms" for item in sms["items"])


@pytest.mark.asyncio
async def test_send_history_rejects_a_bogus_channel(api_client, data):
    response = await api_client.get("/api/v1/send/history", params={"channel": "whatsapp"})

    assert response.status_code == 422 and "whatsapp" in response.json()["message"]


@pytest.mark.asyncio
async def test_the_assistant_tools_can_ask_for_a_scope(monkeypatch):
    from app.mcp.registry import TOOLS_BY_NAME

    dash = {p.name for p in TOOLS_BY_NAME["dashboard_stats"].params}
    analytics = {p.name for p in TOOLS_BY_NAME["analytics_overview"].params}
    history = {p.name for p in TOOLS_BY_NAME["send_history"].params}

    assert {"channel", "days"} <= dash
    assert {"channel", "days"} <= analytics
    assert "channel" in history
