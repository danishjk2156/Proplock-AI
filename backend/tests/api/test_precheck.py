"""Phase 8.2: the broker pre-check: the real checks on a draft, with suggested fixes, nothing saved."""

import pytest
from sqlalchemy import func, select

from app.adapters.llm import ExtractedClaims
from app.api import broker as broker_api
from app.db.models import Audit, CommuteMode, ListingVersion
from app.db.seed import seed
from app.services.rate_limit import RateLimiter

HONEST = {
    "title": "2BHK in Indiranagar",
    "description": "Unfurnished 2BHK on 12th Main, east facing, covered parking.",
    "rent": "35000",
    "deposit": "70000",
    "furnishing": "Unfurnished",
}
SHADY = {**HONEST, "title": "2 MIN WALK TO INDIRANAGAR METRO!!", "rent": "40000", "deposit": "140000"}


@pytest.fixture
def quickcash(client, db_session):
    headers = {"X-API-Key": seed(db_session)["broker_quickcash"]}
    listing_id = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=headers).json()["id"]
    return headers, listing_id


def _precheck(client, quickcash, ad):
    headers, listing_id = quickcash
    return client.post("/broker/precheck", json={"listing_id": listing_id, "ad": ad}, headers=headers)


def test_precheck_finds_the_problems_and_suggests_fixes(client, quickcash, fake_llm, fake_maps):
    fake_llm.claims = ExtractedClaims.model_validate(
        {"commute": [{"destination": "Indiranagar Metro", "minutes": 2, "mode": "walking"}]}
    )
    fake_maps.routes = {("Indiranagar Metro", CommuteMode.WALKING): 11}

    body = _precheck(client, quickcash, SHADY).json()

    assert body["would_be"] == "NEEDS_FIX"
    # Checks run in parallel, so compare as a set. The 4-month deposit is only a Karnataka
    # advisory (no suggestion); the owner's 70k limit is what it breaks.
    assert set(body["suggestions"]) == {
        'Say "11 min walk to Indiranagar Metro" instead of 2 min.',
        "Set the rent between 32000.00 and 35000.00; charge your commission as the brokerage fee.",
        "Lower the deposit to at most 70000.00.",
    }
    assert {c["check"] for c in body["checks"]} == {"claims", "commute", "compliance", "ground_truth"}  # no photos


def test_following_the_suggestions_passes(client, quickcash, fake_llm, fake_maps):
    fake_llm.claims = ExtractedClaims.model_validate(
        {"commute": [{"destination": "Indiranagar Metro", "minutes": 11, "mode": "walking"}]}
    )
    fake_maps.routes = {("Indiranagar Metro", CommuteMode.WALKING): 11}

    body = _precheck(client, quickcash, {**HONEST, "title": "2BHK, 11 min walk to Indiranagar Metro"}).json()

    assert (body["would_be"], body["suggestions"]) == ("VERIFIED", [])


def test_precheck_saves_nothing(client, quickcash, db_session):
    _precheck(client, quickcash, SHADY)

    assert db_session.scalar(select(func.count()).select_from(ListingVersion)) == 0
    assert db_session.scalar(select(func.count()).select_from(Audit)) == 0


def test_precheck_needs_your_own_listing(client, quickcash, db_session):
    _, listing_id = quickcash
    other = {"X-API-Key": "pl_broker_nope"}

    assert client.post("/broker/precheck", json={"listing_id": listing_id, "ad": HONEST}, headers=other).status_code == 401


def test_precheck_is_rate_limited(client, quickcash, monkeypatch):
    monkeypatch.setattr(broker_api, "precheck_limit", RateLimiter(limit=2, per_seconds=60))

    codes = [_precheck(client, quickcash, HONEST).status_code for _ in range(3)]

    assert codes == [200, 200, 429]


def test_rate_limiter_window_slides():
    now = [0.0]
    limiter = RateLimiter(limit=2, per_seconds=60, clock=lambda: now[0])

    assert [limiter.allow("b1"), limiter.allow("b1"), limiter.allow("b1"), limiter.allow("b2")] == [True, True, False, True]
    now[0] = 60.0
    assert limiter.allow("b1") is True
