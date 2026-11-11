"""Write and read the append-only ledger.

record() is the only way an entry is written. It is idempotent: the same idempotency key
twice returns the first entry instead of adding a second (a retried job, a replayed webhook,
the webhook and a manual refresh both reporting one payment). There is no update or delete
function here, and the model refuses both (app/db/models.py).
"""

import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Closing, LedgerDirection, LedgerEntry, LedgerSource, Listing
from app.domain.ledger import DIRECTION, EntryKind, LedgerEvent, Movement, balance


class LedgerConflict(Exception):
    """The idempotency key was already used for a different event."""


class UnknownListing(Exception):
    pass


def record(
    session: Session,
    *,
    listing_id: str,
    kind: EntryKind,
    amount: Decimal,
    currency: str,
    idempotency_key: str,
    source: LedgerSource,
    closing_id: str | None = None,
    paypal_ref: str | None = None,
    occurred_at: datetime | None = None,
    note: str | None = None,
    raw: dict | None = None,
    direction: LedgerDirection | None = None,
) -> LedgerEntry:
    """Add (not commit) one entry inside the caller's transaction, or return the existing one."""
    existing = session.scalar(select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key))
    if existing is None:
        # It may be pending in this same session (added but not flushed yet).
        existing = next(
            (o for o in session.new if isinstance(o, LedgerEntry) and o.idempotency_key == idempotency_key), None
        )
    amount = Decimal(amount).quantize(Decimal("0.01"))
    if existing is not None:
        if existing.kind != kind or Decimal(existing.amount) != amount:
            raise LedgerConflict(f"idempotency key {idempotency_key} already records a different event")
        return existing
    entry = LedgerEntry(
        listing_id=listing_id,
        closing_id=closing_id,
        kind=kind.value,
        amount=amount,
        currency=currency,
        direction=direction or DIRECTION[kind],
        paypal_ref=paypal_ref,
        idempotency_key=idempotency_key,
        occurred_at=occurred_at or datetime.now(UTC),
        source=source,
        note=note,
        raw_json=json.dumps(raw, default=str) if raw is not None else None,
    )
    session.add(entry)
    return entry


def ingest_feature3(session: Session, event: LedgerEvent) -> LedgerEntry:
    """Feature 3's way in: one hold event becomes one entry (idempotent by its PayPal id)."""
    if session.get(Listing, event.listing_id) is None:
        raise UnknownListing(f"No listing {event.listing_id}")
    entry = record(
        session,
        listing_id=event.listing_id,
        kind=event.kind,
        amount=event.amount,
        currency=event.currency,
        idempotency_key=event.idempotency_key,
        source=LedgerSource.FEATURE3,
        paypal_ref=event.paypal_ref,
        occurred_at=event.occurred_at,
        raw=event.model_dump(mode="json"),
    )
    session.commit()
    return entry


def captured_hold(session: Session, listing_id: str, capture_ref: str) -> LedgerEntry | None:
    return session.scalar(
        select(LedgerEntry).where(
            LedgerEntry.listing_id == listing_id,
            LedgerEntry.kind == EntryKind.HOLD_CAPTURED,
            LedgerEntry.paypal_ref == capture_ref,
        )
    )


def closing_entries(session: Session, closing: Closing) -> list[LedgerEntry]:
    """The closing's own entries plus the Feature 3 capture it deducted (recorded before it existed)."""
    return list(
        session.scalars(
            select(LedgerEntry)
            .where(
                or_(
                    LedgerEntry.closing_id == closing.id,
                    (LedgerEntry.paypal_ref == closing.capture_ref) & (LedgerEntry.kind == EntryKind.HOLD_CAPTURED),
                )
            )
            .order_by(LedgerEntry.occurred_at, LedgerEntry.created_at)
        )
    )


def movements(entries: list[LedgerEntry]) -> list[Movement]:
    return [Movement(Decimal(e.amount), e.currency, e.direction, e.occurred_at) for e in entries]


def closing_balance(session: Session, closing: Closing) -> dict[str, Decimal]:
    """Money received for this closing so far, per currency, summed from the entries."""
    return balance(movements(closing_entries(session, closing)))
