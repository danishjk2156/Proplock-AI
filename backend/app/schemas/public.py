"""What tenants see: the verified listing, built only from its own signed audit."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.db.models import CommuteMode, Furnishing


class PublicCommute(BaseModel):
    destination: str
    minutes: int
    mode: CommuteMode
    checked_at_rush_hour: bool


class PublicPhoto(BaseModel):
    url: str
    room_type: str


class BrokerCard(BaseModel):
    name: str
    agency: str | None
    licence_number: str | None
    trust_score: int


class PublicListingOut(BaseModel):
    listing_id: str
    property_id: str
    city: str
    title: str
    description: str
    rent: Decimal
    deposit: Decimal
    brokerage_fee: Decimal
    move_in_total: Decimal  # rent + deposit + brokerage: no surprise charges at signing
    furnishing: Furnishing
    commute: list[PublicCommute]
    advisories: list[str]
    photos: list[PublicPhoto]  # the owner's photos
    broker: BrokerCard
    audit_id: str
    verify_url: str
    paypal_product_id: str | None
    demo_product: bool  # true for DEMO_MODE "FAKE-" products
    verified_at: datetime
    last_confirmed_at: datetime | None
    expires_at: datetime | None
    version_no: int
