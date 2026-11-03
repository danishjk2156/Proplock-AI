"""Commute claims from the form and from the ad text, merged into one list for Check B."""

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import ClaimSource, CommuteMode


class CommuteClaimIn(BaseModel):
    """A commute claim as the broker types it into the form (also the shape the LLM extracts)."""

    model_config = ConfigDict(frozen=True)

    destination: str = Field(min_length=2, max_length=255)
    minutes: int = Field(gt=0, le=600)
    mode: CommuteMode


class ClaimedCommute(CommuteClaimIn):
    source: ClaimSource


def _key(claim: CommuteClaimIn) -> tuple[str, CommuteMode]:
    return " ".join(claim.destination.lower().split()), claim.mode


def merge_commute_claims(
    form: list[CommuteClaimIn], extracted: list[CommuteClaimIn]
) -> list[ClaimedCommute]:
    """Form claims first, then extracted ones. Only exact repeats (same destination, mode and
    minutes) are dropped: if the form says 10 min and the text says 2 min, both get checked."""
    merged = [ClaimedCommute(**c.model_dump(), source=ClaimSource.FORM) for c in form]
    seen = {(*_key(c), c.minutes) for c in merged}
    for claim in extracted:
        if (*_key(claim), claim.minutes) not in seen:
            merged.append(ClaimedCommute(**claim.model_dump(), source=ClaimSource.EXTRACTED))
            seen.add((*_key(claim), claim.minutes))
    return merged
