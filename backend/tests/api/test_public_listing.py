"""Phase 8.3: the public listing page, availability, expiry, rented, and trust points."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import Listing, Mandate, MandateStatus, TrustEvent, User
from app.db.seed import seed
from app.services import listing_service
from tests.conftest import CHN_PIN, photo_bytes, upload_photo

AD = {
    "title": "2BHK in Indiranagar",
    "description": "Unfurnished 2BHK on 12th Main, east facing, covered parking.",
    "rent": "35000",
    "deposit": "70000",
    "brokerage_fee": "35000",
    "furnishing": "Unfurnished",
}


@pytest.fixture
def keys(db_session):
    return {name: {"X-API-Key": key} for name, key in seed(db_session).items()}


def _live(client, keys, broker="broker_rajesh"):
    headers = keys[broker]
    listing_id = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=headers).json()["id"]
    upload_photo(client, headers, listing_id)
    body = client.post(f"/broker/listings/{listing_id}/submit", json=AD, headers=headers).json()
    assert body["listing_status"] == "LIVE"
    return listing_id, body["audit_id"]


def _trust(db_session, name):
    broker = db_session.scalar(select(User).where(User.name == name))
    db_session.refresh(broker)
    return broker.trust_score, db_session.scalars(select(TrustEvent.kind).where(TrustEvent.broker_id == broker.id)).all()


# --- public page ---

def test_public_page_shows_the_signed_terms_and_the_owners_photos(client, keys):
    client.post("/manager/properties/PROP-BLR-001/photos", data={"room_type": "living"},
                files=[("files", ("owner.jpg", photo_bytes(70), "image/jpeg"))], headers=keys["manager"])
    listing_id, audit_id = _live(client, keys)

    page = client.get(f"/public/listings/{listing_id}")

    assert page.status_code == 200
    body = page.json()
    assert (body["rent"], body["deposit"], body["brokerage_fee"]) == ("35000.00", "70000.00", "35000.00")
    assert body["move_in_total"] == "140000.00"  # the whole cost up front
    assert body["verify_url"] == f"/public/audits/{audit_id}/verify"
    assert body["broker"]["name"] == "Rajesh"
    [photo] = body["photos"]
    assert photo["room_type"] == "living"
    assert client.get(photo["url"]).status_code == 200  # the owner's photo, served publicly
    assert body["expires_at"] is not None


def test_listing_that_is_not_live_has_no_public_page(client, keys):
    listing_id = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=keys["broker_rajesh"]).json()["id"]

    assert client.get(f"/public/listings/{listing_id}").status_code == 404
    assert client.get("/public/listings/nope").status_code == 404


def test_badge_comes_only_from_this_listings_own_audit(client, keys):
    # Rajesh is LIVE on this flat; QuickCash's failed listing of the same flat gets no badge (fixes D9).
    _live(client, keys)
    shady = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=keys["broker_quickcash"]).json()["id"]
    upload_photo(client, keys["broker_quickcash"], shady)
    client.post(f"/broker/listings/{shady}/submit", json={**AD, "rent": "40000"}, headers=keys["broker_quickcash"])

    assert client.get(f"/public/listings/{shady}").status_code == 404


# --- availability and expiry ---

def test_confirming_availability_keeps_the_listing_up_for_7_more_days(client, keys, db_session):
    listing_id, _ = _live(client, keys)
    listing = db_session.get(Listing, listing_id)
    listing.last_confirmed_at = datetime.now(UTC) - timedelta(days=6)
    db_session.commit()

    body = client.post(f"/broker/listings/{listing_id}/confirm-availability", headers=keys["broker_rajesh"]).json()

    assert body["status"] == "LIVE"
    assert datetime.fromisoformat(body["last_confirmed_at"]) > datetime.now(UTC) - timedelta(minutes=1)


def test_stale_listing_expires_on_view_and_costs_trust(client, keys, db_session):
    listing_id, _ = _live(client, keys)
    db_session.get(Listing, listing_id).last_confirmed_at = datetime.now(UTC) - timedelta(days=8)
    db_session.commit()

    page = client.get(f"/public/listings/{listing_id}")

    assert page.status_code == 410
    assert db_session.get(Listing, listing_id).status.value == "EXPIRED"
    score, kinds = _trust(db_session, "Rajesh")
    assert kinds == ["verified_first_try", "listing_expired_stale"]
    assert score == 100 + 2 - 3


def test_expired_listing_comes_back_when_confirmed(client, keys, db_session):
    listing_id, _ = _live(client, keys)
    db_session.get(Listing, listing_id).last_confirmed_at = datetime.now(UTC) - timedelta(days=8)
    db_session.commit()
    assert listing_service.expire_stale(db_session) == 1

    client.post(f"/broker/listings/{listing_id}/confirm-availability", headers=keys["broker_rajesh"])

    assert client.get(f"/public/listings/{listing_id}").status_code == 200


def test_revoked_mandate_takes_the_listing_down_without_a_trust_penalty(client, keys, db_session):
    listing_id, _ = _live(client, keys)
    listing = db_session.get(Listing, listing_id)
    db_session.get(Mandate, listing.mandate_id).status = MandateStatus.REVOKED
    db_session.commit()

    assert client.get(f"/public/listings/{listing_id}").status_code == 410
    assert client.post(f"/broker/listings/{listing_id}/confirm-availability",
                       headers=keys["broker_rajesh"]).status_code == 403
    assert _trust(db_session, "Rajesh")[1] == ["verified_first_try"]


def test_rented_listing_leaves_the_public_page(client, keys):
    listing_id, _ = _live(client, keys)

    response = client.post(f"/broker/listings/{listing_id}/mark-rented", headers=keys["broker_rajesh"])

    assert response.json()["status"] == "RENTED"
    assert client.get(f"/public/listings/{listing_id}").status_code == 410
    assert client.post(f"/broker/listings/{listing_id}/confirm-availability",
                       headers=keys["broker_rajesh"]).status_code == 409


def test_draft_cannot_be_confirmed_or_rented(client, keys):
    listing_id = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=keys["broker_rajesh"]).json()["id"]

    assert client.post(f"/broker/listings/{listing_id}/confirm-availability", headers=keys["broker_rajesh"]).status_code == 409
    assert client.post(f"/broker/listings/{listing_id}/mark-rented", headers=keys["broker_rajesh"]).status_code == 409


# --- trust points from audits ---

def test_needs_fix_and_reused_photo_cost_trust(client, keys, db_session):
    # Rajesh's photo of the Indiranagar flat...
    blr = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=keys["broker_rajesh"]).json()["id"]
    upload_photo(client, keys["broker_rajesh"], blr, seed=31)
    # ...reused by QuickCash for the Chennai flat.
    chn = client.post("/broker/listings", json={"property_id": "PROP-CHN-002"}, headers=keys["broker_quickcash"]).json()["id"]
    upload_photo(client, keys["broker_quickcash"], chn, seed=31, pin=CHN_PIN)
    body = {**AD, "rent": "40000", "deposit": "120000", "brokerage_fee": "0", "furnishing": "Semi-Furnished"}
    client.post(f"/broker/listings/{chn}/submit", json=body, headers=keys["broker_quickcash"])

    score, kinds = _trust(db_session, "QuickCash Rentals")
    assert sorted(kinds) == ["needs_fix", "photo_reused_from_another_flat"]
    assert score == 100 - 1 - 15
