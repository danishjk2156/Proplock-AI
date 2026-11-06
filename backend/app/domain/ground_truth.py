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
    text_furnishing: Furnishing | None = None  # what the title/description claims, if anything


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
            suggestion=(
                f"Set the rent between {_money(owner.min_rent)} and {_money(owner.max_rent)}; "
                "charge your commission as brokerage_fee."
            ),
        ),
        Evidence(
            field="deposit",
            claimed=_money(listing.deposit),
            allowed=f"<= {_money(owner.max_deposit)}",
            ok=listing.deposit <= owner.max_deposit,
            note="Deposit cannot exceed what the owner agreed.",
            suggestion=f"Lower the deposit to at most {_money(owner.max_deposit)}.",
        ),
        Evidence(
            field="furnishing",
            claimed=listing.furnishing.value,
            allowed=f"<= {owner.furnishing.value}",
            ok=FURNISHING_RANK[listing.furnishing] <= FURNISHING_RANK[owner.furnishing],
            note="The ad cannot claim more furnishing than the owner registered.",
            suggestion=f"Set furnishing to {owner.furnishing.value}, as the owner registered it.",
        ),
        Evidence(
            field="brokerage_fee",
            claimed=_money(listing.brokerage_fee),
            allowed=f"<= {_money(brokerage_cap)} ({owner.max_brokerage_months} x rent)",
            ok=listing.brokerage_fee <= brokerage_cap,
            note="Brokerage is capped in months of rent, counting rent only up to the owner's maximum.",
            suggestion=f"Lower the brokerage fee to at most {_money(brokerage_cap)}.",
        ),
    )
    if listing.text_furnishing is not None:
        # "Fully furnished!" in the description must not slip past because the form says Unfurnished.
        evidence += (
            Evidence(
                field="furnishing_in_text",
                claimed=listing.text_furnishing.value,
                allowed=f"<= {owner.furnishing.value}",
                ok=FURNISHING_RANK[listing.text_furnishing] <= FURNISHING_RANK[owner.furnishing],
                note="Furnishing claimed in the title or description, read by the claim extractor.",
                suggestion=(
                    "Change the furnishing wording in the title or description: "
                    f"the owner registered {owner.furnishing.value}."
                ),
            ),
        )
    verdict = Verdict.PASS if all(e.ok for e in evidence) else Verdict.FAIL
    return CheckResult(
        check=Check.GROUND_TRUTH, verdict=verdict, evidence=evidence, source=f"mandate:{owner.mandate_id}"
    )
