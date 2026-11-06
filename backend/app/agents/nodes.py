"""Graph state and nodes. A node reads the state and returns only the keys it changes."""

import operator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated, TypedDict

from langchain_core.runnables import RunnableConfig

from app.adapters.llm import LLM, ExtractionUnavailable, Vision, VisionUnavailable
from app.adapters.maps import Maps, Place
from app.db.models import Furnishing, PhotoKind
from app.domain.check_result import Check, CheckResult, Verdict
from app.domain.claims import ClaimedCommute, CommuteClaimIn, merge_commute_claims
from app.domain.commute_rules import check_commute
from app.domain.compliance_rules import check_compliance, default_rules
from app.domain.decision import Decision
from app.domain.ground_truth import FURNISHING_RANK, ListingTerms, OwnerTerms, check_ground_truth
from app.domain.photo_rules import PhotoFacts, PhotoInput, check_photos


class AuditState(TypedDict):
    title: str
    description: str
    listing: ListingTerms
    owner: OwnerTerms
    place: Place
    photos: list[PhotoInput]
    baselines: list[str]  # file paths of the owner's photos
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


def photos(state: AuditState, config: RunnableConfig) -> dict:
    """Check A: one vision call per extra photo (in parallel), then judge every photo."""
    vision: Vision = config["configurable"]["vision"]
    extras = [p for p in state["photos"] if p.kind is PhotoKind.EXTRA]

    def inspect(photo: PhotoInput):
        try:
            return vision.inspect_photo(photo.path, state["baselines"]), ""
        except VisionUnavailable as e:
            return None, str(e)

    with ThreadPoolExecutor(max_workers=4) as pool:
        inspected = dict(zip([p.photo_id for p in extras], pool.map(inspect, extras), strict=True))
    facts = [
        PhotoFacts(p.photo_id, p.kind, p.distance_m, p.captured_at, p.duplicate_of, *inspected.get(p.photo_id, (None, "")))
        for p in state["photos"]
    ]
    listing = state["listing"]
    claimed = max(filter(None, [listing.furnishing, state["text_furnishing"]]), key=FURNISHING_RANK.__getitem__)
    return {"results": [check_photos(facts, claimed, datetime.now(UTC), source=f"phash+gps+{vision.name}")]}


def compliance(state: AuditState) -> dict:
    """Check C: is the advertised deposit allowed in the flat's state?"""
    listing = state["listing"]
    return {"results": [check_compliance(state["place"].state, listing.rent, listing.deposit, default_rules())]}


def ground_truth(state: AuditState) -> dict:
    listing = replace(state["listing"], text_furnishing=state["text_furnishing"])
    return {"results": [check_ground_truth(listing, state["owner"])]}
