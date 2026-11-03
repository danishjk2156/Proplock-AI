"""Phase 2 slice: open a listing, submit it, Check D decides, the audit is stored and read back."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import Audit, Listing, ListingVersion, Mandate, MandateStatus
from app.db.seed import seed
from app.services import audit_service

BLR = "PROP-BLR-001"  # seeded: owner agreed 32k-35k, deposit <= 70k, unfurnished, 1 month brokerage
CHN = "PROP-CHN-002"  # seeded: only QuickCash has a mandate

HONEST = {
    "title": "2BHK in Indiranagar",
    "description": "Unfurnished 2BHK near 12th Main, east facing, covered parking.",
    "rent": "35000",
    "deposit": "70000",
    "brokerage_fee": "35000",
    "furnishing": "Unfurnished",
}
INFLATED = {**HONEST, "rent": "40000", "furnishing": "Fully Furnished"}


@pytest.fixture
def keys(db_session):
    k = seed(db_session)
    return {name: {"X-API-Key": key} for name, key in k.items()}


def _open(client, headers, property_id=BLR):
    return client.post("/broker/listings", json={"property_id": property_id}, headers=headers)


def _submit(client, headers, listing_id, body):
    return client.post(f"/broker/listings/{listing_id}/submit", json=body, headers=headers)


def _failed(report):
    [check] = report["checks"]
    return [e["field"] for e in check["evidence"] if not e["ok"]]


def test_honest_listing_is_verified_and_read_back_exactly(client, keys):
    listing = _open(client, keys["broker_rajesh"]).json()

    submitted = _submit(client, keys["broker_rajesh"], listing["id"], HONEST)

    assert submitted.status_code == 200
    body = submitted.json()
    assert body["listing_status"] == "VERIFIED"
    assert body["report"]["decision"] == "VERIFIED"
    assert body["report"]["checks"][0]["check"] == "ground_truth"
    stored = client.get(f"/audits/{body['audit_id']}", headers=keys["broker_rajesh"]).json()
    assert stored["status"] == "VERIFIED"
    assert stored["report"] == body["report"]


def test_inflated_listing_needs_fix_then_resubmit_is_verified(client, keys):
    listing = _open(client, keys["broker_quickcash"]).json()

    first = _submit(client, keys["broker_quickcash"], listing["id"], INFLATED).json()
    second = _submit(client, keys["broker_quickcash"], listing["id"], HONEST).json()

    assert first["listing_status"] == "NEEDS_FIX"
    assert _failed(first["report"]) == ["rent", "furnishing"]
    assert second["listing_status"] == "VERIFIED"
    assert [first["report"]["version_no"], second["report"]["version_no"]] == [1, 2]
    # The failed version 1 audit is kept as evidence and still reads back unchanged.
    old = client.get(f"/audits/{first['audit_id']}", headers=keys["manager"]).json()
    assert old["report"] == first["report"]


def test_no_mandate_for_the_flat_is_403(client, keys):
    assert _open(client, keys["broker_rajesh"], CHN).status_code == 403


@pytest.mark.parametrize(
    "spoil",
    [
        lambda m: setattr(m, "status", MandateStatus.REVOKED),
        lambda m: setattr(m, "valid_until", datetime.now(UTC) - timedelta(minutes=1)),
    ],
    ids=["revoked", "expired"],
)
def test_mandate_lost_after_opening_blocks_submit(client, keys, db_session, spoil):
    listing = _open(client, keys["broker_rajesh"]).json()
    spoil(db_session.get(Mandate, listing["mandate_id"]))
    db_session.commit()

    response = _submit(client, keys["broker_rajesh"], listing["id"], HONEST)

    assert response.status_code == 403
    assert db_session.scalar(select(func.count()).select_from(ListingVersion)) == 0


def test_expired_mandate_cannot_open_a_listing(client, keys, db_session):
    for m in db_session.scalars(select(Mandate).where(Mandate.property_id == BLR)):
        m.valid_until = datetime.now(UTC) - timedelta(days=1)
    db_session.commit()

    assert _open(client, keys["broker_rajesh"]).status_code == 403


def test_second_open_listing_for_same_flat_is_409(client, keys):
    _open(client, keys["broker_rajesh"])

    assert _open(client, keys["broker_rajesh"]).status_code == 409


def test_verified_listing_cannot_be_resubmitted_yet(client, keys):
    listing = _open(client, keys["broker_rajesh"]).json()
    _submit(client, keys["broker_rajesh"], listing["id"], HONEST)

    assert _submit(client, keys["broker_rajesh"], listing["id"], HONEST).status_code == 409


def test_brokers_only_see_their_own_listings_and_audits(client, keys):
    listing = _open(client, keys["broker_rajesh"]).json()
    audit_id = _submit(client, keys["broker_rajesh"], listing["id"], HONEST).json()["audit_id"]

    assert _submit(client, keys["broker_quickcash"], listing["id"], HONEST).status_code == 404
    assert client.get(f"/audits/{audit_id}", headers=keys["broker_quickcash"]).status_code == 404
    assert client.get(f"/audits/{audit_id}", headers=keys["manager"]).status_code == 200


def test_managers_cannot_submit(client, keys):
    assert _open(client, keys["manager"]).status_code == 403


def test_invalid_content_is_422(client, keys):
    listing = _open(client, keys["broker_rajesh"]).json()

    response = _submit(client, keys["broker_rajesh"], listing["id"], {**HONEST, "rent": "0"})

    assert response.status_code == 422


def test_a_crash_after_the_version_is_written_saves_nothing(client, keys, db_session, monkeypatch):
    listing = _open(client, keys["broker_rajesh"]).json()

    def boom(**_):
        raise RuntimeError("crashed while building the report")

    # AuditReport is built after the version row has been flushed to the database.
    monkeypatch.setattr(audit_service, "AuditReport", boom)
    with pytest.raises(RuntimeError):
        _submit(client, keys["broker_rajesh"], listing["id"], HONEST)

    assert db_session.scalar(select(func.count()).select_from(ListingVersion)) == 0
    assert db_session.scalar(select(func.count()).select_from(Audit)) == 0
    db_session.expire_all()
    assert db_session.get(Listing, listing["id"]).status.value == "DRAFT"
