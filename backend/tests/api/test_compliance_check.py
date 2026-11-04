"""Phase 5: Check C runs on every submit and its advisories reach the stored report."""

from decimal import Decimal

from sqlalchemy import select

from app.db.models import Mandate
from app.db.seed import seed


def _open(client, headers, property_id):
    return client.post("/broker/listings", json={"property_id": property_id}, headers=headers).json()["id"]


def _submit(client, headers, listing_id, **terms):
    body = {
        "title": "Spacious 2BHK flat",
        "description": "East facing 2BHK with covered parking.",
        "brokerage_fee": "0",
        **terms,
    }
    return client.post(f"/broker/listings/{listing_id}/submit", json=body, headers=headers).json()


def _compliance(report):
    [check] = [c for c in report["checks"] if c["check"] == "compliance"]
    return check


def test_chennai_six_month_deposit_needs_fix_with_citation(client, db_session):
    quickcash = {"X-API-Key": seed(db_session)["broker_quickcash"]}
    listing_id = _open(client, quickcash, "PROP-CHN-002")

    body = _submit(client, quickcash, listing_id, rent="40000", deposit="240000", furnishing="Semi-Furnished")

    report = body["report"]
    assert report["decision"] == "NEEDS_FIX"
    check = _compliance(report)
    assert check["verdict"] == "FAIL"
    assert "s.11(1)" in check["evidence"][0]["note"]
    stored = client.get(f"/audits/{body['audit_id']}", headers=quickcash).json()["report"]
    assert _compliance(stored) == check


def test_bengaluru_four_month_deposit_is_verified_with_the_advisory_kept(client, db_session):
    keys = seed(db_session)
    rajesh = {"X-API-Key": keys["broker_rajesh"]}
    # The owner of this flat agreed to a 4-month deposit, so Check D allows it too.
    for mandate in db_session.scalars(select(Mandate).where(Mandate.property_id == "PROP-BLR-001")):
        mandate.max_deposit = Decimal("140000")
    db_session.commit()
    listing_id = _open(client, rajesh, "PROP-BLR-001")

    body = _submit(client, rajesh, listing_id, rent="35000", deposit="140000", furnishing="Unfurnished")

    assert body["report"]["decision"] == "VERIFIED"
    check = _compliance(body["report"])
    assert check["verdict"] == "PASS"
    assert "Advisory: above 3 months, shown to the tenant." in check["summary"]
    assert check["evidence"][0]["note"].startswith("ADVISORY:")
