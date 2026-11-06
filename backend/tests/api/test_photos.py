"""Phase 6: photo uploads and Check A end to end."""

import pytest
from sqlalchemy import select

from app.db.models import ListingPhoto
from app.db.seed import seed
from app.domain.photo_rules import PhotoInspection
from tests.conftest import BLR_PIN, CHN_PIN, photo_bytes, upload_photo

AD = {
    "title": "2BHK in Indiranagar",
    "description": "Unfurnished 2BHK on 12th Main, east facing, covered parking.",
    "rent": "35000",
    "deposit": "70000",
    "furnishing": "Unfurnished",
}
SAME_FLAT = PhotoInspection(same_flat=True, watermark=False, furniture_level="Unfurnished", confidence=0.9)


@pytest.fixture
def keys(db_session):
    return {name: {"X-API-Key": key} for name, key in seed(db_session).items()}


def _open(client, headers, property_id="PROP-BLR-001"):
    return client.post("/broker/listings", json={"property_id": property_id}, headers=headers).json()["id"]


def _submit(client, headers, listing_id, body=AD):
    return client.post(f"/broker/listings/{listing_id}/submit", json=body, headers=headers).json()


def _photos_check(report):
    [check] = [c for c in report["checks"] if c["check"] == "photos"]
    return check


def _failed(report):
    return [e["field"] for e in _photos_check(report)["evidence"] if e["ok"] is False]


# --- uploads ---

def test_upload_stores_photo_with_device_gps(client, keys):
    listing_id = _open(client, keys["broker_rajesh"])

    response = upload_photo(client, keys["broker_rajesh"], listing_id)

    assert response.status_code == 201
    [photo] = response.json()
    assert (photo["kind"], photo["gps_source"], photo["latitude"]) == ("CHECKIN", "device", "12.9718910")
    listed = client.get(f"/broker/listings/{listing_id}/photos", headers=keys["broker_rajesh"]).json()
    assert [p["id"] for p in listed] == [photo["id"]]


def test_exif_gps_is_used_when_the_device_sends_none(client, keys):
    listing_id = _open(client, keys["broker_rajesh"])

    response = upload_photo(client, keys["broker_rajesh"], listing_id, pin=None, gps=(12.97189, 77.64115))

    assert response.json()[0]["gps_source"] == "exif"
    assert _submit(client, keys["broker_rajesh"], listing_id)["report"]["decision"] == "VERIFIED"


@pytest.mark.parametrize(
    ("files", "data", "message"),
    [
        ([("files", ("a.gif", b"GIF89a....", "image/gif"))], {}, "a.gif: not a readable image"),
        ([("files", ("a.jpg", photo_bytes(1), "image/jpeg"))], {"latitude": "12.97"}, "both latitude and longitude"),
        ([("files", (f"{n}.jpg", photo_bytes(n), "image/jpeg")) for n in range(21)], {}, "at most 20 photos"),
    ],
    ids=["not-an-image", "half-a-location", "too-many"],
)
def test_bad_uploads_are_422(client, keys, files, data, message):
    listing_id = _open(client, keys["broker_rajesh"])

    response = client.post(f"/broker/listings/{listing_id}/photos", data=data, files=files, headers=keys["broker_rajesh"])

    assert response.status_code == 422
    assert message in response.text


def test_other_brokers_cannot_add_photos(client, keys):
    listing_id = _open(client, keys["broker_rajesh"])

    assert upload_photo(client, keys["broker_quickcash"], listing_id).status_code == 404


def test_photos_are_frozen_once_verified(client, keys):
    listing_id = _open(client, keys["broker_rajesh"])
    upload_photo(client, keys["broker_rajesh"], listing_id)
    _submit(client, keys["broker_rajesh"], listing_id)

    assert upload_photo(client, keys["broker_rajesh"], listing_id, seed=2).status_code == 409


def test_manager_uploads_baseline_photos_and_brokers_cannot(client, keys):
    files = [("files", ("living.jpg", photo_bytes(50), "image/jpeg"))]
    url = "/manager/properties/PROP-BLR-001/photos"

    created = client.post(url, data={"room_type": "living"}, files=files, headers=keys["manager"])

    assert created.status_code == 201
    assert created.json()[0]["room_type"] == "living"
    assert client.post(url, files=files, headers=keys["broker_rajesh"]).status_code == 403


# --- Check A, end to end ---

def test_good_checkin_is_verified_and_the_photo_is_recorded(client, keys, db_session):
    listing_id = _open(client, keys["broker_rajesh"])
    [photo] = upload_photo(client, keys["broker_rajesh"], listing_id).json()

    report = _submit(client, keys["broker_rajesh"], listing_id)["report"]

    assert report["decision"] == "VERIFIED"
    assert report["photos"] == [{"id": photo["id"], "kind": "CHECKIN", "phash": photo["phash"]}]
    assert db_session.get(ListingPhoto, photo["id"]).version_id is not None


def test_no_checkin_photo_needs_fix(client, keys):
    listing_id = _open(client, keys["broker_rajesh"])

    report = _submit(client, keys["broker_rajesh"], listing_id)["report"]

    assert report["decision"] == "NEEDS_FIX"
    assert _failed(report) == ["check-in photo"]


def test_checkin_taken_2_km_away_needs_fix(client, keys):
    listing_id = _open(client, keys["broker_rajesh"])
    far = ("12.9898910", "77.6411510")  # 0.018 degrees north: about 2 km
    upload_photo(client, keys["broker_rajesh"], listing_id, pin=far)

    report = _submit(client, keys["broker_rajesh"], listing_id)["report"]

    assert report["decision"] == "NEEDS_FIX"
    [row] = [e for e in _photos_check(report)["evidence"] if e["field"] == "photo 1 (checkin) location"]
    assert row["claimed"] == "2002 m from the flat"


def test_old_checkin_photo_needs_fix(client, keys):
    listing_id = _open(client, keys["broker_rajesh"])
    upload_photo(client, keys["broker_rajesh"], listing_id, taken="2026:08:01 10:00:00")

    report = _submit(client, keys["broker_rajesh"], listing_id)["report"]

    assert _failed(report) == ["photo 1 (checkin) age"]


def test_photo_already_used_for_another_flat_needs_fix(client, keys):
    # Rajesh's real Indiranagar photo...
    blr = _open(client, keys["broker_rajesh"])
    upload_photo(client, keys["broker_rajesh"], blr, seed=7)
    # ...reused by QuickCash as an extra photo of the Chennai flat.
    chn = _open(client, keys["broker_quickcash"], "PROP-CHN-002")
    upload_photo(client, keys["broker_quickcash"], chn, seed=8, pin=CHN_PIN)
    upload_photo(client, keys["broker_quickcash"], chn, seed=7, kind="EXTRA", pin=None)

    body = {**AD, "rent": "40000", "deposit": "120000", "furnishing": "Semi-Furnished"}
    report = _submit(client, keys["broker_quickcash"], chn, body)["report"]

    assert report["decision"] == "NEEDS_FIX"
    [row] = [e for e in _photos_check(report)["evidence"] if e["field"] == "photo 2 (extra) reuse"]
    assert (row["claimed"], row["ok"]) == ("copy of a photo of PROP-BLR-001", False)


def test_extra_photo_without_vision_goes_to_review(client, keys):
    listing_id = _open(client, keys["broker_rajesh"])
    upload_photo(client, keys["broker_rajesh"], listing_id)
    upload_photo(client, keys["broker_rajesh"], listing_id, seed=2, kind="EXTRA", pin=None)

    report = _submit(client, keys["broker_rajesh"], listing_id)["report"]

    assert report["decision"] == "IN_REVIEW"
    assert _photos_check(report)["verdict"] == "UNVERIFIABLE"


def test_extra_photo_of_the_same_flat_is_verified(client, keys, fake_vision):
    fake_vision.inspection = SAME_FLAT
    client.post("/manager/properties/PROP-BLR-001/photos",
                files=[("files", ("owner.jpg", photo_bytes(60), "image/jpeg"))], headers=keys["manager"])
    listing_id = _open(client, keys["broker_rajesh"])
    upload_photo(client, keys["broker_rajesh"], listing_id)
    upload_photo(client, keys["broker_rajesh"], listing_id, seed=2, kind="EXTRA", pin=None)

    report = _submit(client, keys["broker_rajesh"], listing_id)["report"]

    assert report["decision"] == "VERIFIED"
    [(photo_path, refs)] = fake_vision.calls
    assert len(refs) == 1  # compared with the owner's baseline photo


def test_removed_photo_is_left_out_of_the_next_audit(client, keys, db_session):
    listing_id = _open(client, keys["broker_rajesh"])
    [far] = upload_photo(client, keys["broker_rajesh"], listing_id, pin=("12.99", "77.64")).json()
    assert _submit(client, keys["broker_rajesh"], listing_id)["report"]["decision"] == "NEEDS_FIX"

    removed = client.delete(f"/broker/listings/{listing_id}/photos/{far['id']}", headers=keys["broker_rajesh"])
    upload_photo(client, keys["broker_rajesh"], listing_id, seed=3, pin=BLR_PIN)
    report = _submit(client, keys["broker_rajesh"], listing_id)["report"]

    assert removed.status_code == 204
    assert report["decision"] == "VERIFIED"
    assert far["id"] not in [p["id"] for p in report["photos"]]
    # Still on record as evidence for version 1.
    assert db_session.scalar(select(ListingPhoto).where(ListingPhoto.id == far["id"])).removed_at is not None
