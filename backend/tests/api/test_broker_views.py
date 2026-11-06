"""Read endpoints the frontend needs: the broker's mandates and listings, and CORS."""

import pytest

from app.db.seed import seed
from tests.conftest import photo_bytes, upload_photo

AD = {
    "title": "2BHK in Indiranagar",
    "description": "Unfurnished 2BHK on 12th Main, east facing, covered parking.",
    "rent": "35000",
    "deposit": "70000",
    "furnishing": "Unfurnished",
    "commute_claims": [{"destination": "Bagmane Tech Park", "minutes": 30, "mode": "driving"}],
}


@pytest.fixture
def keys(db_session):
    return {name: {"X-API-Key": key} for name, key in seed(db_session).items()}


def test_broker_sees_only_their_own_mandates_with_the_pin(client, keys):
    rajesh = client.get("/broker/mandates", headers=keys["broker_rajesh"]).json()
    quickcash = client.get("/broker/mandates", headers=keys["broker_quickcash"]).json()

    assert [m["property_id"] for m in rajesh] == ["PROP-BLR-001"]
    assert sorted(m["property_id"] for m in quickcash) == ["PROP-BLR-001", "PROP-CHN-002"]
    [m] = rajesh
    assert (m["max_rent"], m["latitude"], m["listing_id"]) == ("35000.00", "12.9718910", None)


def test_listing_shows_up_with_its_latest_audit_and_draft(client, keys):
    headers = keys["broker_rajesh"]
    listing_id = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=headers).json()["id"]

    [draft] = client.get("/broker/listings", headers=headers).json()
    assert (draft["status"], draft["version_no"], draft["ad"], draft["has_checkin"]) == ("DRAFT", None, None, False)

    upload_photo(client, headers, listing_id)
    client.post(f"/broker/listings/{listing_id}/submit", json={**AD, "rent": "40000"}, headers=headers)
    view = client.get(f"/broker/listings/{listing_id}", headers=headers).json()

    assert (view["status"], view["version_no"], view["audit_status"]) == ("NEEDS_FIX", 1, "NEEDS_FIX")
    assert view["problems"] == 1
    assert view["has_checkin"] is True
    assert view["ad"]["rent"] == "40000.00"
    assert view["ad"]["commute_claims"] == [{"destination": "Bagmane Tech Park", "minutes": 30, "mode": "driving"}]
    assert client.get("/broker/mandates", headers=headers).json()[0]["listing_id"] == listing_id


def test_other_brokers_cannot_read_a_listing(client, keys):
    listing_id = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=keys["broker_rajesh"]).json()["id"]

    assert client.get(f"/broker/listings/{listing_id}", headers=keys["broker_quickcash"]).status_code == 404
    assert client.get("/broker/listings", headers=keys["broker_quickcash"]).json() == []


def test_cors_allows_only_the_configured_frontend(client):
    allowed = client.options(
        "/public/listings/x",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    blocked = client.options(
        "/public/listings/x",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )

    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in blocked.headers


def test_manager_lists_owner_photos(client, keys):
    url = "/manager/properties/PROP-BLR-001/photos"
    client.post(url, data={"room_type": "kitchen"}, files=[("files", ("k.jpg", photo_bytes(90), "image/jpeg"))],
                headers=keys["manager"])

    [photo] = client.get(url, headers=keys["manager"]).json()
    assert photo["room_type"] == "kitchen"
    assert client.get(url, headers=keys["broker_rajesh"]).status_code == 403
    assert client.get("/manager/properties/NOPE/photos", headers=keys["manager"]).status_code == 404
