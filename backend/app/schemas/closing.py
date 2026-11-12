"""Request and response shapes for Feature 4: closings, invoices, the ledger and disputes.

Money is Decimal and serialises as a string ("1035.00"), so no amount passes through a float.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import ClosingStatus, DisputeStatus, InvoiceStatus, LedgerDirection, LedgerSource


class CloseIn(BaseModel):
    tenant_name: str = Field(min_length=2, max_length=150)
    tenant_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    capture_ref: str = Field(min_length=3, max_length=100, description="The Feature 3 PayPal capture id of the visit token")


class ManagerCloseIn(CloseIn):
    """A manager may invoice amounts that differ from the signed audit, but only with a note."""

    deposit: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    brokerage: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    override_note: str | None = Field(None, max_length=1000)


class SimulateCaptureIn(BaseModel):
    listing_id: str
    amount: Decimal = Field(Decimal("15.00"), gt=0, max_digits=12, decimal_places=2)
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    tenant_email: str | None = None


class SimulateCaptureOut(BaseModel):
    authorization_ref: str
    capture_ref: str
    amount: Decimal
    currency: str


class LedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    listing_id: str
    closing_id: str | None
    kind: str
    amount: Decimal
    currency: str
    direction: LedgerDirection
    paypal_ref: str | None
    source: LedgerSource
    note: str | None
    occurred_at: datetime


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    number: str
    paypal_invoice_id: str | None
    status: InvoiceStatus
    deposit: Decimal
    brokerage: Decimal
    token_deduction: Decimal
    total: Decimal
    currency: str
    due_date: datetime
    sent_at: datetime | None
    paid_at: datetime | None
    reminders_sent: int
    payer_view_url: str | None
    override_note: str | None
    signature_valid: bool = False
    demo: bool = False  # a FAKE-INV- id: shown so it can't be mistaken for a real PayPal invoice


class ClosingSummary(BaseModel):
    """One row of the cockpit: the closing, its invoice and the balance summed from the ledger."""

    id: str
    listing_id: str
    property_id: str
    title: str
    broker_name: str
    tenant_name: str
    tenant_email: str
    capture_ref: str
    audit_id: str
    status: ClosingStatus
    created_at: datetime
    currency: str
    gross: Decimal  # deposit + brokerage
    received: Decimal  # Σ in − Σ out from the ledger
    outstanding: Decimal  # gross − received
    invoice: InvoiceOut | None
    dispute_status: DisputeStatus | None = None
    entry_count: int


class ClosingDetail(ClosingSummary):
    entries: list[LedgerEntryOut]


class LedgerOut(BaseModel):
    closings: list[ClosingSummary]
    entries: list[LedgerEntryOut]
    totals: dict[str, Decimal]  # received per currency, summed from every entry shown


class QrOut(BaseModel):
    data_url: str
    demo: bool


class DisputeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    paypal_dispute_id: str
    closing_id: str | None
    reason: str
    amount: Decimal
    currency: str
    status: DisputeStatus
    evidence_pack: dict | None = None
    draft_response: str | None
    draft_source: str | None
    final_response: str | None
    approved_by: str | None
    submitted_at: datetime | None
    created_at: datetime


class DisputeApproveIn(BaseModel):
    response: str = Field(min_length=20, max_length=2000, description="The text a person approved; sent as-is")


class CopilotIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class CopilotOut(BaseModel):
    answer: str
    refused: bool
    query: dict | None
    target: str | None
    rows: list[dict]
    source: str  # which model (or rule) produced the query


class HoldOut(BaseModel):
    capture_ref: str
    amount: Decimal
    currency: str
    occurred_at: datetime
    # Who booked the visit (Feature 2), so the closing form can start with their details.
    tenant_name: str | None = None
    tenant_email: str | None = None


class ClosableListing(BaseModel):
    """A LIVE verified listing and the captured visit tokens not yet used by a closing."""

    listing_id: str
    property_id: str
    title: str
    broker_name: str
    deposit: Decimal
    brokerage: Decimal
    audit_id: str
    holds: list[HoldOut]
