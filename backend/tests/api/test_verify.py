"""Phase 7: anyone can check an audit, and any change to it, or to the rows it describes, shows."""

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import Audit, ListingPhoto, ListingVersion, Mandate
from app.db.seed import seed
from tests.conftest import upload_photo

AD = {
    "title": "2BHK in Indiranagar",
    "description": "Unfurnished 2BHK on 12th Main, east facing, covered parking.",
    "rent": "35000",
    "deposit": "70000",
    "furnishing": "Unfurnished",
}


@pytest.fixture
def audit(client, db_session):
    headers = {"X-API-Key": seed(db_session)["broker_rajesh"]}
    listing_id = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=headers).json()["id"]
    upload_photo(client, headers, listing_id)
    body = client.post(f"/broker/listings/{listing_id}/submit", json=AD, headers=headers).json()
    return db_session.get(Audit, body["audit_id"])


def _verify(client, audit_id):
    return client.get(f"/public/audits/{audit_id}/verify").json()


def test_untouched_audit_verifies_without_logging_in(client, audit):
    result = _verify(client, audit.id)

    assert result["signature_valid"] is True
    assert result["matches_current_data"] is True
    assert result["mismatches"] == []
    assert result["status"] == "VERIFIED"
    assert result["paypal_product_id"].startswith("PL-")


def test_report_records_everything_that_was_signed(client, audit):
    report = json.loads(audit.report_json)

    assert report["terms"]["rent"] == "35000.00"
    assert report["owner"]["max_rent"] == "35000.00"
    assert report["owner"]["latitude"] == "12.9718910"
    assert len(report["photos"]) == 1 and report["photos"][0]["phash"]
    assert {c["check"] for c in report["checks"]} == {"claims", "commute", "compliance", "ground_truth", "photos"}


def test_editing_the_stored_report_breaks_the_signature(client, audit, db_session):
    audit.report_json = audit.report_json.replace('"35000.00"', '"30000.00"', 1)
    db_session.commit()

    result = _verify(client, audit.id)

    assert result["signature_valid"] is False
    assert result["matches_current_data"] is False


def test_editing_the_rent_row_shows_as_a_mismatch(client, audit, db_session):
    version = db_session.get(ListingVersion, audit.version_id)
    version.rent = Decimal("45000.00")
    db_session.commit()

    result = _verify(client, audit.id)

    assert result["signature_valid"] is True  # the report itself is untouched...
    assert result["matches_current_data"] is False  # ...but the live data no longer agrees with it
    assert result["mismatches"] == ["ad rent: signed 35000.00, now 45000.00"]


def test_swapping_a_photo_shows_as_a_mismatch(client, audit, db_session):
    photo = db_session.scalar(select(ListingPhoto))
    photo.phash = "ffffffffffffffff"
    db_session.commit()

    [mismatch] = _verify(client, audit.id)["mismatches"]

    assert mismatch.startswith(f"photo {photo.id} fingerprint")


def test_owner_terms_changed_after_the_audit_show_as_a_mismatch(client, audit, db_session):
    report = json.loads(audit.report_json)
    db_session.get(Mandate, report["owner"]["mandate_id"]).max_rent = Decimal("30000.00")
    db_session.commit()

    assert _verify(client, audit.id)["mismatches"] == ["owner max_rent: signed 35000.00, now 30000.00"]


def test_failed_audits_are_sealed_too(client, db_session):
    headers = {"X-API-Key": seed(db_session)["broker_quickcash"]}
    listing_id = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=headers).json()["id"]
    body = client.post(f"/broker/listings/{listing_id}/submit", json={**AD, "rent": "40000"}, headers=headers).json()

    result = _verify(client, body["audit_id"])

    assert (result["status"], result["signature_valid"]) == ("NEEDS_FIX", True)
    assert result["paypal_product_id"] is None


def test_unknown_audit_is_404(client):
    assert client.get("/public/audits/nope/verify").status_code == 404
