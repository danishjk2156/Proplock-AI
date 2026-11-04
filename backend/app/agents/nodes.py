"""Graph state and nodes. A node reads the state and returns only the keys it changes."""

import operator
from dataclasses import replace
from typing import Annotated, TypedDict

from langchain_core.runnables import RunnableConfig

from app.adapters.llm import LLM, ExtractionUnavailable
from app.adapters.maps import Maps, Place
from app.db.models import Furnishing
from app.domain.check_result import Check, CheckResult, Verdict
from app.domain.claims import ClaimedCommute, CommuteClaimIn, merge_commute_claims
from app.domain.commute_rules import check_commute
from app.domain.decision import Decision
from app.domain.ground_truth import ListingTerms, OwnerTerms, check_ground_truth


class AuditState(TypedDict):
    title: str
    description: str
    listing: ListingTerms
    owner: OwnerTerms
    place: Place
    form_commute: list[CommuteClaimIn]
    # Set by extract_claims:
    commute: list[ClaimedCommute]
    text_furnishing: Furnishing | None
    # Each node returns a one-item list; operator.add joins them when parallel branches meet.
    results: Annotated[list[CheckResult], operator.add]
    decision: Decision


def extract_claims(state: AuditState, config: RunnableConfig) -> dict:
    """The LLM reads the title and description; the form's own claims are always kept."""
    llm: LLM = config["configurable"]["llm"]
    try:
        extracted = llm.extract_claims(state["title"], state["description"])
    except ExtractionUnavailable as e:
        # Without reading the text we can't know what it claims, so this is UNVERIFIABLE, not PASS.
        return {
            "commute": merge_commute_claims(state["form_commute"], []),
            "text_furnishing": None,
            "results": [
                CheckResult(check=Check.CLAIMS, verdict=Verdict.UNVERIFIABLE, source=llm.name, summary=str(e))
            ],
        }
    commute = merge_commute_claims(state["form_commute"], extracted.commute)
    found = len(extracted.commute) + (extracted.furnishing is not None)
    return {
        "commute": commute,
        "text_furnishing": extracted.furnishing,
        "results": [
            CheckResult(
                check=Check.CLAIMS,
                verdict=Verdict.PASS,
                source=llm.name,
                summary=f"Read the ad text: {found} claim(s) found.",
            )
        ],
    }


def commute(state: AuditState, config: RunnableConfig) -> dict:
    """Check B: look up each claimed route from the flat's verified pin, then judge it."""
    maps: Maps = config["configurable"]["maps"]
    claims = state["commute"]
    lookups = [maps.travel_minutes(state["place"], c.destination, c.mode) for c in claims]
    result = check_commute(claims, lookups, source=maps.name)
    if maps.name == "demo-table" and claims:
        result = result.model_copy(update={"summary": f"{result.summary} DEMO route table, not live traffic data."})
    return {"results": [result]}


def ground_truth(state: AuditState) -> dict:
    listing = replace(state["listing"], text_furnishing=state["text_furnishing"])
    return {"results": [check_ground_truth(listing, state["owner"])]}
