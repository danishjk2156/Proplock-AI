"""The ledger's vocabulary and arithmetic. Pure: no database, no network.

Every money event is one append-only entry. A balance is never stored; it is summed from the
entries each time, so it can always be explained row by row.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.db.models import LedgerDirection


class EntryKind(StrEnum):
    # Feature 3 (visit escrow)
    HOLD_AUTHORIZED = "HOLD_AUTHORIZED"
    HOLD_CAPTURED = "HOLD_CAPTURED"
    HOLD_VOIDED = "HOLD_VOIDED"
    # Feature 4 (closing)
    INVOICE_PREPARED = "INVOICE_PREPARED"
    AMOUNT_OVERRIDE = "AMOUNT_OVERRIDE"
    INVOICE_DRAFTED = "INVOICE_DRAFTED"
    INVOICE_SENT = "INVOICE_SENT"
    INVOICE_REMINDED = "INVOICE_REMINDED"
    INVOICE_PAID = "INVOICE_PAID"
    INVOICE_CANCELLED = "INVOICE_CANCELLED"
    INVOICE_REFUNDED = "INVOICE_REFUNDED"
    DISPUTE_OPENED = "DISPUTE_OPENED"
    DISPUTE_EVIDENCE_SUBMITTED = "DISPUTE_EVIDENCE_SUBMITTED"
    DISPUTE_RESOLVED = "DISPUTE_RESOLVED"
    REVERSAL = "REVERSAL"  # corrects an earlier entry; the original row is never edited


# Which way money moves for each kind. A hold is only authorised money, so it is info until captured.
DIRECTION: dict[EntryKind, LedgerDirection] = {
    EntryKind.HOLD_AUTHORIZED: LedgerDirection.INFO,
    EntryKind.HOLD_CAPTURED: LedgerDirection.IN,
    EntryKind.HOLD_VOIDED: LedgerDirection.INFO,
    EntryKind.INVOICE_PREPARED: LedgerDirection.INFO,
    EntryKind.AMOUNT_OVERRIDE: LedgerDirection.INFO,
    EntryKind.INVOICE_DRAFTED: LedgerDirection.INFO,
    EntryKind.INVOICE_SENT: LedgerDirection.INFO,
    EntryKind.INVOICE_REMINDED: LedgerDirection.INFO,
    EntryKind.INVOICE_PAID: LedgerDirection.IN,
    EntryKind.INVOICE_CANCELLED: LedgerDirection.INFO,
    EntryKind.INVOICE_REFUNDED: LedgerDirection.OUT,
    EntryKind.DISPUTE_OPENED: LedgerDirection.INFO,
    EntryKind.DISPUTE_EVIDENCE_SUBMITTED: LedgerDirection.INFO,
    EntryKind.DISPUTE_RESOLVED: LedgerDirection.INFO,
}

FEATURE3_KINDS = {EntryKind.HOLD_AUTHORIZED, EntryKind.HOLD_CAPTURED, EntryKind.HOLD_VOIDED}


class LedgerEvent(BaseModel):
    """The contract Feature 3 (visit escrow) sends for each hold event.

    Feature 3 is not built yet; POST /dev/ledger/simulate-capture produces these in DEMO_MODE.
    paypal_ref is the PayPal authorization id (HOLD_AUTHORIZED / HOLD_VOIDED) or capture id
    (HOLD_CAPTURED); the capture id is what a closing later deducts.
    """

    kind: EntryKind
    listing_id: str
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    paypal_ref: str = Field(min_length=3, max_length=100)
    occurred_at: AwareDatetime
    tenant_email: str | None = None

    @model_validator(mode="after")
    def _feature3_kind(self) -> "LedgerEvent":
        if self.kind not in FEATURE3_KINDS:
            raise ValueError(f"Feature 3 can only send {', '.join(sorted(FEATURE3_KINDS))}, not {self.kind}")
        return self

    @property
    def idempotency_key(self) -> str:
        return f"f3:{self.kind}:{self.paypal_ref}"


@dataclass(frozen=True)
class Movement:
    """The parts of an entry that arithmetic needs."""

    amount: Decimal
    currency: str
    direction: LedgerDirection
    occurred_at: datetime | None = None


def balance(entries: Iterable[Movement]) -> dict[str, Decimal]:
    """Money held per currency: everything in minus everything out. Info entries count for nothing."""
    totals: dict[str, Decimal] = {}
    for e in entries:
        sign = {LedgerDirection.IN: 1, LedgerDirection.OUT: -1}.get(e.direction, 0)
        if sign:
            totals[e.currency] = totals.get(e.currency, Decimal("0")) + sign * e.amount
    return {c: v.quantize(Decimal("0.01")) for c, v in totals.items()}
