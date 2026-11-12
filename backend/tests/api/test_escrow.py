"""Feature 3 end to end: a real (fake-PayPal) authorization, door proof, the referee's four
outcomes, settlement through the outbox, and the hand-off to Feature 4's closing."""

import io
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api import visits as visits_api
from app.db.models import JobStatus, LedgerEntry, Listing, ListingStatus, OutboxJob, User, Visit, VisitCheckin, VisitStatus
from app.services import escrow_service, outbox, visit_service
from app.services.rate_limit import RateLimiter
from tests.conftest import BLR_PIN, photo_bytes
from tests.f4_helpers import TENANT, demo_mode, keys, live_listing  # noqa: F401  (fixtures)

GUEST = {"tenant_name": "Asha Rao", "tenant_email": "asha@example.com"}
AT_FLAT = {"latitude": BLR_PIN[0], "longitude": BLR_PIN[1], "accuracy_m": 12}
FAR = {"latitude": 12.90, "longitude": 77.50, "accuracy_m": 12}


@pytest.fixture(autouse=True)
def fresh_rate_limits(monkeypatch):
    for name, limit in (("hold_limit", 10), ("assistant_limit", 20), ("checkin_limit", 30)):
        monkeypatch.setattr(visits_api, name, RateLimiter(limit=limit, per_seconds=60))


def held(client, listing_id) -> dict:
    slot = client.get(f"/public/listings/{listing_id}/slots").json()["slots"][0]
    resp = client.post(f"/public/listings/{listing_id}/visits", json={**GUEST, "starts_at": slot["starts_at"], "ends_at": slot["ends_at"]})
    assert resp.status_code == 201, resp.text
    return resp.json()


def tenant(visit) -> dict:
    return {"X-Hold-Token": visit["hold_token"]}


def authorize(client, visit) -> dict:
    order = client.post(f"/public/visits/{visit['id']}/paypal-order", headers=tenant(visit))
    assert order.status_code == 200, order.text
    resp = client.post(f"/public/visits/{visit['id']}/authorize", json={"order_id": order.json()["order_id"]}, headers=tenant(visit))
    assert resp.status_code == 200, resp.text
    return resp.json()


def at_the_door(db_session, visit_id, started_minutes_ago=35):
    """Move the booked visit so it started N minutes ago (the clock can't be fast-forwarded)."""
    row = db_session.get(Visit, visit_id)
    row.starts_at = datetime.now(UTC) - timedelta(minutes=started_minutes_ago)
    row.ends_at = row.starts_at + timedelta(hours=1)
    db_session.commit()


@pytest.fixture
def booked(client, keys, db_session):
    """A LIVE listing with a booked visit that started 35 minutes ago. Returns (listing_id, visit, pin)."""
    listing_id, _ = live_listing(client, keys)
    visit = held(client, listing_id)
    out = authorize(client, visit)
    at_the_door(db_session, visit["id"])
    return listing_id, visit, out["pin"]


def post(client, path, headers, json=None):
    return client.post(path, json=json or {}, headers=headers)


def ledger_kinds(db_session, listing_id):
    return [e.kind for e in db_session.scalars(
        select(LedgerEntry).where(LedgerEntry.listing_id == listing_id).order_by(LedgerEntry.occurred_at))]


# --- authorization ---


def test_the_order_is_created_once_for_the_visits_own_fee(client, keys, fake_orders):
    listing_id, _ = live_listing(client, keys)
    visit = held(client, listing_id)
    assert post(client, f"/public/visits/{visit['id']}/paypal-order", {"X-Hold-Token": "wrong"}).status_code == 404
    first = post(client, f"/public/visits/{visit['id']}/paypal-order", tenant(visit)).json()
    again = post(client, f"/public/visits/{visit['id']}/paypal-order", tenant(visit)).json()
    assert first["order_id"] == again["order_id"] and first["demo"] is True
    assert str(fake_orders.orders[first["order_id"]].req.amount) == visit["fee"]
    assert [c[0] for c in fake_orders.calls] == ["create"]


def test_authorize_books_the_visit_gives_a_pin_and_writes_the_ledger(client, keys, fake_orders, db_session):
    listing_id, _ = live_listing(client, keys)
    visit = held(client, listing_id)
    assert post(client, f"/public/visits/{visit['id']}/authorize", tenant(visit)).status_code == 409  # no order yet
    out = authorize(client, visit)
    assert out["status"] == "COMMITTED" and out["authorization_ref"].startswith("FAKE-T-AUTH-")
    assert len(out["pin"]) == 4 and out["pin"].isdigit()
    assert client.get(f"/public/visits/{visit['id']}", headers=tenant(visit)).json()["pin"] == out["pin"]  # stable
    assert post(client, f"/public/visits/{visit['id']}/authorize", tenant(visit)).json()["authorization_ref"] == out["authorization_ref"]
    assert [c[0] for c in fake_orders.calls].count("authorize") == 1
    assert ledger_kinds(db_session, listing_id) == ["HOLD_AUTHORIZED"]
    row = db_session.get(Visit, visit["id"])
    assert row.pin_hash and out["pin"] not in row.pin_hash


def test_a_wrong_order_id_is_refused(client, keys):
    listing_id, _ = live_listing(client, keys)
    visit = held(client, listing_id)
    post(client, f"/public/visits/{visit['id']}/paypal-order", tenant(visit))
    resp = post(client, f"/public/visits/{visit['id']}/authorize", tenant(visit), {"order_id": "SOMEONE-ELSES"})
    assert resp.status_code == 409


def test_approval_after_the_hold_ran_out_is_voided_at_once(client, keys, fake_orders, db_session):
    listing_id, _ = live_listing(client, keys)
    visit = held(client, listing_id)
    order = post(client, f"/public/visits/{visit['id']}/paypal-order", tenant(visit)).json()["order_id"]
    db_session.get(Visit, visit["id"]).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    resp = post(client, f"/public/visits/{visit['id']}/authorize", tenant(visit), {"order_id": order})
    assert resp.status_code == 409 and "ran out" in resp.json()["detail"]
    row = db_session.get(Visit, visit["id"])
    assert row.status is VisitStatus.EXPIRED and row.outcome == "TOO_LATE"
    assert fake_orders.authorizations[row.authorization_ref].status == "VOIDED"
    assert ledger_kinds(db_session, listing_id) == ["HOLD_AUTHORIZED", "HOLD_VOIDED"]


def test_paypal_down_during_authorize_can_be_retried(client, keys, fake_orders):
    listing_id, _ = live_listing(client, keys)
    visit = held(client, listing_id)
    post(client, f"/public/visits/{visit['id']}/paypal-order", tenant(visit))
    fake_orders.fail_times = 1
    assert post(client, f"/public/visits/{visit['id']}/authorize", tenant(visit)).status_code == 502
    assert post(client, f"/public/visits/{visit['id']}/authorize", tenant(visit)).json()["status"] == "COMMITTED"


# --- A: visited and passed ---


def test_pin_then_pass_releases_the_hold(client, keys, booked, fake_orders, db_session):
    listing_id, visit, pin = booked
    resp = post(client, f"/broker/visits/{visit['id']}/pin", keys["broker_rajesh"], {"pin": pin})
    assert resp.status_code == 200 and resp.json()["proof"]["pin_entered"] and resp.json()["pin"] is None
    out = post(client, f"/public/visits/{visit['id']}/decision", tenant(visit), {"choice": "PASS"}).json()
    assert (out["status"], out["outcome"]) == ("VOIDED", "PASSED")
    assert fake_orders.authorizations[out["authorization_ref"]].status == "VOIDED"
    assert ledger_kinds(db_session, listing_id) == ["HOLD_AUTHORIZED", "HOLD_VOIDED"]
    late = post(client, f"/public/visits/{visit['id']}/decision", tenant(visit), {"choice": "LOCK"})
    assert late.status_code == 409 and "already settled" in late.json()["detail"]


def test_wrong_pins_are_counted_then_locked(client, keys, booked):
    _, visit, pin = booked
    wrong = "0000" if pin != "0000" else "1111"
    for left in (4, 3, 2, 1, 0):
        resp = post(client, f"/broker/visits/{visit['id']}/pin", keys["broker_rajesh"], {"pin": wrong})
        assert resp.status_code == 422 and f"{left} tries left" in resp.json()["detail"]
    resp = post(client, f"/broker/visits/{visit['id']}/pin", keys["broker_rajesh"], {"pin": pin})
    assert resp.status_code == 422 and "GPS" in resp.json()["detail"]


def test_the_pin_never_reaches_staff(client, keys, booked):
    _, visit, pin = booked
    cal = client.get("/visits", headers=keys["manager"]).text
    assert f'"pin":"{pin}"' not in cal and '"pin"' not in cal
    assert client.get(f"/public/visits/{visit['id']}").status_code == 404  # no token, no visit


def test_another_brokers_visit_looks_like_none(client, keys, booked):
    _, visit, pin = booked
    assert post(client, f"/broker/visits/{visit['id']}/pin", keys["broker_quickcash"], {"pin": pin}).status_code == 404


# --- B: the tenant locks the flat ---


def test_lock_and_approval_capture_the_token_for_the_closing(client, keys, booked, fake_orders, db_session):
    listing_id, visit, pin = booked
    post(client, f"/broker/visits/{visit['id']}/pin", keys["broker_rajesh"], {"pin": pin})
    waiting = post(client, f"/public/visits/{visit['id']}/decision", tenant(visit), {"choice": "LOCK"}).json()
    assert waiting["status"] == "COMMITTED" and "waiting for the broker to approve" in waiting["proof"]["waiting_for"]
    out = post(client, f"/broker/visits/{visit['id']}/approve-lock", keys["broker_rajesh"]).json()
    assert (out["status"], out["outcome"]) == ("CAPTURED", "LOCKED") and out["capture_ref"].startswith("FAKE-T-CAP-")
    assert ledger_kinds(db_session, listing_id) == ["HOLD_AUTHORIZED", "HOLD_CAPTURED"]
    [closable] = client.get("/ledger/closable", headers=keys["broker_rajesh"]).json()
    assert [(h["capture_ref"], h["tenant_name"]) for h in closable["holds"]] == [(out["capture_ref"], "Asha Rao")]
    closing = client.post(f"/broker/listings/{listing_id}/close", json={**TENANT, "capture_ref": out["capture_ref"]},
                          headers=keys["broker_rajesh"])
    assert closing.status_code == 201 and closing.json()["invoice"]["token_deduction"] == visit["fee"]


def test_a_broker_cannot_lock_without_the_tenant(client, keys, booked):
    _, visit, _ = booked
    post(client, f"/broker/visits/{visit['id']}/checkin", keys["broker_rajesh"], AT_FLAT)
    out = post(client, f"/broker/visits/{visit['id']}/approve-lock", keys["broker_rajesh"]).json()
    assert out["status"] == "COMMITTED"


# --- C: the tenant didn't show ---


def test_broker_at_the_flat_claims_a_no_show_and_the_fee_is_captured(client, keys, booked, fake_orders):
    _, visit, _ = booked
    here = post(client, f"/broker/visits/{visit['id']}/checkin", keys["broker_rajesh"], AT_FLAT).json()
    assert here["proof"]["broker_at_flat"] and here["message"] is None
    out = post(client, f"/broker/visits/{visit['id']}/claim-no-show", keys["broker_rajesh"]).json()
    assert (out["status"], out["outcome"]) == ("CAPTURED", "TENANT_NO_SHOW")
    [closable] = client.get("/ledger/closable", headers=keys["broker_rajesh"]).json()
    assert closable["holds"] == []  # a no-show fee is nobody's token for a move-in invoice


def test_a_no_show_claimed_from_far_away_is_not_accepted(client, keys, booked):
    _, visit, _ = booked
    far = post(client, f"/broker/visits/{visit['id']}/checkin", keys["broker_rajesh"], FAR).json()
    assert "m from the flat" in far["message"] and not far["proof"]["broker_at_flat"]
    out = post(client, f"/broker/visits/{visit['id']}/claim-no-show", keys["broker_rajesh"]).json()
    assert out["status"] == "COMMITTED" and "own GPS" in out["proof"]["waiting_for"]


def test_a_pin_blocks_a_no_show_claim(client, keys, booked):
    _, visit, pin = booked
    post(client, f"/broker/visits/{visit['id']}/pin", keys["broker_rajesh"], {"pin": pin})
    post(client, f"/broker/visits/{visit['id']}/checkin", keys["broker_rajesh"], AT_FLAT)
    assert post(client, f"/broker/visits/{visit['id']}/claim-no-show", keys["broker_rajesh"]).json()["status"] == "COMMITTED"


# --- D: the broker didn't show ---


def test_tenant_gps_and_photo_release_the_hold_and_flag_the_listing(client, keys, booked, db_session):
    listing_id, visit, _ = booked
    broker = db_session.scalar(select(User).where(User.id == db_session.get(Listing, listing_id).broker_id))
    trust_before = broker.trust_score
    post(client, f"/public/visits/{visit['id']}/checkin", tenant(visit), AT_FLAT)
    photo = client.post(f"/public/visits/{visit['id']}/photo", headers=tenant(visit),
                        data={"latitude": BLR_PIN[0], "longitude": BLR_PIN[1], "accuracy_m": 10},
                        files={"file": ("door.jpg", photo_bytes(7), "image/jpeg")})
    assert photo.status_code == 200, photo.text
    assert photo.json()["proof"]["tenant_photo"]
    out = post(client, f"/public/visits/{visit['id']}/decision", tenant(visit), {"choice": "BROKER_ABSENT"}).json()
    assert (out["status"], out["outcome"]) == ("VOIDED", "BROKER_NO_SHOW")
    db_session.expire_all()
    assert db_session.get(Listing, listing_id).status is ListingStatus.EXPIRED
    assert db_session.get(User, broker.id).trust_score == trust_before - 10


def test_a_photo_without_a_location_is_refused(client, keys, booked):
    _, visit, _ = booked
    resp = client.post(f"/public/visits/{visit['id']}/photo", headers=tenant(visit),
                       files={"file": ("door.jpg", photo_bytes(8), "image/jpeg")})
    assert resp.status_code == 422 and "location" in resp.json()["detail"]


# --- nobody proved anything; the deadline ---


def test_manager_settle_now_applies_the_default(client, keys, booked, fake_orders):
    _, visit, _ = booked
    assert post(client, f"/manager/visits/{visit['id']}/settle-now", keys["broker_rajesh"]).status_code == 403
    out = post(client, f"/manager/visits/{visit['id']}/settle-now", keys["manager"]).json()
    assert (out["status"], out["outcome"]) == ("VOIDED", "NO_PROOF")


def test_the_worker_settles_visits_past_their_deadline(client, keys, booked, fake_orders, db_session):
    _, visit, _ = booked
    later = datetime.now(UTC) + timedelta(hours=14)
    assert escrow_service.settle_due(db_session, later) == 1
    assert db_session.get(Visit, visit["id"]).status is VisitStatus.SETTLING
    assert outbox.run_due(db_session, None, later, orders=fake_orders) == 1
    assert db_session.get(Visit, visit["id"]).status is VisitStatus.VOIDED
    assert escrow_service.settle_due(db_session, later) == 0


def test_paypal_down_at_settlement_leaves_one_job_that_finishes_once(client, keys, booked, fake_orders, db_session):
    listing_id, visit, pin = booked
    post(client, f"/broker/visits/{visit['id']}/pin", keys["broker_rajesh"], {"pin": pin})
    post(client, f"/public/visits/{visit['id']}/decision", tenant(visit), {"choice": "LOCK"})
    fake_orders.fail_times = 1
    out = post(client, f"/broker/visits/{visit['id']}/approve-lock", keys["broker_rajesh"]).json()
    assert out["status"] == "SETTLING"
    job = db_session.scalar(select(OutboxJob).where(OutboxJob.ref_id == visit["id"]))
    assert job.status is JobStatus.PENDING and "SERVICE_UNAVAILABLE" in job.last_error
    later = datetime.now(UTC) + timedelta(hours=1)
    outbox.run_due(db_session, None, later, orders=fake_orders)
    outbox.run_due(db_session, None, later, orders=fake_orders)
    assert db_session.get(Visit, visit["id"]).status is VisitStatus.CAPTURED
    assert len(fake_orders.captures) == 1 and ledger_kinds(db_session, listing_id).count("HOLD_CAPTURED") == 1


def test_check_ins_outside_the_visit_window_are_kept_but_do_not_count(client, keys, booked, db_session):
    _, visit, _ = booked
    at_the_door(db_session, visit["id"], started_minutes_ago=-180)  # starts in 3 hours
    out = post(client, f"/public/visits/{visit['id']}/checkin", tenant(visit), AT_FLAT).json()
    assert "30 minutes before" in out["message"] and not out["proof"]["tenant_at_flat"]
    assert len(out["proof"]["log"]) == 1


def test_evidence_is_append_only(client, keys, booked, db_session):
    from app.db.models import LedgerIsAppendOnly

    _, visit, _ = booked
    post(client, f"/public/visits/{visit['id']}/checkin", tenant(visit), AT_FLAT)
    row = db_session.scalar(select(VisitCheckin))
    row.distance_m = 0
    with pytest.raises(LedgerIsAppendOnly):
        db_session.commit()
    db_session.rollback()


def test_the_sweep_expires_old_holds_only(client, keys, db_session):
    listing_id, _ = live_listing(client, keys)
    old = held(client, listing_id)
    db_session.get(Visit, old["id"]).expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    assert visit_service.sweep_expired(db_session) == 1
    assert visit_service.sweep_expired(db_session) == 0


# --- webhooks: they confirm what the outbox did, idempotently, and never decide ---


def _webhook(client, webhook_verifier, event, sign=True):
    import json as _json

    from app.adapters.paypal_invoicing import DEMO_SIGNATURE_HEADER, DemoWebhookVerifier, demo_signature

    webhook_verifier["verifier"] = DemoWebhookVerifier("wh-secret")
    raw = _json.dumps(event).encode()
    headers = {DEMO_SIGNATURE_HEADER: demo_signature(raw, "wh-secret")} if sign else {}
    return client.post("/webhooks/paypal", content=raw, headers=headers)


def test_capture_webhook_after_the_job_counts_once(client, keys, booked, db_session, webhook_verifier):
    listing_id, visit, _ = booked
    post(client, f"/broker/visits/{visit['id']}/checkin", keys["broker_rajesh"], AT_FLAT)
    out = post(client, f"/broker/visits/{visit['id']}/claim-no-show", keys["broker_rajesh"]).json()
    event = {"id": "WH-CAP-1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {
        "id": out["capture_ref"], "status": "COMPLETED",
        "supplementary_data": {"related_ids": {"authorization_id": out["authorization_ref"]}}}}
    assert _webhook(client, webhook_verifier, event, sign=False).status_code == 400
    assert _webhook(client, webhook_verifier, event).json()["outcome"] == "applied"
    assert _webhook(client, webhook_verifier, event).json()["outcome"] == "duplicate"
    assert ledger_kinds(db_session, listing_id).count("HOLD_CAPTURED") == 1


def test_capture_webhook_finishes_a_settling_visit(client, keys, booked, fake_orders, db_session, webhook_verifier):
    _, visit, _ = booked
    post(client, f"/broker/visits/{visit['id']}/checkin", keys["broker_rajesh"], AT_FLAT)
    fake_orders.fail_times = 1  # our capture call "failed", but PayPal did capture
    out = post(client, f"/broker/visits/{visit['id']}/claim-no-show", keys["broker_rajesh"]).json()
    assert out["status"] == "SETTLING"
    event = {"id": "WH-CAP-2", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {
        "id": "PAYPAL-CAP-9", "supplementary_data": {"related_ids": {"authorization_id": out["authorization_ref"]}}}}
    _webhook(client, webhook_verifier, event)
    db_session.expire_all()
    row = db_session.get(Visit, visit["id"])
    assert (row.status, row.capture_ref) == (VisitStatus.CAPTURED, "PAYPAL-CAP-9")


def test_void_webhook_for_an_unsettled_visit_releases_it(client, keys, booked, db_session, webhook_verifier):
    listing_id, visit, _ = booked
    auth = db_session.get(Visit, visit["id"]).authorization_ref
    event = {"id": "WH-VOID-1", "event_type": "PAYMENT.AUTHORIZATION.VOIDED", "resource": {"id": auth, "status": "VOIDED"}}
    assert _webhook(client, webhook_verifier, event).json()["outcome"] == "applied"
    db_session.expire_all()
    assert db_session.get(Visit, visit["id"]).status is VisitStatus.VOIDED
    assert ledger_kinds(db_session, listing_id) == ["HOLD_AUTHORIZED", "HOLD_VOIDED"]
    late = post(client, f"/public/visits/{visit['id']}/decision", tenant(visit), {"choice": "PASS"})
    assert late.status_code == 409
