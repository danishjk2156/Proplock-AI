"""Graph state and nodes. A node reads the state and returns only the keys it changes."""

import operator
from typing import Annotated, TypedDict

from app.domain.check_result import CheckResult
from app.domain.decision import Decision
from app.domain.ground_truth import ListingTerms, OwnerTerms, check_ground_truth


class AuditState(TypedDict):
    listing: ListingTerms
    owner: OwnerTerms
    # Each check node returns a one-item list; operator.add joins them when parallel branches meet.
    results: Annotated[list[CheckResult], operator.add]
    decision: Decision


def extract_claims(state: AuditState) -> dict:
    """Stub until Phase 3, when an LLM will pull extra claims out of the title and description."""
    return {}


def ground_truth(state: AuditState) -> dict:
    return {"results": [check_ground_truth(state["listing"], state["owner"])]}
