"""The shape every check (A-D) returns: a three-state verdict plus the evidence behind it."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Check(StrEnum):
    CLAIMS = "claims"  # the ad text was read and its claims extracted (runs before A-D)
    PHOTOS = "photos"  # A
    COMMUTE = "commute"  # B
    COMPLIANCE = "compliance"  # C
    GROUND_TRUTH = "ground_truth"  # D


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    # "We could not tell" (no key, no data, timeout). Never silently turned into PASS or FAIL.
    UNVERIFIABLE = "UNVERIFIABLE"


class Evidence(BaseModel):
    """One comparison a check made. Values are strings so money stays exact in JSON."""

    model_config = ConfigDict(frozen=True)

    field: str
    claimed: str
    allowed: str
    ok: bool | None  # None = this comparison couldn't be made (e.g. no route data)
    note: str


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    check: Check
    verdict: Verdict
    evidence: tuple[Evidence, ...] = ()
    source: str  # where the "truth" came from, e.g. "mandate:<id>"
    summary: str = ""  # one line for people, e.g. why a check was UNVERIFIABLE
