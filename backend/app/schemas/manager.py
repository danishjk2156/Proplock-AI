"""Request and response shapes for manager endpoints. Validation errors become 422 automatically."""

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.models import Furnishing, MandateStatus

Money = Field(gt=0, max_digits=12, decimal_places=2)


class PropertyIn(BaseModel):
    id: str = Field(pattern=r"^[A-Z0-9-]{3,50}$", examples=["PROP-BLR-003"])
    owner_name: str = Field(min_length=2, max_length=150)
    address: str = Field(min_length=5)
    city: str = Field(min_length=2, max_length=100)
    state: str = Field(min_length=2, max_length=100, examples=["Karnataka"])
    postal_code: str = Field(min_length=3, max_length=20)
    latitude: Decimal = Field(ge=-90, le=90)
    longitude: Decimal = Field(ge=-180, le=180)
    furnishing: Furnishing


class PropertyOut(PropertyIn):
    model_config = ConfigDict(from_attributes=True)

    created_at: datetime


class BrokerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    agency: str | None
    licence_number: str | None
    trust_score: int


class MandateIn(BaseModel):
    property_id: str
    broker_id: str
    min_rent: Decimal = Money
    max_rent: Decimal = Money
    max_deposit: Decimal = Money
    max_brokerage_months: Decimal = Field(default=Decimal("1.00"), ge=0, le=3, decimal_places=2)
    valid_until: AwareDatetime  # must include a timezone, e.g. 2026-12-31T23:59:00+05:30

    @field_validator("valid_until")
    @classmethod
    def must_be_in_future(cls, value: datetime) -> datetime:
        if value <= datetime.now(UTC):
            raise ValueError("valid_until must be in the future")
        return value

    @model_validator(mode="after")
    def rent_range_is_ordered(self) -> "MandateIn":
        if self.max_rent < self.min_rent:
            raise ValueError("max_rent must be greater than or equal to min_rent")
        return self


class MandateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    property_id: str
    broker_id: str
    min_rent: Decimal
    max_rent: Decimal
    max_deposit: Decimal
    max_brokerage_months: Decimal
    valid_until: datetime
    status: MandateStatus
    created_at: datetime
