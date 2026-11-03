from dataclasses import replace
from decimal import Decimal

import pytest

from app.db.models import Furnishing
from app.domain.check_result import Check, Verdict
from app.domain.ground_truth import ListingTerms, OwnerTerms, check_ground_truth

# The seeded Bengaluru flat: owner agreed 32k-35k, deposit up to 70k, unfurnished, 1 month brokerage.
OWNER = OwnerTerms(
    mandate_id="m-1",
    min_rent=Decimal("32000"),
    max_rent=Decimal("35000"),
    max_deposit=Decimal("70000"),
    max_brokerage_months=Decimal("1.00"),
    furnishing=Furnishing.UNFURNISHED,
)
HONEST = ListingTerms(
    rent=Decimal("35000"),
    deposit=Decimal("70000"),
    brokerage_fee=Decimal("35000"),
    furnishing=Furnishing.UNFURNISHED,
)


def _failed_fields(result):
    return [e.field for e in result.evidence if not e.ok]


def test_honest_listing_passes_with_evidence_for_every_field():
    result = check_ground_truth(HONEST, OWNER)

    assert result.check is Check.GROUND_TRUTH
    assert result.verdict is Verdict.PASS
    assert [e.field for e in result.evidence] == ["rent", "deposit", "furnishing", "brokerage_fee"]
    assert result.source == "mandate:m-1"


def test_bait_and_switch_fails_on_every_lie():
    # The review's example: 40k rent, 1.4L deposit, "Fully Furnished" for an unfurnished flat.
    shady = ListingTerms(
        rent=Decimal("40000"),
        deposit=Decimal("140000"),
        brokerage_fee=Decimal("0"),
        furnishing=Furnishing.FULLY,
    )

    result = check_ground_truth(shady, OWNER)

    assert result.verdict is Verdict.FAIL
    assert _failed_fields(result) == ["rent", "deposit", "furnishing"]


@pytest.mark.parametrize(
    ("change", "failed"),
    [
        ({"rent": Decimal("35000.01")}, "rent"),  # one paisa over the range
        ({"rent": Decimal("31999"), "brokerage_fee": Decimal("0")}, "rent"),  # below the owner's minimum
        ({"deposit": Decimal("70001")}, "deposit"),
        ({"furnishing": Furnishing.SEMI}, "furnishing"),
        ({"brokerage_fee": Decimal("35000.01")}, "brokerage_fee"),
    ],
)
def test_each_rule_fails_on_its_own(change, failed):
    result = check_ground_truth(replace(HONEST, **change), OWNER)

    assert result.verdict is Verdict.FAIL
    assert _failed_fields(result) == [failed]


def test_range_edges_and_less_furnishing_pass():
    listing = replace(HONEST, rent=Decimal("32000"), deposit=Decimal("0"), brokerage_fee=Decimal("0"))
    semi_owner = replace(OWNER, furnishing=Furnishing.SEMI)

    assert check_ground_truth(listing, OWNER).verdict is Verdict.PASS
    # Under-promising is fine: an "Unfurnished" ad for a semi-furnished flat doesn't mislead the tenant.
    assert check_ground_truth(listing, semi_owner).verdict is Verdict.PASS


def test_brokerage_cap_scales_with_rent_and_months():
    half_month = replace(OWNER, max_brokerage_months=Decimal("0.50"))
    listing = replace(HONEST, rent=Decimal("33000"), brokerage_fee=Decimal("16500"))

    result = check_ground_truth(listing, half_month)

    assert result.verdict is Verdict.PASS
    assert result.evidence[3].allowed == "<= 16500.00 (0.50 x rent)"
    assert check_ground_truth(replace(listing, brokerage_fee=Decimal("16501")), half_month).verdict is Verdict.FAIL


def test_inflated_rent_does_not_raise_the_brokerage_cap():
    # 40k rent is already a FAIL; it must not also make a 40k brokerage fee look acceptable.
    listing = replace(HONEST, rent=Decimal("40000"), brokerage_fee=Decimal("40000"))

    result = check_ground_truth(listing, OWNER)

    assert _failed_fields(result) == ["rent", "brokerage_fee"]
    assert result.evidence[3].allowed == "<= 35000.00 (1.00 x rent)"


def test_evidence_shows_money_with_two_decimals():
    result = check_ground_truth(replace(HONEST, rent=Decimal("40000")), OWNER)

    assert (result.evidence[0].claimed, result.evidence[0].allowed) == ("40000.00", "32000.00-35000.00")


def test_result_serialises_to_json_with_exact_money():
    shady = replace(HONEST, rent=Decimal("40000.50"))

    data = check_ground_truth(shady, OWNER).model_dump(mode="json")

    assert data["verdict"] == "FAIL"
    assert data["evidence"][0]["claimed"] == "40000.50"
