"""Public endpoints: anyone can check that a verified listing's audit hasn't been changed."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import get_session
from app.db.models import Audit, AuditStatus, ListingPhoto, ListingVersion, Mandate, Property
from app.domain.signing import is_valid
from app.schemas.audit import AuditReport

router = APIRouter(prefix="/public", tags=["public"])


class VerifyOut(BaseModel):
    audit_id: str
    status: AuditStatus
    paypal_product_id: str | None
    signature_valid: bool  # the stored report is exactly what was signed
    matches_current_data: bool  # the live database rows still say what the report says
    mismatches: list[str]


def _compare(label: str, signed, current, out: list[str]) -> None:
    # Compare values, not text: Decimal("35000") == Decimal("35000.00").
    if signed != current:
        out.append(f"{label}: signed {signed}, now {current}")


def current_mismatches(session: Session, audit: Audit, report: AuditReport) -> list[str]:
    """Compare the signed report with today's rows, so an edit straight in the database shows up."""
    out: list[str] = []
    version = session.get(ListingVersion, audit.version_id)
    if report.terms:
        for field in ("title", "description", "rent", "deposit", "brokerage_fee", "furnishing"):
            _compare(f"ad {field}", getattr(report.terms, field), getattr(version, field), out)
    stored = {p.id: p.phash for p in session.scalars(
        select(ListingPhoto).where(ListingPhoto.id.in_([p.id for p in report.photos]))
    )}
    for photo in report.photos:
        _compare(f"photo {photo.id} fingerprint", photo.phash, stored.get(photo.id, "missing"), out)
    if report.owner:
        mandate = session.get(Mandate, report.owner.mandate_id)
        prop = session.get(Property, report.property_id)
        for field in ("min_rent", "max_rent", "max_deposit", "max_brokerage_months"):
            _compare(f"owner {field}", getattr(report.owner, field), getattr(mandate, field), out)
        for field in ("furnishing", "latitude", "longitude"):
            _compare(f"flat {field}", getattr(report.owner, field), getattr(prop, field), out)
    return out


@router.get("/audits/{audit_id}/verify", response_model=VerifyOut)
def verify_audit(audit_id: str, session: Session = Depends(get_session)) -> VerifyOut:
    audit = session.get(Audit, audit_id)
    if audit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audit not found")
    valid = is_valid(audit.id, audit.report_json, audit.signature, get_settings().audit_secret)
    # Only compare with live rows if the report itself is genuine; a forged report proves nothing.
    mismatches = current_mismatches(session, audit, AuditReport.model_validate_json(audit.report_json)) if valid else []
    return VerifyOut(
        audit_id=audit.id,
        status=audit.status,
        paypal_product_id=audit.paypal_product_id,
        signature_valid=valid,
        matches_current_data=valid and not mismatches,
        mismatches=mismatches,
    )
