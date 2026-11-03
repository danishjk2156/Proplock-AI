"""Turns the checks' verdicts into one outcome for the listing."""

from collections.abc import Collection, Iterable
from enum import StrEnum

from app.domain.check_result import Check, CheckResult, Verdict


class Decision(StrEnum):
    NEEDS_FIX = "NEEDS_FIX"  # at least one claim is provably wrong: the broker must fix it
    IN_REVIEW = "IN_REVIEW"  # nothing wrong, but something couldn't be checked: a manager decides
    VERIFIED = "VERIFIED"  # every required check passed


def decide(results: Iterable[CheckResult], required: Collection[Check]) -> Decision:
    """FAIL beats UNVERIFIABLE beats PASS.

    A required check with no result (a node crashed, or was never wired in) counts as
    UNVERIFIABLE, so a missing check can never let a listing through.
    """
    if not required:
        raise ValueError("at least one check must be required")
    relevant = [r for r in results if r.check in required]
    verdicts = {r.verdict for r in relevant}

    if Verdict.FAIL in verdicts:
        return Decision.NEEDS_FIX
    if {r.check for r in relevant} != set(required) or Verdict.UNVERIFIABLE in verdicts:
        return Decision.IN_REVIEW
    return Decision.VERIFIED
