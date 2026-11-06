"""The audit report: saved as JSON when the audit is made, and returned exactly as saved."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, field_validator

from app.db.models import AuditStatus, Furnishing, PhotoKind
from app.domain.check_result import CheckResult
from app.domain.claims import ClaimedCommute
from app.domain.decision import Decision


class PhotoRecord(BaseModel):
    """Which photo was judged, and its fingerprint, so a later swap is detectable."""

    id: str
    kind: PhotoKind
    phash: str


class SubmittedTerms(BaseModel):
    """The ad exactly as submitted for this version."""

    title: str
    description: str
    rent: Decimal
    deposit: Decimal
    brokerage_fee: Decimal
    furnishing: Furnishing

    @field_validator("rent", "deposit", "brokerage_fee")
    @classmethod
    def _paise(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"))  # "35000" and "35000.00" are stored the same way


class OwnerSnapshot(BaseModel):
    """The ground truth the ad was judged against, frozen at audit time."""

    mandate_id: str
    min_rent: Decimal
    max_rent: Decimal
    max_deposit: Decimal
    max_brokerage_months: Decimal
    furnishing: Furnishing
    latitude: Decimal
    longitude: Decimal
    city: str
    state: str


class AuditReport(BaseModel):
    listing_id: str
    property_id: str
    version_no: int
    decision: Decision
    ruleset_version: str
    checks: list[CheckResult]
    # The claims that were judged: form + extracted from the text (defaults keep ruleset-1 reports readable).
    commute_claims: list[ClaimedCommute] = []
    text_furnishing: Furnishing | None = None
    photos: list[PhotoRecord] = []
    terms: SubmittedTerms | None = None
    owner: OwnerSnapshot | None = None


class AuditOut(BaseModel):
    id: str
    status: AuditStatus  # can change later (e.g. a manager's review); the report never does
    created_at: datetime
    signature: str | None
    paypal_product_id: str | None
    report: AuditReport
