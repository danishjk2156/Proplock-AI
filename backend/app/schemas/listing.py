"""Request and response shapes for broker listing endpoints."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Furnishing, ListingStatus, PhotoKind
from app.domain.claims import CommuteClaimIn
from app.schemas.audit import AuditReport


class ListingCreate(BaseModel):
    property_id: str


class ListingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    property_id: str
    mandate_id: str
    status: ListingStatus
    created_at: datetime


class ListingSubmit(BaseModel):
    """The ad's content. Every submit saves it as a new numbered version."""

    title: str = Field(min_length=5, max_length=255)
    description: str = Field(min_length=10, max_length=5000)  # bounds what we send to the LLM
    rent: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    deposit: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    brokerage_fee: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    furnishing: Furnishing
    commute_claims: list[CommuteClaimIn] = Field(default=[], max_length=10)


class SubmitOut(BaseModel):
    audit_id: str
    listing_status: ListingStatus
    report: AuditReport


class PhotoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: PhotoKind
    phash: str
    latitude: Decimal | None
    longitude: Decimal | None
    gps_source: str | None
    captured_at: datetime | None
