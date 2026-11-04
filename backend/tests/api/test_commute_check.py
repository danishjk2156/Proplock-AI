"""Phase 4: Check B judges every commute claim, from the form or the ad text (fixes M3, D5)."""

import pytest

from app.adapters.llm import ExtractedClaims
from app.adapters.maps import DemoTableMaps, get_maps
from app.db.models import CommuteMode
from app.db.seed import seed

AD = {
    "title": "2BHK in Indiranagar",
    "description": "Unfurnished 2BHK on 12th Main, east facing, covered parking.",
    "rent": "35000",
    "deposit": "70000",
    "furnishing": "Unfurnished",
}
METRO_5_MIN = {"destination": "Indiranagar Metro", "minutes": 5, "mode": "walking"}


@pytest.fixture
def rajesh(client, db_session):
    headers = {"X-API-Key": seed(db_session)["broker_rajesh"]}
    listing = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=headers).json()
    return headers, listing["id"]


def _submit(client, rajesh, body):
    headers, listing_id = rajesh
    return client.post(f"/broker/listings/{listing_id}/submit", json=body, headers=headers).json()


def _commute(report):
    [check] = [c for c in report["checks"] if c["check"] == "commute"]
    return check


@pytest.mark.parametrize(("actual", "decision", "verdict"), [(22, "NEEDS_FIX", "FAIL"), (7, "VERIFIED", "PASS")])
def test_five_minute_claim_is_judged_against_the_real_time(client, rajesh, fake_maps, actual, decision, verdict):
    fake_maps.routes = {("indiranagar metro", CommuteMode.WALKING): actual}

    report = _submit(client, rajesh, {**AD, "commute_claims": [METRO_5_MIN]})["report"]

    assert report["decision"] == decision
    [e] = _commute(report)["evidence"]
    assert (_commute(report)["verdict"], e["claimed"], e["allowed"]) == (verdict, "5 min", "<= 10 min")
    assert f"Actual {actual} min" in e["note"]
    # Every route starts at the manager-verified flat, whatever the broker typed.
    assert fake_maps.calls == [("PROP-BLR-001", "Indiranagar Metro", CommuteMode.WALKING)]


def test_unknown_destination_goes_to_review_with_no_number(client, rajesh):
    body = {**AD, "commute_claims": [{**METRO_5_MIN, "destination": "Manyata Tech Park"}]}

    report = _submit(client, rajesh, body)["report"]

    assert report["decision"] == "IN_REVIEW"
    [e] = _commute(report)["evidence"]
    assert e["ok"] is None
    assert "Actual" not in e["note"]


def test_demo_table_catches_a_lie_hidden_in_the_title(client, rajesh, fake_llm):
    # The story for the demo: "2 MIN WALK" in the title, no form claim, DEMO_MODE route table says 11.
    client.app.dependency_overrides[get_maps] = DemoTableMaps
    fake_llm.claims = ExtractedClaims.model_validate(
        {"commute": [{"destination": "Indiranagar Metro", "minutes": 2, "mode": "walking"}]}
    )

    report = _submit(client, rajesh, {**AD, "title": "2 MIN WALK TO INDIRANAGAR METRO!!"})["report"]

    assert report["decision"] == "NEEDS_FIX"
    check = _commute(report)
    assert check["source"] == "demo-table"
    assert "DEMO route table, not live traffic data." in check["summary"]
    [e] = check["evidence"]
    assert (e["claimed"], e["allowed"], e["ok"]) == ("2 min", "<= 7 min", False)
    assert "Claim from the ad text." in e["note"]
