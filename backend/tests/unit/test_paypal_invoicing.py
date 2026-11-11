"""PayPalInvoicing against a mock HTTP server, the demo/fake twins, and how each is chosen."""

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.adapters import paypal_invoicing as module
from app.adapters.paypal import PayPalError
from app.adapters.paypal_invoicing import (
    DemoInvoicing,
    DemoWebhookVerifier,
    FakeInvoicing,
    InvoiceLine,
    InvoiceRequest,
    PayPalInvoicing,
    PayPalWebhookVerifier,
    RejectAllVerifier,
    UnconfiguredInvoicing,
    demo_signature,
    invoice_body,
)
from app.config import Settings

BASE = "https://api-m.sandbox.paypal.com"
TOKEN = httpx.Response(200, json={"access_token": "tok-123", "expires_in": 32400})
REQ = InvoiceRequest(
    number="PL-ABC12345",
    currency="USD",
    lines=(InvoiceLine("Security deposit", "Verified in audit A1", Decimal("700")),
           InvoiceLine("Brokerage fee", "Verified in audit A1", Decimal("350"))),
    discount=Decimal("15"),
    recipient_name="Asha Rao",
    recipient_email="asha@example.com",
    due_date=date(2026, 11, 18),
    note="Visit token already paid (PayPal capture CAP-1) is deducted.",
    reference="inv-1",
)


def _api(routes):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        reply = routes[(request.method, request.url.path)]
        return reply(request) if callable(reply) else reply

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return PayPalInvoicing("client-id", "client-secret", BASE, client=client), requests


def test_invoice_body_has_items_and_the_token_as_an_invoice_discount():
    body = invoice_body(REQ)

    assert body["detail"]["invoice_number"] == "PL-ABC12345"
    assert body["detail"]["payment_term"] == {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": "2026-11-18"}
    assert [i["unit_amount"] for i in body["items"]] == [
        {"currency_code": "USD", "value": "700.00"}, {"currency_code": "USD", "value": "350.00"}]
    assert body["amount"]["breakdown"]["discount"]["invoice_discount"]["amount"] == {"currency_code": "USD", "value": "15.00"}
    assert body["primary_recipients"][0]["billing_info"] == {
        "name": {"given_name": "Asha", "surname": "Rao"}, "email_address": "asha@example.com"}


def test_create_sends_the_request_id_and_returns_paypals_id():
    api, requests = _api({
        ("POST", "/v1/oauth2/token"): TOKEN,
        ("POST", "/v2/invoicing/invoices"): httpx.Response(201, json={"id": "INV2-AAAA", "status": "DRAFT"}),
    })

    created = api.create_draft(REQ, request_id="create-inv-1")

    assert (created.paypal_invoice_id, created.status) == ("INV2-AAAA", "DRAFT")
    create = requests[1]
    assert create.headers["PayPal-Request-Id"] == "create-inv-1"
    assert json.loads(create.content) == invoice_body(REQ)


def test_create_reads_the_id_from_a_link_only_reply():
    api, _ = _api({
        ("POST", "/v1/oauth2/token"): TOKEN,
        ("POST", "/v2/invoicing/invoices"): httpx.Response(
            201, json={"rel": "self", "href": f"{BASE}/v2/invoicing/invoices/INV2-BBBB", "method": "GET"}),
    })
    assert api.create_draft(REQ, "r").paypal_invoice_id == "INV2-BBBB"


def test_a_retry_refused_as_duplicate_number_finds_the_first_invoice():
    # Real sandbox behaviour (2026-11-11): the same PayPal-Request-Id is not replayed for invoices;
    # PayPal answers 422 DUPLICATE_INVOICE_NUMBER. Our number is fixed, so the match is ours.
    api, requests = _api({
        ("POST", "/v1/oauth2/token"): TOKEN,
        ("POST", "/v2/invoicing/invoices"): httpx.Response(
            422, json={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "DUPLICATE_INVOICE_NUMBER"}]}),
        ("POST", "/v2/invoicing/search-invoices"): httpx.Response(200, json={"items": [
            {"id": "INV2-OTHER", "status": "SENT", "detail": {"invoice_number": "PL-OTHER"}},
            {"id": "INV2-MINE", "status": "DRAFT", "detail": {"invoice_number": "PL-ABC12345"}}]}),
    })

    assert api.create_draft(REQ, "create-inv-1").paypal_invoice_id == "INV2-MINE"
    assert json.loads(requests[2].content) == {"invoice_number": "PL-ABC12345"}


def test_a_duplicate_number_with_no_match_is_still_an_error():
    api, _ = _api({
        ("POST", "/v1/oauth2/token"): TOKEN,
        ("POST", "/v2/invoicing/invoices"): httpx.Response(
            422, json={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "DUPLICATE_INVOICE_NUMBER"}]}),
        ("POST", "/v2/invoicing/search-invoices"): httpx.Response(200, json={"items": []}),
    })
    with pytest.raises(PayPalError, match="DUPLICATE_INVOICE_NUMBER"):
        api.create_draft(REQ, "r")


def test_errors_carry_status_issue_and_debug_id():
    api, _ = _api({
        ("POST", "/v1/oauth2/token"): TOKEN,
        ("POST", "/v2/invoicing/invoices"): httpx.Response(
            403, json={"name": "NOT_AUTHORIZED", "details": [{"issue": "PERMISSION_DENIED"}]},
            headers={"PayPal-Debug-Id": "dbg-9"}),
    })
    with pytest.raises(PayPalError) as err:
        api.create_draft(REQ, "r")
    assert (err.value.status, err.value.debug_id) == (403, "dbg-9")
    assert "NOT_AUTHORIZED PERMISSION_DENIED" in str(err.value)


def test_send_remind_cancel_get_and_qr():
    api, requests = _api({
        ("POST", "/v1/oauth2/token"): TOKEN,
        ("POST", "/v2/invoicing/invoices/INV2-A/send"): httpx.Response(200, json={"href": "https://pay/INV2-A"}),
        ("POST", "/v2/invoicing/invoices/INV2-A/remind"): httpx.Response(204),
        ("POST", "/v2/invoicing/invoices/INV2-A/cancel"): httpx.Response(204),
        ("GET", "/v2/invoicing/invoices/INV2-A"): httpx.Response(200, json={
            "status": "PAID", "payments": {"paid_amount": {"currency_code": "USD", "value": "1035.00"}},
            "detail": {"metadata": {"recipient_view_url": "https://pay/INV2-A"}}}),
        ("POST", "/v2/invoicing/invoices/INV2-A/generate-qr-code"): httpx.Response(200, text="iVBORw0KGgoAAAANSUhEUg=="),
    })

    assert api.send("INV2-A", "send-1") == "https://pay/INV2-A"
    api.remind("INV2-A", "remind-1", "due soon")
    api.cancel("INV2-A", "cancel-1", "cancelled")
    state = api.get("INV2-A")
    assert (state.status, state.paid_amount, state.payer_view_url) == ("PAID", Decimal("1035.00"), "https://pay/INV2-A")
    assert api.qr_code("INV2-A").startswith("iVBOR")
    ids = [r.headers.get("PayPal-Request-Id") for r in requests[1:4]]
    assert ids == ["send-1", "remind-1", "cancel-1"]


def test_sending_an_already_sent_invoice_counts_as_success():
    api, _ = _api({
        ("POST", "/v1/oauth2/token"): TOKEN,
        ("POST", "/v2/invoicing/invoices/INV2-A/send"): httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY",
                                                                             "details": [{"issue": "INVOICE_ALREADY_SENT"}]}),
        ("GET", "/v2/invoicing/invoices/INV2-A"): httpx.Response(200, json={"status": "SENT"}),
    })
    api.send("INV2-A", "send-1")  # no error


def test_dispute_evidence_is_multipart_with_the_approved_notes():
    api, requests = _api({
        ("POST", "/v1/oauth2/token"): TOKEN,
        ("POST", "/v1/customer/disputes/PP-D-1/provide-evidence"): httpx.Response(200, json={}),
    })
    api.provide_dispute_evidence("PP-D-1", "Signed audit A1 shows ...", "evidence-1")
    req = requests[1]
    assert req.headers["content-type"].startswith("multipart/form-data")
    assert b'"notes": "Signed audit A1 shows ..."' in req.content


def test_webhook_verification_asks_paypal():
    seen = {}

    def verify(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"verification_status": "SUCCESS"})

    api, _ = _api({("POST", "/v1/oauth2/token"): TOKEN, ("POST", "/v1/notifications/verify-webhook-signature"): verify})
    headers = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://cert", "PAYPAL-TRANSMISSION-ID": "t1",
               "PAYPAL-TRANSMISSION-SIG": "sig", "PAYPAL-TRANSMISSION-TIME": "2026-11-11T00:00:00Z"}
    event = {"id": "WH-1", "event_type": "INVOICING.INVOICE.PAID"}

    assert PayPalWebhookVerifier(api, "WHID").verify(headers, b"{}", event) is True
    assert seen["webhook_id"] == "WHID" and seen["webhook_event"] == event and seen["transmission_sig"] == "sig"
    assert PayPalWebhookVerifier(api, "WHID").verify({}, b"{}", event) is False  # missing headers: not even asked


def test_demo_verifier_needs_our_hmac_and_reject_all_rejects():
    body = b'{"id":"WH-1"}'
    demo = DemoWebhookVerifier("secret")
    assert demo.verify({"X-Demo-Signature": demo_signature(body, "secret")}, body, {}) is True
    assert demo.verify({"X-Demo-Signature": demo_signature(body, "other")}, body, {}) is False
    assert demo.verify({}, body, {}) is False
    assert RejectAllVerifier().verify({}, body, {}) is False


def test_fake_is_idempotent_per_request_id_and_demo_ids_say_fake():
    fake = FakeInvoicing()
    assert fake.create_draft(REQ, "r1").paypal_invoice_id == fake.create_draft(REQ, "r1").paypal_invoice_id
    assert len(fake.invoices) == 1
    assert DemoInvoicing().create_draft(REQ, "r1").paypal_invoice_id.startswith("FAKE-INV-")
    with pytest.raises(PayPalError):
        UnconfiguredInvoicing("no keys").create_draft(REQ, "r1")


@pytest.mark.parametrize(
    "settings,expected",
    [
        ({"paypal_client_id": "id", "paypal_client_secret": "s", "demo_mode": True}, PayPalInvoicing),
        ({"paypal_client_id": None, "paypal_client_secret": None, "demo_mode": True}, DemoInvoicing),
        ({"paypal_client_id": None, "paypal_client_secret": None, "demo_mode": False}, UnconfiguredInvoicing),
    ],
)
def test_adapter_choice(monkeypatch, settings, expected):
    monkeypatch.setattr(module, "get_settings", lambda: Settings(_env_file=None, **settings))
    module.get_invoicing.cache_clear()
    try:
        assert isinstance(module.get_invoicing(), expected)
    finally:
        module.get_invoicing.cache_clear()


@pytest.mark.parametrize(
    "settings,expected",
    [
        ({"paypal_client_id": "id", "paypal_client_secret": "s", "paypal_webhook_id": "W", "demo_mode": True}, PayPalWebhookVerifier),
        ({"paypal_client_id": "id", "paypal_client_secret": "s", "paypal_webhook_id": None, "demo_mode": True}, DemoWebhookVerifier),
        ({"paypal_client_id": "id", "paypal_client_secret": "s", "paypal_webhook_id": None, "demo_mode": False}, RejectAllVerifier),
    ],
)
def test_webhook_verifier_choice(monkeypatch, settings, expected):
    monkeypatch.setattr(module, "get_settings", lambda: Settings(_env_file=None, **settings))
    module.get_webhook_verifier.cache_clear()
    try:
        assert isinstance(module.get_webhook_verifier(), expected)
    finally:
        module.get_webhook_verifier.cache_clear()
