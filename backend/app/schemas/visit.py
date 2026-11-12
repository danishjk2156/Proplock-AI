"""Request and response shapes for Feature 2: the booking assistant, slots and visits."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

from app.agents.visit_agent import ChatTurn
from app.db.models import VisitStatus
from app.domain.visit_rules import Collected, Qualification


class AssistantIn(BaseModel):
    message: str = Field(max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=40)
    collected: Collected = Collected()  # what earlier turns collected; the page sends it back


class AssistantOut(BaseModel):
    available: bool  # False: the assistant couldn't answer; the page offers the slots directly
    reply: Qualification
    unavailable_reason: str | None = None


class SlotOut(BaseModel):
    starts_at: datetime
    ends_at: datetime
    fee: Decimal
    currency: str


class SlotsOut(BaseModel):
    slots: list[SlotOut]
    window_days: int
    hold_minutes: int
    transit_buffer_minutes: int


class HoldIn(BaseModel):
    tenant_name: str = Field(min_length=2, max_length=150)
    tenant_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    collected: Collected | None = None


class ProofLog(BaseModel):
    party: str
    kind: str
    at: datetime
    distance_m: float | None
    counts: bool  # did the referee accept it as proof (close enough, on time)?


class ProofOut(BaseModel):
    """Feature 3: the check-in proof so far. Never contains the PIN."""

    pin_entered: bool
    pin_attempts_left: int
    tenant_at_flat: bool
    tenant_photo: bool
    broker_at_flat: bool
    tenant_choice: str | None
    broker_approved_lock: bool
    no_show_claimed: bool
    broker_absent_claimed: bool
    grace_ends_at: datetime
    settle_deadline: datetime | None
    waiting_for: str | None
    log: list[ProofLog]


class VisitOut(BaseModel):
    id: str
    listing_id: str
    starts_at: datetime
    ends_at: datetime
    status: VisitStatus
    expires_at: datetime
    fee: Decimal
    currency: str
    paypal_order_id: str | None = None
    authorization_ref: str | None
    capture_ref: str | None = None
    demo_authorization: bool
    outcome: str | None = None
    outcome_reason: str | None = None
    pin: str | None = None  # only ever sent to the tenant (it comes from their hold token)
    proof: ProofOut | None = None
    message: str | None = None  # e.g. "recorded, but you are 900 m from the flat"


class OrderOut(BaseModel):
    order_id: str
    demo: bool  # no PayPal popup: the page approves the demo order itself


class PaymentsConfig(BaseModel):
    paypal_client_id: str | None  # public: the JS SDK needs it to draw PayPal's button
    demo: bool
    currency: str


class GpsIn(BaseModel):
    latitude: Decimal = Field(ge=-90, le=90, max_digits=10, decimal_places=7)
    longitude: Decimal = Field(ge=-180, le=180, max_digits=10, decimal_places=7)
    accuracy_m: Decimal | None = Field(None, ge=0, le=100_000)


class AuthorizeIn(BaseModel):
    order_id: str | None = None


class DecisionIn(BaseModel):
    choice: Literal["PASS", "LOCK", "BROKER_ABSENT"]


class PinIn(BaseModel):
    pin: str = Field(pattern=r"^\s*\d{4}\s*$")


class HoldOut(VisitOut):
    hold_token: str  # shown once; send it as X-Hold-Token to authorise or release the hold


class CalendarVisit(BaseModel):
    id: str
    listing_id: str
    listing_title: str
    broker_id: str
    broker_name: str
    tenant_name: str
    tenant_email: str
    starts_at: datetime
    ends_at: datetime
    status: VisitStatus
    expires_at: datetime
    fee: Decimal
    currency: str
    authorization_ref: str | None
    capture_ref: str | None
    qualification: dict | None
    outcome: str | None = None
    outcome_reason: str | None = None
    proof: ProofOut | None = None


class CalendarOut(BaseModel):
    now: datetime
    window_days: int
    utc_offset_minutes: int
    visit_hours: str
    transit_buffer_minutes: int
    visits: list[CalendarVisit]
