"""Check D: does the listing match what the owner agreed in the mandate? (fixes M1)

No AI and no network: it compares numbers we already hold, so it is the cheapest check
and catches the most common scams (rent markup, inflated deposit, furnishing lies).
"""

from dataclasses import dataclass
from decimal import Decimal

from app.db.models import Furnishing
from app.domain.check_result import Check, CheckResult, Evidence, Verdict

FURNISHING_RANK = {Furnishing.UNFURNISHED: 0, Furnishing.SEMI: 1, Furnishing.FULLY: 2}


@dataclass(frozen=True)
class ListingTerms:
    """What the broker advertises."""

    rent: Decimal
    deposit: Decimal
    brokerage_fee: Decimal  # one-time fee in rupees
    furnishing: Furnishing


@dataclass(frozen=True)
class OwnerTerms:
    """What the owner agreed: the mandate limits plus the flat's registered furnishing."""

    mandate_id: str
    min_rent: Decimal
    max_rent: Decimal
    max_deposit: Decimal
    max_brokerage_months: Decimal
    furnishing: Furnishing


def check_ground_truth(listing: ListingTerms, owner: OwnerTerms) -> CheckResult:
    brokerage_cap = (owner.max_brokerage_months * listing.rent).quantize(Decimal("0.01"))
    evidence = (
        Evidence(
            field="rent",
            claimed=str(listing.rent),
            allowed=f"{owner.min_rent}-{owner.max_rent}",
            ok=owner.min_rent <= listing.rent <= owner.max_rent,
            note="Rent must be inside the owner's range; commission goes in brokerage_fee, not the rent.",
        ),
        Evidence(
            field="deposit",
            claimed=str(listing.deposit),
            allowed=f"<= {owner.max_deposit}",
            ok=listing.deposit <= owner.max_deposit,
            note="Deposit cannot exceed what the owner agreed.",
        ),
        Evidence(
            field="furnishing",
            claimed=listing.furnishing.value,
            allowed=f"<= {owner.furnishing.value}",
            ok=FURNISHING_RANK[listing.furnishing] <= FURNISHING_RANK[owner.furnishing],
            note="The ad cannot claim more furnishing than the owner registered.",
        ),
        Evidence(
            field="brokerage_fee",
            claimed=str(listing.brokerage_fee),
            allowed=f"<= {brokerage_cap} ({owner.max_brokerage_months} x rent)",
            ok=listing.brokerage_fee <= brokerage_cap,
            note="Brokerage is capped in months of the advertised rent.",
        ),
    )
    verdict = Verdict.PASS if all(e.ok for e in evidence) else Verdict.FAIL
    return CheckResult(
        check=Check.GROUND_TRUTH, verdict=verdict, evidence=evidence, source=f"mandate:{owner.mandate_id}"
    )
