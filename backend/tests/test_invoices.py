"""Invoice studio + Paystack collection.

These tests drive the real FastAPI app against in-memory SQLite (the shared
``api_client`` fixture), so they cover routing, auth dependencies, schema and
serialisation — not just the service functions.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoicePayment
from app.models.notification import NotificationEvent


def _payload(**overrides):
    body = {
        "customer_name": "Adaeze Okafor",
        "customer_email": "adaeze@example.com",
        "customer_phone": "+2348012345678",
        "customer_address": "12 Adeola Odeku Street, Victoria Island, Lagos",
        "currency": "NGN",
        "items": [
            {"description": "Brand identity package", "quantity": 1, "unit_price_minor": 250_000_00},
            {"description": "Social media templates", "quantity": 3, "unit_price_minor": 25_000_00},
        ],
        "discount_minor": 10_000_00,
        "tax_rate": 7.5,
        "notes": "Thank you for choosing us.",
    }
    body.update(overrides)
    return body


async def _create(client, **overrides):
    response = await client.post("/api/v1/invoices", json=_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()["invoice"]


@pytest.mark.asyncio
async def test_create_recomputes_totals_and_numbers(api_client):
    invoice = await _create(api_client)
    # 250,000 + 75,000 = 325,000; minus 10,000 = 315,000; +7.5% VAT = 338,625
    assert invoice["subtotal_minor"] == 325_000_00
    assert invoice["discount_minor"] == 10_000_00
    assert invoice["tax_minor"] == 23_625_00
    assert invoice["total_minor"] == 338_625_00
    assert invoice["status"] == "draft"
    assert len(invoice["items"]) == 2
    assert invoice["number"].startswith("INV-")
    assert invoice["public_url"].endswith(f"/i/{invoice['public_token']}")
    assert [event["event_type"] for event in invoice["events"]] == ["created"]

    second = await _create(api_client)
    assert second["number"] != invoice["number"]


@pytest.mark.asyncio
async def test_edit_is_full_featured_and_keeps_history(api_client):
    invoice = await _create(api_client)
    response = await api_client.patch(
        f"/api/v1/invoices/{invoice['id']}",
        json={
            "customer_name": "Adaeze Okafor (Acme)",
            "items": [
                {"description": "Consulting retainer", "quantity": 2, "unit_price_minor": 100_000_00}
            ],
            "discount_minor": 0,
            "tax_rate": 0,
            "notes": "Updated scope",
        },
    )
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["customer"]["name"] == "Adaeze Okafor (Acme)"
    assert edited["total_minor"] == 200_000_00
    assert edited["items"][0]["description"] == "Consulting retainer"
    assert any(e["event_type"] == "updated" for e in edited["events"])


@pytest.mark.asyncio
async def test_manual_payments_move_through_partial_to_paid(api_client):
    invoice = await _create(api_client)
    invoice_id = invoice["id"]

    first = await api_client.post(
        f"/api/v1/invoices/{invoice_id}/payments",
        json={"amount_minor": 100_000_00, "provider": "bank_transfer", "note": "GTB transfer"},
    )
    assert first.status_code == 201, first.text
    partial = first.json()["invoice"]
    assert partial["status"] == "partial"
    assert partial["amount_paid_minor"] == 100_000_00
    assert partial["balance_minor"] == invoice["total_minor"] - 100_000_00

    # Overpayment is rejected with a human-readable 422.
    bad = await api_client.post(
        f"/api/v1/invoices/{invoice_id}/payments",
        json={"amount_minor": invoice["total_minor"] + 1},
    )
    assert bad.status_code == 422

    second = await api_client.post(
        f"/api/v1/invoices/{invoice_id}/payments",
        json={"amount_minor": partial["balance_minor"], "provider": "cash"},
    )
    assert second.status_code == 201, second.text
    paid = second.json()["invoice"]
    assert paid["status"] == "paid"
    assert paid["balance_minor"] == 0
    assert paid["paid_at"]
    assert any(e["event_type"] == "paid" for e in paid["events"])
    assert any(p["status"] == "success" for p in paid["payments"])


@pytest.mark.asyncio
async def test_paystack_initialize_and_webhook_are_idempotent(api_client, monkeypatch):
    await api_client.put(
        "/api/v1/invoices/settings",
        json={
            "business_name": "Pikbites Studio",
            "business_email": "billing@pikbites.test",
            "paystack_public_key": "pk_test_demo",
            "paystack_secret_key": "sk_test_demo_secret",
            "prefix": "PK",
        },
    )
    settings = (await api_client.get("/api/v1/invoices/settings")).json()
    assert settings["paystack"]["configured"] is True
    assert settings["paystack"]["mode"] == "test"
    assert "demo_secret" not in settings["paystack"]["secret_key_masked"]
    assert settings["business_name"] == "Pikbites Studio"

    invoice = await _create(api_client, currency="NGN")
    invoice_id = invoice["id"]

    async def fake_initialize(secret_key, **kwargs):
        assert secret_key == "sk_test_demo_secret"
        assert kwargs["amount_minor"] == invoice["total_minor"]
        return {
            "authorization_url": "https://checkout.paystack.com/demo",
            "access_code": "demo_access",
            "reference": kwargs["reference"],
        }

    monkeypatch.setattr(
        "app.services.invoice_service.initialize_transaction", fake_initialize
    )
    pay = await api_client.post(f"/api/v1/invoices/{invoice_id}/pay", json={})
    assert pay.status_code == 200, pay.text
    body = pay.json()
    assert body["authorization_url"] == "https://checkout.paystack.com/demo"
    reference = body["reference"]
    assert body["invoice"]["paystack_reference"] == reference
    assert any(p["status"] == "pending" for p in body["invoice"]["payments"])

    event = {
        "event": "charge.success",
        "data": {
            "id": 99887766,
            "reference": reference,
            "amount": invoice["total_minor"],
            "currency": "NGN",
            "status": "success",
            "channel": "card",
            "paid_at": datetime.now(timezone.utc).isoformat(),
            "customer": {"email": "adaeze@example.com"},
            "metadata": {"invoice_id": invoice_id},
        },
    }
    raw = json.dumps(event).encode()
    signature = hmac.new(b"sk_test_demo_secret", raw, hashlib.sha512).hexdigest()

    rejected = await api_client.post(
        "/api/v1/invoices/paystack/webhook", content=raw,
        headers={"x-paystack-signature": "wrong"},
    )
    assert rejected.status_code == 401

    accepted = await api_client.post(
        "/api/v1/invoices/paystack/webhook", content=raw,
        headers={"x-paystack-signature": signature},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "ok"

    detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()
    assert detail["status"] == "paid"
    assert detail["amount_paid_minor"] == invoice["total_minor"]

    # The same webhook again must not credit the invoice twice.
    again = await api_client.post(
        "/api/v1/invoices/paystack/webhook", content=raw,
        headers={"x-paystack-signature": signature},
    )
    assert again.status_code == 200
    detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()
    assert detail["amount_paid_minor"] == invoice["total_minor"]
    assert len([p for p in detail["payments"] if p["status"] == "success"]) == 1


@pytest.mark.asyncio
async def test_public_page_marks_viewed_and_hides_internals(api_client):
    invoice = await _create(api_client)
    await api_client.post(f"/api/v1/invoices/{invoice['id']}/send?email_customer=false")
    response = await api_client.get(f"/api/v1/invoices/public/{invoice['public_token']}")
    assert response.status_code == 200, response.text
    public = response.json()
    assert public["status"] == "viewed"
    assert "events" not in public
    assert public["business"]["name"]
    assert public["payment_link"].endswith(invoice["public_token"])

    bad = await api_client.get("/api/v1/invoices/public/does-not-exist")
    assert bad.status_code == 404


@pytest.mark.asyncio
async def test_public_pay_and_verify(api_client, monkeypatch):
    await api_client.put(
        "/api/v1/invoices/settings",
        json={"paystack_public_key": "pk_test_x", "paystack_secret_key": "sk_test_x"},
    )
    invoice = await _create(api_client)
    captured = {}

    async def fake_initialize(secret_key, **kwargs):
        captured.update(kwargs)
        return {
            "authorization_url": "https://checkout.paystack.com/public",
            "access_code": "ac",
            "reference": kwargs["reference"],
        }

    async def fake_verify(secret_key, reference):
        return {
            "status": "success",
            "amount": captured["amount_minor"],
            "currency": "NGN",
            "channel": "bank",
            "id": 42,
            "paid_at": datetime.now(timezone.utc).isoformat(),
            "customer": {"email": "adaeze@example.com"},
        }

    monkeypatch.setattr(
        "app.services.invoice_service.initialize_transaction", fake_initialize
    )
    monkeypatch.setattr("app.services.invoice_service.verify_transaction", fake_verify)

    pay = await api_client.post(f"/api/v1/invoices/public/{invoice['public_token']}/pay", json={})
    assert pay.status_code == 200, pay.text
    reference = pay.json()["reference"]

    verified = await api_client.get(
        f"/api/v1/invoices/public/{invoice['public_token']}/verify",
        params={"reference": reference},
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["success"] is True
    assert verified.json()["invoice"]["status"] == "paid"


@pytest.mark.asyncio
async def test_duplicate_rotate_and_void(api_client):
    invoice = await _create(api_client)
    copy = await api_client.post(f"/api/v1/invoices/{invoice['id']}/duplicate")
    assert copy.status_code == 201
    clone = copy.json()["invoice"]
    assert clone["id"] != invoice["id"]
    assert clone["number"] != invoice["number"]
    assert clone["status"] == "draft"
    assert clone["total_minor"] == invoice["total_minor"]

    rotated = await api_client.post(f"/api/v1/invoices/{invoice['id']}/rotate-link")
    assert rotated.status_code == 200
    assert rotated.json()["public_url"] != invoice["public_url"]

    voided = await api_client.post(f"/api/v1/invoices/{invoice['id']}/void")
    assert voided.status_code == 200
    assert voided.json()["invoice"]["status"] == "void"


@pytest.mark.asyncio
async def test_analytics_reports_naira_revenue(api_client):
    first = await _create(api_client)
    second = await _create(api_client)
    await api_client.post(f"/api/v1/invoices/{second['id']}/send?email_customer=false")
    await api_client.post(
        f"/api/v1/invoices/{first['id']}/payments",
        json={"amount_minor": first["total_minor"], "provider": "cash"},
    )
    response = await api_client.get(
        "/api/v1/invoices/analytics", params={"currency": "NGN"}
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["currency"] == "NGN"
    assert data["summary"]["paid_minor"] == first["total_minor"]
    assert data["summary"]["outstanding_minor"] == second["total_minor"]
    assert len(data["monthly"]) == 12
    assert data["recent_payments"]
    assert data["top_customers"]


@pytest.mark.asyncio
async def test_list_filters_and_overdue(api_client):
    overdue = await _create(
        api_client,
        issue_date=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
        due_date=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
    )
    open_invoice = await _create(api_client)
    await api_client.post(f"/api/v1/invoices/{overdue['id']}/send?email_customer=false")

    response = await api_client.get("/api/v1/invoices", params={"status": "overdue"})
    assert response.status_code == 200, response.text
    ids = [item["id"] for item in response.json()["items"]]
    assert overdue["id"] in ids
    assert open_invoice["id"] not in ids

    query = await api_client.get("/api/v1/invoices", params={"q": "Adaeze"})
    assert query.json()["total"] >= 2


@pytest.mark.asyncio
async def test_payment_creates_in_app_notification(api_client, api_db):
    invoice = await _create(api_client)
    await api_client.post(
        f"/api/v1/invoices/{invoice['id']}/payments",
        json={"amount_minor": invoice["total_minor"], "provider": "cash"},
    )
    event = (
        await api_db.execute(
            select(NotificationEvent).where(NotificationEvent.event_type == "invoice_paid")
        )
    ).scalars().first()
    assert event is not None
    assert invoice["number"] in (event.title or "")
    assert f"/invoices?invoice={invoice['id']}" == event.url


@pytest.mark.asyncio
async def test_unpaid_invoice_cannot_be_marked_paid_by_status(api_client):
    invoice = await _create(api_client)
    response = await api_client.patch(
        f"/api/v1/invoices/{invoice['id']}", json={"status": "paid"}
    )
    assert response.status_code == 400
    assert "payment" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_invoice_rows_persisted(api_client, api_db):
    invoice = await _create(api_client)
    row = (
        await api_db.execute(select(Invoice).where(Invoice.id == invoice["id"]))
    ).scalar_one()
    assert row.customer_name == "Adaeze Okafor"
    assert row.balance_minor == invoice["total_minor"]
    payments = (await api_db.execute(select(InvoicePayment))).scalars().all()
    assert payments == []


@pytest.mark.asyncio
async def test_frontend_contract_shapes(api_client):
    """The invoice studio reads these exact fields — pin them.

    A rename here is invisible to the Python tests but breaks the UI at
    runtime, so this test asserts the keys the React layer actually uses:
    list.summary (the stat cards), analytics (the chart and top customers),
    the payments history rows (Payments tab) and the settings payload the
    Paystack card edits.
    """
    invoice = await _create(api_client, currency="USD")
    await api_client.post(f"/api/v1/invoices/{invoice['id']}/send?email_customer=false")
    await api_client.post(
        f"/api/v1/invoices/{invoice['id']}/payments",
        json={"amount_minor": 1_000_00, "provider": "bank_transfer"},
    )

    listing = (await api_client.get("/api/v1/invoices", params={"currency": "USD"})).json()
    assert set(listing) >= {"items", "total", "page", "per_page", "summary"}
    summary = listing["summary"]
    for key in (
        "invoice_count", "paid_count", "draft_count", "overdue_count",
        "paid_minor", "outstanding_minor", "overdue_minor", "draft_minor", "by_status",
    ):
        assert key in summary, key

    row = listing["items"][0]
    for key in (
        "number", "public_url", "status", "status_label", "currency", "currency_symbol",
        "total_minor", "balance_minor", "amount_paid_minor", "days_until_due",
        "viewed_at", "reminders_sent", "payment_link",
    ):
        assert key in row, key
    for key in ("name", "email", "phone", "address", "business", "contact_id"):
        assert key in row["customer"], key
    assert row["items"][0]["amount_minor"] == (
        row["items"][0]["quantity"] * row["items"][0]["unit_price_minor"]
    )

    analytics = (
        await api_client.get("/api/v1/invoices/analytics", params={"currency": "USD"})
    ).json()
    for key in ("monthly", "top_customers", "recent_payments", "due_in_14_days_minor", "summary"):
        assert key in analytics, key
    assert {"month", "label", "invoiced_minor", "collected_minor"} <= set(analytics["monthly"][0])
    assert {"customer_name", "invoices", "paid_minor"} <= set(analytics["top_customers"][0])
    assert {
        "invoice_id", "invoice_number", "customer_name", "amount_minor",
        "currency", "provider", "channel", "paid_at",
    } <= set(analytics["recent_payments"][0])

    history = (await api_client.get("/api/v1/invoices/payments")).json()
    assert {"items", "total", "page", "per_page", "total_collected_minor", "by_provider"} <= set(history)
    if history["items"]:
        assert {"invoice_id", "invoice_number", "customer_name", "invoice_status"} <= set(
            history["items"][0]
        )

    settings = (await api_client.get("/api/v1/invoices/settings")).json()
    for key in (
        "business_name", "business_email", "business_phone", "business_address",
        "business_website", "business_logo_url", "prefix", "default_currency",
        "default_tax_rate", "default_terms", "footer", "currencies", "paystack",
    ):
        assert key in settings, key
    assert {"configured", "mode", "public_key", "secret_key_masked", "webhook_url", "channels"} <= set(
        settings["paystack"]
    )
