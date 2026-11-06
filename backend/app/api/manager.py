"""Manager endpoints: register flats (owner ground truth) and give brokers mandates to list them."""

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import images
from app.adapters.paypal import PayPal, get_paypal
from app.auth import require_role
from app.config import get_upload_dir
from app.db.base import get_session
from app.db.models import (
    Audit,
    AuditStatus,
    BaselinePhoto,
    Listing,
    ListingVersion,
    Mandate,
    MandateStatus,
    Property,
    Role,
    User,
)
from app.schemas.audit import AuditReport
from app.schemas.manager import (
    BaselinePhotoOut,
    BrokerOut,
    MandateIn,
    MandateOut,
    PropertyIn,
    PropertyOut,
    ReviewIn,
    ReviewItem,
    ReviewOut,
)
from app.services import audit_service, outbox, photo_service
from app.services.audit_service import ListingConflict, NoMandate
from app.services.photo_service import PhotoRejected, Upload

router = APIRouter(prefix="/manager", tags=["manager"])
manager_only = require_role(Role.MANAGER)


@router.post("/properties", response_model=PropertyOut, status_code=status.HTTP_201_CREATED)
def create_property(
    body: PropertyIn,
    manager: User = Depends(manager_only),
    session: Session = Depends(get_session),
) -> Property:
    if session.get(Property, body.id):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Property {body.id} already exists")
    prop = Property(**body.model_dump(), created_by=manager.id)
    session.add(prop)
    session.commit()
    return prop


@router.get("/properties", response_model=list[PropertyOut])
def list_properties(_: User = Depends(manager_only), session: Session = Depends(get_session)):
    return session.scalars(select(Property).order_by(Property.id)).all()


@router.get("/brokers", response_model=list[BrokerOut])
def list_brokers(_: User = Depends(manager_only), session: Session = Depends(get_session)):
    return session.scalars(select(User).where(User.role == Role.BROKER).order_by(User.name)).all()


@router.post("/mandates", response_model=MandateOut, status_code=status.HTTP_201_CREATED)
def create_mandate(
    body: MandateIn,
    manager: User = Depends(manager_only),
    session: Session = Depends(get_session),
) -> Mandate:
    if session.get(Property, body.property_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Property {body.property_id} not found")
    broker = session.get(User, body.broker_id)
    if broker is None or broker.role is not Role.BROKER:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "broker_id must be an existing broker")
    duplicate = session.scalar(
        select(Mandate).where(
            Mandate.property_id == body.property_id,
            Mandate.broker_id == body.broker_id,
            Mandate.status == MandateStatus.ACTIVE,
        )
    )
    if duplicate:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Broker already has active mandate {duplicate.id}")

    mandate = Mandate(**body.model_dump(), created_by=manager.id)
    session.add(mandate)
    session.commit()
    return mandate


@router.get("/mandates", response_model=list[MandateOut])
def list_mandates(
    property_id: str | None = None,
    _: User = Depends(manager_only),
    session: Session = Depends(get_session),
):
    query = select(Mandate).order_by(Mandate.created_at)
    if property_id:
        query = query.where(Mandate.property_id == property_id)
    return session.scalars(query).all()


@router.post("/mandates/{mandate_id}/revoke", response_model=MandateOut)
def revoke_mandate(
    mandate_id: str,
    _: User = Depends(manager_only),
    session: Session = Depends(get_session),
) -> Mandate:
    mandate = session.get(Mandate, mandate_id)
    if mandate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mandate not found")
    mandate.status = MandateStatus.REVOKED
    session.commit()
    return mandate


@router.post(
    "/properties/{property_id}/photos", response_model=list[BaselinePhotoOut], status_code=status.HTTP_201_CREATED
)
def upload_baseline_photos(
    property_id: str,
    files: list[UploadFile] = File(..., description="The owner's own photos of the flat (JPEG, PNG or WEBP)"),
    room_type: str = Form("general", max_length=50),
    _: User = Depends(manager_only),
    session: Session = Depends(get_session),
    upload_dir: Path = Depends(get_upload_dir),
):
    """Baseline photos: what the flat really looks like. Extra broker photos are compared with these."""
    prop = session.get(Property, property_id)
    if prop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Property {property_id} not found")
    uploads = [Upload(f.filename or "upload", f.file.read(images.MAX_BYTES + 1)) for f in files]
    try:
        return photo_service.add_baseline_photos(session, prop, uploads, room_type, upload_dir)
    except PhotoRejected as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e


@router.post("/outbox/run")
def run_outbox(
    _: User = Depends(manager_only),
    session: Session = Depends(get_session),
    paypal: PayPal = Depends(get_paypal),
) -> dict[str, int]:
    """Retry every due PayPal job now (the background worker does the same every 30 s)."""
    return {"minted": outbox.run_due(session, paypal)}


@router.get("/reviews", response_model=list[ReviewItem])
def list_reviews(_: User = Depends(manager_only), session: Session = Depends(get_session)) -> list[ReviewItem]:
    """Audits waiting for a person: each lists exactly what the checks couldn't decide."""
    rows = session.execute(
        select(Audit, ListingVersion, Listing, User)
        .join(ListingVersion, Audit.version_id == ListingVersion.id)
        .join(Listing, ListingVersion.listing_id == Listing.id)
        .join(User, Listing.broker_id == User.id)
        .where(Audit.status == AuditStatus.IN_REVIEW)
        .order_by(Audit.created_at)
    ).all()
    return [
        ReviewItem(
            audit_id=audit.id,
            listing_id=listing.id,
            property_id=listing.property_id,
            broker_name=broker.name,
            version_no=version.version_no,
            created_at=audit.created_at,
            open_questions=audit_service.open_questions(AuditReport.model_validate_json(audit.report_json)),
        )
        for audit, version, listing, broker in rows
    ]


@router.post("/reviews/{audit_id}", response_model=ReviewOut)
def decide_review(
    audit_id: str,
    body: ReviewIn,
    manager: User = Depends(manager_only),
    session: Session = Depends(get_session),
    paypal: PayPal = Depends(get_paypal),
) -> ReviewOut:
    """Approve (it is minted and goes LIVE, like an automatic VERIFIED) or reject (back to the broker)."""
    audit = session.get(Audit, audit_id)
    if audit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audit not found")
    try:
        listing = audit_service.review_audit(session, audit, manager, body.approve, body.note)
    except ListingConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except NoMandate as e:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Can't approve: {e}") from e
    if body.approve:
        outbox.try_now(session, audit.id, paypal)
        session.refresh(audit)
        session.refresh(listing)
    return ReviewOut(audit_id=audit.id, audit_status=audit.status, listing_status=listing.status)


@router.get("/properties/{property_id}/photos", response_model=list[BaselinePhotoOut])
def list_baseline_photos(property_id: str, _: User = Depends(manager_only), session: Session = Depends(get_session)):
    if session.get(Property, property_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Property {property_id} not found")
    return session.scalars(
        select(BaselinePhoto).where(BaselinePhoto.property_id == property_id).order_by(BaselinePhoto.captured_at)
    ).all()
