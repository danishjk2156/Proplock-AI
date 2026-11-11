"""Shared setup for Feature 4 tests: a LIVE verified listing and a captured visit token."""

import pytest

from app.config import get_settings
from app.db.seed import seed
from tests.conftest import upload_photo

AD = {
    "title": "2BHK in Indiranagar",
    "description": "Unfurnished 2BHK on 12th Main, east facing, covered parking.",
    "rent": "35000",
    "deposit": "70000",
    "brokerage_fee": "35000",
    "furnishing": "Unfurnished",
}
TENANT = {"tenant_name": "Asha Rao", "tenant_email": "asha@example.com"}


@pytest.fixture
def demo_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_mode", True)
    monkeypatch.setattr(get_settings(), "invoice_currency", "USD")


@pytest.fixture
def keys(db_session):
    return {name: {"X-API-Key": key} for name, key in seed(db_session).items()}


def live_listing(client, keys, broker="broker_rajesh") -> tuple[str, str]:
    headers = keys[broker]
    listing_id = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=headers).json()["id"]
    upload_photo(client, headers, listing_id)
    body = client.post(f"/broker/listings/{listing_id}/submit", json=AD, headers=headers).json()
    assert body["listing_status"] == "LIVE", body
    return listing_id, body["audit_id"]


def capture(client, keys, listing_id, amount="15.00") -> str:
    resp = client.post("/dev/ledger/simulate-capture", json={"listing_id": listing_id, "amount": amount}, headers=keys["manager"])
    assert resp.status_code == 200, resp.text
    return resp.json()["capture_ref"]


def closed(client, keys, fake_invoicing=None) -> dict:
    listing_id, _ = live_listing(client, keys)
    ref = capture(client, keys, listing_id)
    resp = client.post(f"/broker/listings/{listing_id}/close", json={**TENANT, "capture_ref": ref}, headers=keys["broker_rajesh"])
    assert resp.status_code == 201, resp.text
    return resp.json()
