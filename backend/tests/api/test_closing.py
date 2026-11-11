"""Feature 4 closing: a LIVE verified listing + a captured token -> one signed invoice, sent once."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import Invoice, JobStatus, LedgerEntry, Listing, OutboxJob
from app.services import outbox
from tests.f4_helpers import TENANT, capture, closed, demo_mode, keys, live_listing  # noqa: F401  (fixtures)


def test_simulated_capture_is_demo_mode_only(client, keys):
    listing_id, _ = live_listing(client, keys)
    resp = client.post("/dev/ledger/simulate-capture", json={"listing_id": listing_id}, headers=keys["manager"])
    assert resp.status_code == 404


def test_simulated_capture_needs_a_manager(client, keys, demo_mode):
    listing_id, _ = live_listing(client, keys)
    resp = client.post("/dev/ledger/simulate-capture", json={"listing_id": listing_id}, headers=keys["broker_rajesh"])
    assert resp.status_code == 403


def test_closing_invoices_deposit_plus_brokerage_minus_the_token(client, keys, demo_mode, fake_invoicing, db_session):
    body = closed(client, keys)

    inv = body["invoice"]
    assert (inv["deposit"], inv["brokerage"], inv["token_deduction"], inv["total"]) == (
        "70000.00", "35000.00", "15.00", "104985.00")
    assert inv["status"] == "SENT"  # created and sent straight away through the outbox
    assert inv["signature_valid"] is True
    assert body["status"] == "INVOICED"
    assert (body["gross"], body["received"], body["outstanding"]) == ("105000.00", "15.00", "104985.00")
    kinds = [e["kind"] for e in body["entries"]]
    assert kinds == ["HOLD_CAPTURED", "INVOICE_PREPARED", "INVOICE_DRAFTED", "INVOICE_SENT"]

    [paypal_invoice] = fake_invoicing.invoices.values()
    sent = paypal_invoice["body"]
    assert sent["amount"]["breakdown"]["discount"]["invoice_discount"]["amount"]["value"] == "15.00"
    assert body["capture_ref"] in sent["detail"]["note"]
    assert db_session.get(Listing, body["listing_id"]).status.value == "RENTED"


def test_running_the_worker_again_sends_nothing_twice(client, keys, demo_mode, fake_invoicing, db_session):
    closed(client, keys)
    later = datetime.now(UTC) + timedelta(minutes=5)

    outbox.run_due(db_session, None, now=later, invoicing=fake_invoicing)
    outbox.run_due(db_session, None, now=later, invoicing=fake_invoicing)

    assert [c[0] for c in fake_invoicing.calls] == ["create", "send"]
    assert len(fake_invoicing.invoices) == 1


def test_paypal_down_leaves_the_invoice_queued_then_the_worker_finishes(client, keys, demo_mode, fake_invoicing, db_session):
    fake_invoicing.fail_times = 1
    body = closed(client, keys)
    assert body["invoice"]["status"] == "PENDING"
    assert body["invoice"]["paypal_invoice_id"] is None  # never a made-up id

    later = datetime.now(UTC) + timedelta(minutes=2)
    outbox.run_due(db_session, None, now=later, invoicing=fake_invoicing)
    outbox.run_due(db_session, None, now=later, invoicing=fake_invoicing)

    invoice = db_session.scalar(select(Invoice))
    db_session.refresh(invoice)
    assert invoice.status.value == "SENT"
    creates = [c for c in fake_invoicing.calls if c[0] == "create"]
    assert len({rid for _, rid in creates}) == 1  # every retry used the same PayPal-Request-Id
    assert len(fake_invoicing.invoices) == 1


def test_broker_cannot_change_amounts(client, keys, demo_mode):
    listing_id, _ = live_listing(client, keys)
    ref = capture(client, keys, listing_id)
    # Extra fields are ignored for brokers: the invoice is built from the signed audit only.
    resp = client.post(f"/broker/listings/{listing_id}/close",
                       json={**TENANT, "capture_ref": ref, "deposit": "1"}, headers=keys["broker_rajesh"])
    assert resp.status_code == 201
    assert resp.json()["invoice"]["deposit"] == "70000.00"


def test_manager_override_needs_a_note_and_is_recorded(client, keys, demo_mode):
    listing_id, _ = live_listing(client, keys)
    ref = capture(client, keys, listing_id)
    url = f"/manager/listings/{listing_id}/close"

    refused = client.post(url, json={**TENANT, "capture_ref": ref, "deposit": "60000.00"}, headers=keys["manager"])
    assert refused.status_code == 422
    assert "override" in refused.json()["detail"]

    ok = client.post(url, json={**TENANT, "capture_ref": ref, "deposit": "60000.00",
                                "override_note": "Owner agreed a lower deposit by email"}, headers=keys["manager"])
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["invoice"]["total"] == "94985.00"
    assert body["invoice"]["override_note"] == "Owner agreed a lower deposit by email"
    override = next(e for e in body["entries"] if e["kind"] == "AMOUNT_OVERRIDE")
    assert "lower deposit" in override["note"]


def test_closing_needs_a_captured_hold_for_this_listing(client, keys, demo_mode):
    listing_id, _ = live_listing(client, keys)
    resp = client.post(f"/broker/listings/{listing_id}/close", json={**TENANT, "capture_ref": "CAP-NOPE"},
                       headers=keys["broker_rajesh"])
    assert resp.status_code == 409
    assert "No captured visit hold" in resp.json()["detail"]


def test_one_token_pays_one_closing_and_a_listing_closes_once(client, keys, demo_mode):
    body = closed(client, keys)
    again = client.post(f"/broker/listings/{body['listing_id']}/close",
                        json={**TENANT, "capture_ref": body["capture_ref"]}, headers=keys["broker_rajesh"])
    assert again.status_code == 409


def test_only_a_live_listing_can_be_closed(client, keys, demo_mode):
    draft = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=keys["broker_rajesh"]).json()["id"]
    ref = capture(client, keys, draft)
    resp = client.post(f"/broker/listings/{draft}/close", json={**TENANT, "capture_ref": ref}, headers=keys["broker_rajesh"])
    assert resp.status_code == 409


def test_another_brokers_listing_looks_like_none(client, keys, demo_mode):
    listing_id, _ = live_listing(client, keys)
    ref = capture(client, keys, listing_id)
    resp = client.post(f"/broker/listings/{listing_id}/close", json={**TENANT, "capture_ref": ref},
                       headers=keys["broker_quickcash"])
    assert resp.status_code == 404


def test_a_tampered_invoice_is_never_sent(client, keys, demo_mode, fake_invoicing, db_session):
    fake_invoicing.fail_times = 1
    closed(client, keys)
    invoice = db_session.scalar(select(Invoice))
    invoice.total = invoice.total - 1000  # someone edits the database before the retry
    db_session.commit()

    outbox.run_due(db_session, None, now=datetime.now(UTC) + timedelta(minutes=2), invoicing=fake_invoicing)

    job = db_session.scalar(select(OutboxJob).where(OutboxJob.ref_id == invoice.id))
    db_session.refresh(job)
    assert job.status is JobStatus.FAILED
    assert "signature" in job.last_error
    assert fake_invoicing.invoices == {}


def test_refresh_reads_payment_from_paypal_once(client, keys, demo_mode, fake_invoicing, db_session):
    body = closed(client, keys)
    fake_invoicing.mark_paid(body["invoice"]["paypal_invoice_id"], body["invoice"]["total"])

    for _ in range(2):
        resp = client.post(f"/closings/{body['id']}/refresh", headers=keys["broker_rajesh"])
    out = resp.json()

    assert (out["status"], out["invoice"]["status"]) == ("PAID", "PAID")
    assert (out["received"], out["outstanding"]) == ("105000.00", "0.00")
    paid = db_session.scalars(select(LedgerEntry).where(LedgerEntry.kind == "INVOICE_PAID")).all()
    assert len(paid) == 1


def test_manager_cancels_an_unpaid_invoice(client, keys, demo_mode, fake_invoicing):
    body = closed(client, keys)
    assert client.post(f"/closings/{body['id']}/cancel", headers=keys["broker_rajesh"]).status_code == 403

    out = client.post(f"/closings/{body['id']}/cancel", headers=keys["manager"]).json()

    assert (out["status"], out["invoice"]["status"]) == ("CANCELLED", "CANCELLED")
    assert ("cancel", f"cancel-{body['invoice']['id']}") in fake_invoicing.calls


def test_qr_code(client, keys, demo_mode):
    body = closed(client, keys)
    qr = client.get(f"/closings/{body['id']}/qr", headers=keys["broker_rajesh"]).json()
    assert qr["data_url"].startswith("data:image/png;base64,")
