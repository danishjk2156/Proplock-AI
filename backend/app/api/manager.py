"""Manager endpoints: register flats (owner ground truth) and give brokers mandates to list them."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_role
from app.db.base import get_session
from app.db.models import Mandate, MandateStatus, Property, Role, User
from app.schemas.manager import BrokerOut, MandateIn, MandateOut, PropertyIn, PropertyOut

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
