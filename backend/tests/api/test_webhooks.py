"""Feature 4 webhooks: unverified or replayed events change nothing; a verified PAID applies once."""

import json

from sqlalchemy import select

from app.adapters.paypal_invoicing import DEMO_SIGNATURE_HEADER, DemoWebhookVerifier, demo_signature
from app.db.models import Dispute, LedgerEntry, WebhookEvent
from tests.f4_helpers import closed, demo_mode, keys  # noqa: F401  (fixtures)

SECRET = "webhook-test-secret"


def _paid_event(body, event_id="WH-PAID-1"):
    inv = body["invoice"]
    return {
        "id": event_id, "event_type": "INVOICING.INVOICE.PAID", "create_time": "2026-11-12T10:00:00Z",
        "resource": {"invoice": {"id": inv["paypal_invoice_id"], "status": "PAID",
                                 "detail": {"invoice_number": inv["number"]},
                                 "payments": {"paid_amount": {"currency_code": "USD", "value": inv["total"]}}}},
    }


def _post(client, event, sign=True):
    raw = json.dumps(event).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        headers[DEMO_SIGNATURE_HEADER] = demo_signature(raw, SECRET)
    return client.post("/webhooks/paypal", content=raw, headers=headers)


def _trust(webhook_verifier):
    webhook_verifier["verifier"] = DemoWebhookVerifier(SECRET)


def test_unverified_webhook_is_stored_but_changes_nothing(client, keys, demo_mode, db_session):
    body = closed(client, keys)

    resp = _post(client, _paid_event(body), sign=False)  # the default verifier trusts nobody

    assert resp.status_code == 400
    stored = db_session.get(WebhookEvent, "WH-PAID-1")
    assert (stored.verified, stored.processed_at) == (False, None)
    detail = client.get(f"/closings/{body['id']}", headers=keys["broker_rajesh"]).json()
    assert detail["invoice"]["status"] == "SENT"


def test_a_forged_signature_is_refused(client, keys, demo_mode, webhook_verifier):
    _trust(webhook_verifier)
    body = closed(client, keys)
    raw = json.dumps(_paid_event(body)).encode()
    resp = client.post("/webhooks/paypal", content=raw, headers={DEMO_SIGNATURE_HEADER: demo_signature(raw, "guess")})
    assert resp.status_code == 400


def test_verified_paid_marks_invoice_and_closing_paid_exactly_once(client, keys, demo_mode, webhook_verifier, db_session):
    _trust(webhook_verifier)
    body = closed(client, keys)

    first = _post(client, _paid_event(body))
    replay = _post(client, _paid_event(body))
    other_id_same_payment = _post(client, _paid_event(body, "WH-PAID-2"))  # PayPal re-sent under a new id

    assert first.json()["outcome"] == "applied"
    assert replay.json()["outcome"] == "duplicate"
    assert other_id_same_payment.status_code == 200
    detail = client.get(f"/closings/{body['id']}", headers=keys["broker_rajesh"]).json()
    assert (detail["status"], detail["invoice"]["status"]) == ("PAID", "PAID")
    assert (detail["received"], detail["outstanding"]) == ("105000.00", "0.00")
    assert len(db_session.scalars(select(LedgerEntry).where(LedgerEntry.kind == "INVOICE_PAID")).all()) == 1


def test_an_unknown_event_type_is_kept_and_ignored(client, webhook_verifier, db_session):
    _trust(webhook_verifier)
    resp = _post(client, {"id": "WH-X", "event_type": "PAYMENT.SALE.COMPLETED", "resource": {}})
    assert resp.json()["outcome"] == "ignored"
    assert db_session.get(WebhookEvent, "WH-X").processed_at is not None


def test_garbage_is_a_400(client):
    assert client.post("/webhooks/paypal", content=b"not json").status_code == 400


def test_dispute_webhook_opens_a_dispute_with_an_evidence_pack(client, keys, demo_mode, webhook_verifier, db_session):
    _trust(webhook_verifier)
    body = closed(client, keys)
    event = {"id": "WH-D-1", "event_type": "CUSTOMER.DISPUTE.CREATED", "resource": {
        "dispute_id": "PP-D-1", "reason": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "dispute_amount": {"currency_code": "USD", "value": body["invoice"]["total"]},
        "disputed_transactions": [{"invoice_number": body["invoice"]["number"]}]}}

    assert _post(client, event).json()["outcome"] == "applied"

    dispute = db_session.scalar(select(Dispute))
    assert dispute.closing_id == body["id"]
    pack = json.loads(dispute.evidence_pack_json)
    kinds = {i["kind"] for i in pack["items"]}
    assert {"dispute", "signed_listing_audit", "broker_checkin_photo", "invoice", "ledger_entry", "broker_trust"} <= kinds
    assert all(i["source_id"] for i in pack["items"])
    assert any("Feature 3" in m for m in pack["missing"])
    detail = client.get(f"/closings/{body['id']}", headers=keys["broker_rajesh"]).json()
    assert detail["status"] == "DISPUTED"
    assert "DISPUTE_OPENED" in [e["kind"] for e in detail["entries"]]

    resolved = {"id": "WH-D-2", "event_type": "CUSTOMER.DISPUTE.RESOLVED", "resource": {
        "dispute_id": "PP-D-1", "status": "RESOLVED", "dispute_outcome": {"outcome_code": "RESOLVED_SELLER_FAVOUR"}}}
    _post(client, resolved)
    db_session.refresh(dispute)
    assert dispute.status.value == "RESOLVED"
