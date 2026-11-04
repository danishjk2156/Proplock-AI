"""Phase 3: claims hidden in the ad text are found, stored and judged (fixes M2)."""

import pytest
from sqlalchemy import select

from app.adapters.llm import ExtractedClaims, ExtractionUnavailable
from app.db.models import ClaimSource, CommuteClaim
from app.db.seed import seed

AD = {
    "title": "2 MIN WALK TO INDIRANAGAR METRO!!",
    "description": "Unfurnished 2BHK on 12th Main, east facing, covered parking.",
    "rent": "35000",
    "deposit": "70000",
    "furnishing": "Unfurnished",
}


@pytest.fixture
def rajesh(client, db_session):
    headers = {"X-API-Key": seed(db_session)["broker_rajesh"]}
    listing = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=headers).json()
    return headers, listing["id"]


def _submit(client, rajesh, body):
    headers, listing_id = rajesh
    return client.post(f"/broker/listings/{listing_id}/submit", json=body, headers=headers).json()


def _check(report, name):
    [check] = [c for c in report["checks"] if c["check"] == name]
    return check


def test_claim_only_in_the_title_is_extracted_and_stored(client, rajesh, fake_llm, db_session):
    fake_llm.claims = ExtractedClaims.model_validate(
        {"commute": [{"destination": "Indiranagar Metro", "minutes": 2, "mode": "walking"}]}
    )

    report = _submit(client, rajesh, AD)["report"]

    assert fake_llm.calls == [(AD["title"], AD["description"])]
    assert report["commute_claims"] == [
        {"destination": "Indiranagar Metro", "minutes": 2, "mode": "walking", "source": "EXTRACTED"}
    ]
    # The test maps know no routes, so Check B can't confirm the claim: review, never a silent pass.
    assert _check(report, "commute")["verdict"] == "UNVERIFIABLE"
    assert report["decision"] == "IN_REVIEW"
    stored = db_session.scalars(select(CommuteClaim)).all()
    assert [(c.destination, c.source) for c in stored] == [("Indiranagar Metro", ClaimSource.EXTRACTED)]


def test_form_and_extracted_claims_are_both_kept(client, rajesh, fake_llm, db_session):
    fake_llm.claims = ExtractedClaims.model_validate(
        {"commute": [{"destination": "Indiranagar Metro", "minutes": 2, "mode": "walking"}]}
    )
    body = {**AD, "commute_claims": [{"destination": "Bagmane Tech Park", "minutes": 15, "mode": "driving"}]}

    report = _submit(client, rajesh, body)["report"]

    assert [c["source"] for c in report["commute_claims"]] == ["FORM", "EXTRACTED"]
    assert len(db_session.scalars(select(CommuteClaim)).all()) == 2


def test_fully_furnished_in_the_text_fails_check_d(client, rajesh, fake_llm):
    fake_llm.claims = ExtractedClaims(furnishing="Fully Furnished")

    report = _submit(client, rajesh, {**AD, "title": "Fully furnished 2BHK, move in today"})["report"]

    assert report["decision"] == "NEEDS_FIX"
    assert report["text_furnishing"] == "Fully Furnished"
    failed = [e["field"] for e in _check(report, "ground_truth")["evidence"] if not e["ok"]]
    assert failed == ["furnishing_in_text"]


def test_when_the_text_cannot_be_read_the_listing_goes_to_review(client, rajesh, fake_llm):
    fake_llm.error = ExtractionUnavailable("GROQ_API_KEY is not set")

    body = _submit(client, rajesh, {**AD, "title": "2BHK Indiranagar"})

    assert body["listing_status"] == "IN_REVIEW"
    claims = _check(body["report"], "claims")
    assert (claims["verdict"], claims["summary"]) == ("UNVERIFIABLE", "GROQ_API_KEY is not set")


def test_too_many_form_claims_is_422(client, rajesh):
    headers, listing_id = rajesh
    claim = {"destination": "Somewhere", "minutes": 5, "mode": "walking"}

    response = client.post(
        f"/broker/listings/{listing_id}/submit", json={**AD, "commute_claims": [claim] * 11}, headers=headers
    )

    assert response.status_code == 422
