"""Broker endpoints: open a listing for a flat they hold a mandate for, add photos, submit it for audit."""

import logging
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import images
from app.adapters.llm import LLM, Vision, get_llm, get_vision
from app.adapters.maps import Maps, get_maps
from app.adapters.paypal import PayPal, get_paypal
from app.auth import require_role
from app.config import get_upload_dir
from app.db.base import get_session
from app.db.models import Listing, ListingPhoto, OutboxJob, PhotoKind, Role, User
from app.schemas.listing import ListingCreate, ListingOut, ListingSubmit, PhotoOut, SubmitOut
from app.services import audit_service, outbox, photo_service
from app.services.audit_service import ListingConflict, NoMandate
from app.services.photo_service import PhotoRejected, Upload

logger = logging.getLogger(__name__)
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
    llm: LLM = Depends(get_llm),
    maps: Maps = Depends(get_maps),
    vision: Vision = Depends(get_vision),
    paypal: PayPal = Depends(get_paypal),
) -> SubmitOut:
    listing = session.get(Listing, listing_id)
    if listing is None or listing.broker_id != broker.id:  # someone else's listing looks the same as none
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")
    try:
        audit, report = audit_service.submit_listing(session, listing, body, llm, maps, vision)
    except NoMandate as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    except ListingConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    # The audit and its mint job are already saved. Try the job now so the listing usually goes
    # LIVE straight away; if PayPal is down it stays queued and the worker retries it.
    job = session.scalar(select(OutboxJob).where(OutboxJob.audit_id == audit.id))
    if job is not None:
        try:
            outbox.run_job(session, job.id, paypal)
        except Exception:
            session.rollback()
            logger.exception("Immediate mint failed for audit %s; the outbox worker will retry", audit.id)
        session.refresh(listing)
    return SubmitOut(audit_id=audit.id, listing_status=listing.status, report=report)


def _own_listing(session: Session, broker: User, listing_id: str) -> Listing:
    listing = session.get(Listing, listing_id)
    if listing is None or listing.broker_id != broker.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")
    return listing


def _read(files: list[UploadFile]) -> list[Upload]:
    # Read at most one byte past the limit, so an oversized file is rejected without loading it all.
    return [Upload(f.filename or "upload", f.file.read(images.MAX_BYTES + 1)) for f in files]


@router.post("/listings/{listing_id}/photos", response_model=list[PhotoOut], status_code=status.HTTP_201_CREATED)
def upload_photos(
    listing_id: str,
    files: list[UploadFile] = File(..., description="1-20 JPEG, PNG or WEBP images, 8 MB each at most"),
    kind: PhotoKind = Form(PhotoKind.EXTRA, description="CHECKIN: taken at the flat now. EXTRA: other photos."),
    latitude: Decimal | None = Form(None, ge=-90, le=90, description="Device location when the photo was taken"),
    longitude: Decimal | None = Form(None, ge=-180, le=180),
    broker: User = Depends(broker_only),
    session: Session = Depends(get_session),
    upload_dir: Path = Depends(get_upload_dir),
) -> list[ListingPhoto]:
    listing = _own_listing(session, broker, listing_id)
    if listing.status not in audit_service.SUBMITTABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Photos can't be changed while the listing is {listing.status}")
    if (latitude is None) != (longitude is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Send both latitude and longitude, or neither")
    gps = (latitude, longitude) if latitude is not None else None
    try:
        return photo_service.add_listing_photos(session, listing, _read(files), kind, gps, upload_dir)
    except PhotoRejected as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e


@router.get("/listings/{listing_id}/photos", response_model=list[PhotoOut])
def list_photos(listing_id: str, broker: User = Depends(broker_only), session: Session = Depends(get_session)):
    listing = _own_listing(session, broker, listing_id)
    return session.scalars(
        select(ListingPhoto)
        .where(ListingPhoto.listing_id == listing.id, ListingPhoto.removed_at.is_(None))
        .order_by(ListingPhoto.sequence)
    ).all()


@router.delete("/listings/{listing_id}/photos/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_photo(
    listing_id: str, photo_id: str, broker: User = Depends(broker_only), session: Session = Depends(get_session)
) -> None:
    listing = _own_listing(session, broker, listing_id)
    photo = session.get(ListingPhoto, photo_id)
    if photo is None or photo.listing_id != listing.id or photo.removed_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo not found")
    if listing.status not in audit_service.SUBMITTABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Photos can't be changed while the listing is {listing.status}")
    photo_service.remove_listing_photo(session, photo)
