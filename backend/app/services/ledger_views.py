"""Read models for the ledger cockpit. Every balance here is summed from ledger entries."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Closing, Dispute, Invoice, LedgerEntry, Listing, ListingVersion, Role, User
from app.schemas.closing import ClosingDetail, ClosingSummary, InvoiceOut, LedgerEntryOut, LedgerOut
from app.services import ledger_service
from app.services.closing_service import invoice_signature_ok

ZERO = Decimal("0.00")


def visible_closings(session: Session, user: User) -> list[Closing]:
    """Brokers see only their own closings; managers see the whole agency's."""
    q = select(Closing).order_by(Closing.created_at.desc())
    if user.role is Role.BROKER:
        q = q.where(Closing.broker_id == user.id)
    return list(session.scalars(q))


def invoice_out(session: Session, invoice: Invoice | None) -> InvoiceOut | None:
    if invoice is None:
        return None
    out = InvoiceOut.model_validate(invoice)
    out.signature_valid = invoice_signature_ok(session, invoice)
    out.demo = bool(invoice.paypal_invoice_id and invoice.paypal_invoice_id.startswith("FAKE-"))
    return out


def summary(session: Session, closing: Closing, entries: list[LedgerEntry] | None = None) -> ClosingSummary:
    entries = entries if entries is not None else ledger_service.closing_entries(session, closing)
    invoice = session.scalar(select(Invoice).where(Invoice.closing_id == closing.id))
    version = session.get(ListingVersion, closing.version_id)
    listing = session.get(Listing, closing.listing_id)
    broker = session.get(User, closing.broker_id)
    dispute = session.scalar(select(Dispute).where(Dispute.closing_id == closing.id).order_by(Dispute.created_at.desc()))
    currency = invoice.currency if invoice else (entries[0].currency if entries else "USD")
    received = ledger_service.closing_balance(session, closing).get(currency, ZERO)
    gross = (Decimal(invoice.deposit) + Decimal(invoice.brokerage)) if invoice else ZERO
    return ClosingSummary(
        id=closing.id,
        listing_id=closing.listing_id,
        property_id=listing.property_id,
        title=version.title,
        broker_name=broker.name,
        tenant_name=closing.tenant_name,
        tenant_email=closing.tenant_email,
        capture_ref=closing.capture_ref,
        audit_id=closing.audit_id,
        status=closing.status,
        created_at=closing.created_at,
        currency=currency,
        gross=gross,
        received=received,
        outstanding=max(gross - received, ZERO),
        invoice=invoice_out(session, invoice),
        dispute_status=dispute.status if dispute else None,
        entry_count=len(entries),
    )


def detail(session: Session, closing: Closing) -> ClosingDetail:
    entries = ledger_service.closing_entries(session, closing)
    base = summary(session, closing, entries)
    return ClosingDetail(**base.model_dump(), entries=[LedgerEntryOut.model_validate(e) for e in entries])


def ledger(session: Session, user: User) -> LedgerOut:
    """All visible closings, plus every entry for the user's listings (holds before a closing too)."""
    closings = visible_closings(session, user)
    q = select(LedgerEntry).order_by(LedgerEntry.occurred_at.desc(), LedgerEntry.created_at.desc())
    if user.role is Role.BROKER:
        q = q.join(Listing, LedgerEntry.listing_id == Listing.id).where(Listing.broker_id == user.id)
    entries = list(session.scalars(q))
    totals = ledger_service.balance(ledger_service.movements(entries))
    return LedgerOut(
        closings=[summary(session, c) for c in closings],
        entries=[LedgerEntryOut.model_validate(e) for e in entries],
        totals=totals,
    )
