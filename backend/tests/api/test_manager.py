from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Role

PROPERTY = {
    "id": "PROP-BLR-003",
    "owner_name": "Anita Rao",
    "address": "5th Cross, Koramangala 4th Block",
    "city": "Bengaluru",
    "state": "Karnataka",
    "postal_code": "560034",
    "latitude": "12.9352000",
    "longitude": "77.6245000",
    "furnishing": "Semi-Furnished",
}


@pytest.fixture
def manager(make_user):
    _, key = make_user(Role.MANAGER, name="XYZ Desk")
    return {"X-API-Key": key}


@pytest.fixture
def broker(make_user):
    user, key = make_user(Role.BROKER, name="Rajesh")
    return user, {"X-API-Key": key}


def _mandate(property_id, broker_id, **overrides):
    body = {
        "property_id": property_id,
        "broker_id": broker_id,
        "min_rent": "32000",
        "max_rent": "35000",
        "max_deposit": "70000",
        "valid_until": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
    }
    body.update(overrides)
    return body


def test_manager_registers_a_property(client, manager):
    response = client.post("/manager/properties", json=PROPERTY, headers=manager)

    assert response.status_code == 201
    assert response.json()["latitude"] == "12.9352000"  # Decimal stays exact (sent as a string)


def test_same_property_twice_is_409(client, manager):
    client.post("/manager/properties", json=PROPERTY, headers=manager)

    assert client.post("/manager/properties", json=PROPERTY, headers=manager).status_code == 409


def test_broker_cannot_register_properties(client, broker):
    _, headers = broker

    assert client.post("/manager/properties", json=PROPERTY, headers=headers).status_code == 403


def test_invalid_coordinates_are_422(client, manager):
    bad = {**PROPERTY, "latitude": "123.0"}

    assert client.post("/manager/properties", json=bad, headers=manager).status_code == 422


def test_manager_gives_broker_a_mandate(client, manager, broker):
    broker_user, _ = broker
    client.post("/manager/properties", json=PROPERTY, headers=manager)

    response = client.post("/manager/mandates", json=_mandate(PROPERTY["id"], broker_user.id), headers=manager)

    assert response.status_code == 201
    assert response.json()["status"] == "ACTIVE"
    assert response.json()["max_brokerage_months"] == "1.00"


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_rent": "30000"},  # below min_rent
        {"min_rent": "-1"},
        {"valid_until": (datetime.now(UTC) - timedelta(days=1)).isoformat()},  # in the past
        {"valid_until": "2027-01-01T00:00:00"},  # no timezone
        {"max_brokerage_months": "5"},
    ],
)
def test_invalid_mandates_are_422(client, manager, broker, overrides):
    broker_user, _ = broker
    client.post("/manager/properties", json=PROPERTY, headers=manager)

    body = _mandate(PROPERTY["id"], broker_user.id, **overrides)

    assert client.post("/manager/mandates", json=body, headers=manager).status_code == 422


def test_mandate_must_go_to_a_broker(client, manager, make_user):
    other_manager, _ = make_user(Role.MANAGER, name="Another Manager")
    client.post("/manager/properties", json=PROPERTY, headers=manager)

    body = _mandate(PROPERTY["id"], other_manager.id)

    assert client.post("/manager/mandates", json=body, headers=manager).status_code == 422


def test_second_active_mandate_for_same_broker_and_flat_is_409(client, manager, broker):
    broker_user, _ = broker
    client.post("/manager/properties", json=PROPERTY, headers=manager)
    body = _mandate(PROPERTY["id"], broker_user.id)
    client.post("/manager/mandates", json=body, headers=manager)

    assert client.post("/manager/mandates", json=body, headers=manager).status_code == 409


def test_revoked_mandate_frees_the_slot(client, manager, broker):
    broker_user, _ = broker
    client.post("/manager/properties", json=PROPERTY, headers=manager)
    body = _mandate(PROPERTY["id"], broker_user.id)
    first = client.post("/manager/mandates", json=body, headers=manager).json()

    revoked = client.post(f"/manager/mandates/{first['id']}/revoke", headers=manager)

    assert revoked.json()["status"] == "REVOKED"
    assert client.post("/manager/mandates", json=body, headers=manager).status_code == 201
    listed = client.get("/manager/mandates", params={"property_id": PROPERTY["id"]}, headers=manager).json()
    assert [m["status"] for m in listed] == ["REVOKED", "ACTIVE"]


def test_manager_lists_brokers(client, manager, broker):
    response = client.get("/manager/brokers", headers=manager)

    assert [b["name"] for b in response.json()] == ["Rajesh"]
