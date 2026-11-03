"""The audit report: saved as JSON when the audit is made, and returned exactly as saved."""

from datetime import datetime

from pydantic import BaseModel

from app.db.models import AuditStatus, Furnishing
from app.domain.check_result import CheckResult
from app.domain.claims import ClaimedCommute
from app.domain.decision import Decision


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


class AuditOut(BaseModel):
    id: str
    status: AuditStatus  # can change later (e.g. a manager's review); the report never does
    created_at: datetime
    report: AuditReport
