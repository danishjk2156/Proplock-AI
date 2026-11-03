"""Broker endpoints: open a listing for a flat they hold a mandate for, then submit it for audit."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import require_role
from app.db.base import get_session
from app.db.models import Listing, Role, User
from app.schemas.listing import ListingCreate, ListingOut, ListingSubmit, SubmitOut
from app.services import audit_service
from app.services.audit_service import ListingConflict, NoMandate

router = APIRouter(prefix="/broker", tags=["broker"])
broker_only = require_role(Role.BROKER)


@router.post("/listings", response_model=ListingOut, status_code=status.HTTP_201_CREATED)
def open_listing(
    body: ListingCreate,
    broker: User = Depends(broker_only),
    session: Session = Depends(get_session),
) -> Listing:
    try:
        return audit_service.open_listing(session, broker, body.property_id)
    except NoMandate as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    except ListingConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e


@router.post("/listings/{listing_id}/submit", response_model=SubmitOut)
def submit_listing(
    listing_id: str,
    body: ListingSubmit,
    broker: User = Depends(broker_only),
    session: Session = Depends(get_session),
) -> SubmitOut:
    listing = session.get(Listing, listing_id)
    if listing is None or listing.broker_id != broker.id:  # someone else's listing looks the same as none
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")
    try:
        audit, report = audit_service.submit_listing(session, listing, body)
    except NoMandate as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    except ListingConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return SubmitOut(audit_id=audit.id, listing_status=listing.status, report=report)
