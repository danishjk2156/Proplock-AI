"""Read an audit back exactly as it was stored."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db.base import get_session
from app.db.models import Audit, Listing, ListingVersion, Role, User
from app.schemas.audit import AuditOut, AuditReport

router = APIRouter(prefix="/audits", tags=["audits"])


@router.get("/{audit_id}", response_model=AuditOut)
def get_audit(
    audit_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> AuditOut:
    audit = session.get(Audit, audit_id)
    listing = session.get(Listing, session.get(ListingVersion, audit.version_id).listing_id) if audit else None
    # Managers see every audit; a broker sees only their own (others look like "not found").
    if listing is None or (user.role is Role.BROKER and listing.broker_id != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audit not found")
    return AuditOut(
        id=audit.id,
        status=audit.status,
        created_at=audit.created_at,
        signature=audit.signature,
        paypal_product_id=audit.paypal_product_id,
        report=AuditReport.model_validate_json(audit.report_json),  # parsed from storage, never rebuilt
    )
