"""Phase 8.1: the manager review queue."""

import pytest
from sqlalchemy import select

from app.db.models import Audit, TrustEvent
from app.db.seed import seed
from tests.conftest import upload_photo

# "metro" names no station, so Check B can't look it up: the listing goes to review.
AD = {
    "title": "2BHK, 10 min walk to metro",
    "description": "Unfurnished 2BHK on 12th Main, east facing, covered parking.",
    "rent": "35000",
    "deposit": "70000",
    "furnishing": "Unfurnished",
    "commute_claims": [{"destination": "metro", "minutes": 10, "mode": "walking"}],
}


@pytest.fixture
def keys(db_session):
    return {name: {"X-API-Key": key} for name, key in seed(db_session).items()}


@pytest.fixture
def in_review(client, keys):
    listing_id = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=keys["broker_rajesh"]).json()["id"]
    upload_photo(client, keys["broker_rajesh"], listing_id)
    body = client.post(f"/broker/listings/{listing_id}/submit", json=AD, headers=keys["broker_rajesh"]).json()
    assert body["listing_status"] == "IN_REVIEW"
    return listing_id, body["audit_id"]


def _decide(client, keys, audit_id, approve, note="Checked with the owner by phone."):
    return client.post(f"/manager/reviews/{audit_id}", json={"approve": approve, "note": note}, headers=keys["manager"])


def test_queue_lists_what_could_not_be_checked(client, keys, in_review):
    listing_id, audit_id = in_review

    [item] = client.get("/manager/reviews", headers=keys["manager"]).json()

    assert (item["audit_id"], item["listing_id"], item["broker_name"]) == (audit_id, listing_id, "Rajesh")
    [question] = item["open_questions"]
    assert question.startswith("commute: commute: metro (walking): No route data")


def test_brokers_cannot_see_or_decide_reviews(client, keys, in_review):
    _, audit_id = in_review

    assert client.get("/manager/reviews", headers=keys["broker_rajesh"]).status_code == 403
    assert _decide(client, {"manager": keys["broker_rajesh"]}, audit_id, True).status_code == 403


def test_approval_mints_and_goes_live_without_touching_the_signed_report(client, keys, in_review, db_session, fake_paypal):
    _, audit_id = in_review
    before = db_session.get(Audit, audit_id).report_json

    response = _decide(client, keys, audit_id, approve=True)

    assert response.json() == {"audit_id": audit_id, "audit_status": "VERIFIED", "listing_status": "LIVE"}
    audit = db_session.get(Audit, audit_id)
    db_session.refresh(audit)
    assert audit.report_json == before  # the decision sits next to the report, not inside it
    assert audit.review_note == "Checked with the owner by phone."
    assert len(fake_paypal.calls) == 1
    assert client.get(f"/public/audits/{audit_id}/verify").json()["signature_valid"] is True
    assert client.get("/manager/reviews", headers=keys["manager"]).json() == []


def test_rejection_sends_the_listing_back_to_the_broker(client, keys, in_review, db_session):
    listing_id, audit_id = in_review

    response = _decide(client, keys, audit_id, approve=False, note="Name the station, please.")

    assert response.json()["audit_status"] == "REJECTED"
    assert response.json()["listing_status"] == "NEEDS_FIX"
    resubmit = client.post(
        f"/broker/listings/{listing_id}/submit",
        json={**AD, "commute_claims": [], "title": "2BHK in Indiranagar"},
        headers=keys["broker_rajesh"],
    )
    assert resubmit.json()["report"]["version_no"] == 2


def test_an_audit_can_be_decided_only_once(client, keys, in_review):
    _, audit_id = in_review
    _decide(client, keys, audit_id, approve=False)

    assert _decide(client, keys, audit_id, approve=True).status_code == 409


def test_a_note_is_required(client, keys, in_review):
    _, audit_id = in_review

    assert _decide(client, keys, audit_id, approve=True, note="").status_code == 422


def test_approval_records_the_first_try_trust_points(client, keys, in_review, db_session):
    _, audit_id = in_review

    _decide(client, keys, audit_id, approve=True)

    kinds = db_session.scalars(select(TrustEvent.kind)).all()
    assert kinds == ["verified_first_try"]
