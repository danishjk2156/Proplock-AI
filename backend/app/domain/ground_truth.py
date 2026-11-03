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


def _money(value: Decimal) -> str:
    """Always two decimals, so "40000" and "40000.00" read the same in the evidence."""
    return str(value.quantize(Decimal("0.01")))


def check_ground_truth(listing: ListingTerms, owner: OwnerTerms) -> CheckResult:
    # An inflated rent must not raise the brokerage cap too, so the cap uses at most max_rent.
    brokerage_cap = owner.max_brokerage_months * min(listing.rent, owner.max_rent)
    evidence = (
        Evidence(
            field="rent",
            claimed=_money(listing.rent),
            allowed=f"{_money(owner.min_rent)}-{_money(owner.max_rent)}",
            ok=owner.min_rent <= listing.rent <= owner.max_rent,
            note="Rent must be inside the owner's range; commission goes in brokerage_fee, not the rent.",
        ),
        Evidence(
            field="deposit",
            claimed=_money(listing.deposit),
            allowed=f"<= {_money(owner.max_deposit)}",
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
            claimed=_money(listing.brokerage_fee),
            allowed=f"<= {_money(brokerage_cap)} ({owner.max_brokerage_months} x rent)",
            ok=listing.brokerage_fee <= brokerage_cap,
            note="Brokerage is capped in months of rent, counting rent only up to the owner's maximum.",
        ),
    )
    verdict = Verdict.PASS if all(e.ok for e in evidence) else Verdict.FAIL
    return CheckResult(
        check=Check.GROUND_TRUTH, verdict=verdict, evidence=evidence, source=f"mandate:{owner.mandate_id}"
    )
