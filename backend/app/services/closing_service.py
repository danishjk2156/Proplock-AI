"""Close a verified listing: closing + signed invoice + ledger entries + outbox job, in ONE commit.

PayPal is only called afterwards by the outbox (invoice_jobs), so a crash can never leave an
invoice in PayPal that we have no record of, or a record that never reaches PayPal.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    Audit,
    AuditStatus,
    Closing,
    ClosingStatus,
    Invoice,
    InvoiceStatus,
    LedgerSource,
    Listing,
    ListingStatus,
    ListingVersion,
    User,
    new_id,
)
from app.domain.invoice_rules import InvoiceDraft, InvoiceRefused, Override, build_invoice_draft
from app.domain.ledger import EntryKind
from app.domain.signing import data_is_valid, is_valid, sign_data
from app.schemas.audit import AuditReport
from app.services import ledger_service, outbox

CREATE_INVOICE = "create_invoice"
SEND_INVOICE = "send_invoice"
REMIND_INVOICE = "remind_invoice"
CANCEL_INVOICE = "cancel_invoice"
SUBMIT_EVIDENCE = "submit_dispute_evidence"

OPEN_STATUSES = {ClosingStatus.OPEN, ClosingStatus.INVOICED, ClosingStatus.PAID, ClosingStatus.DISPUTED}


class ClosingConflict(Exception):
    """Valid request, but the listing, hold or invoice is in the wrong state for it (409)."""


@dataclass(frozen=True)
class CloseRequest:
    tenant_name: str
    tenant_email: str
    capture_ref: str
    deposit: Decimal | None = None
    brokerage: Decimal | None = None
    override_note: str | None = None


def verified_audit(session: Session, listing: Listing) -> tuple[Audit, ListingVersion, AuditReport]:
    """The signed VERIFIED audit of the listing's latest version, or ClosingConflict."""
    version = session.scalar(
        select(ListingVersion)
        .where(ListingVersion.listing_id == listing.id)
        .order_by(ListingVersion.version_no.desc())
        .limit(1)
    )
    audit = session.scalar(select(Audit).where(Audit.version_id == version.id)) if version else None
    if audit is None or audit.status is not AuditStatus.VERIFIED:
        raise ClosingConflict("Only a listing whose latest version is VERIFIED can be closed")
    if not is_valid(audit.id, audit.report_json, audit.signature, get_settings().audit_secret):
        raise ClosingConflict(f"Audit {audit.id} fails its signature check; it can't be invoiced")
    report = AuditReport.model_validate_json(audit.report_json)
    if report.terms is None:
        raise ClosingConflict(f"Audit {audit.id} has no signed terms to invoice from")
    return audit, version, report


def invoice_payload(invoice: Invoice, closing: Closing, audit: Audit) -> dict:
    """Everything the invoice signature covers. Money as strings, so no float can sneak in."""
    return {
        "invoice_id": invoice.id,
        "number": invoice.number,
        "closing_id": closing.id,
        "listing_id": closing.listing_id,
        "version_id": closing.version_id,
        "audit_id": audit.id,
        "audit_signature": audit.signature,
        "capture_ref": closing.capture_ref,
        "tenant_email": closing.tenant_email,
        "currency": invoice.currency,
        "items": json.loads(invoice.items_json),
        "deposit": f"{Decimal(invoice.deposit):.2f}",
        "brokerage": f"{Decimal(invoice.brokerage):.2f}",
        "token_deduction": f"{Decimal(invoice.token_deduction):.2f}",
        "total": f"{Decimal(invoice.total):.2f}",
        "due_date": invoice.due_date.date().isoformat(),
        "override_note": invoice.override_note,
    }


def invoice_signature_ok(session: Session, invoice: Invoice) -> bool:
    closing = session.get(Closing, invoice.closing_id)
    audit = session.get(Audit, closing.audit_id)
    return data_is_valid(invoice_payload(invoice, closing, audit), invoice.signature, get_settings().audit_secret)


def close_listing(session: Session, actor: User, listing: Listing, req: CloseRequest, *, is_manager: bool) -> tuple[Closing, Invoice]:
    if listing.status is not ListingStatus.LIVE:
        raise ClosingConflict(f"Only a LIVE listing can be closed; this one is {listing.status}")
    audit, version, report = verified_audit(session, listing)

    hold = ledger_service.captured_hold(session, listing.id, req.capture_ref)
    if hold is None:
        raise ClosingConflict(f"No captured visit hold {req.capture_ref} for this listing")
    if session.scalar(select(Closing).where(Closing.capture_ref == req.capture_ref)):
        raise ClosingConflict(f"Hold {req.capture_ref} was already used by another closing")
    if session.scalar(select(Closing).where(Closing.listing_id == listing.id, Closing.status.in_(OPEN_STATUSES))):
        raise ClosingConflict("This listing already has an open closing")

    settings = get_settings()
    if hold.currency != settings.invoice_currency:
        raise ClosingConflict(f"The hold is in {hold.currency} but invoices are in {settings.invoice_currency}")
    wants_override = req.deposit is not None or req.brokerage is not None
    if wants_override and not is_manager:
        # Brokers can't change amounts at all; only a manager can, and only with a reason.
        if (req.deposit is not None and req.deposit != report.terms.deposit) or (
            req.brokerage is not None and req.brokerage != report.terms.brokerage_fee
        ):
            raise InvoiceRefused(
                f"Amounts differ from signed audit {audit.id}; only a manager can override them, with a note"
            )
    override = Override(actor.id, req.override_note or "") if is_manager and req.override_note else None
    draft: InvoiceDraft = build_invoice_draft(
        audit_id=audit.id,
        verified_deposit=report.terms.deposit,
        verified_brokerage=report.terms.brokerage_fee,
        token=Decimal(hold.amount),
        currency=settings.invoice_currency,
        deposit=req.deposit,
        brokerage=req.brokerage,
        override=override,
    )

    now = datetime.now(UTC)
    closing = Closing(
        id=new_id(),
        listing_id=listing.id,
        version_id=version.id,
        audit_id=audit.id,
        broker_id=listing.broker_id,
        tenant_name=req.tenant_name,
        tenant_email=req.tenant_email,
        capture_ref=req.capture_ref,
        created_by=actor.id,
    )
    invoice_id = new_id()
    invoice = Invoice(
        id=invoice_id,
        closing_id=closing.id,
        number=f"PL-{invoice_id[:8].upper()}",  # ours and fixed: PayPal refuses a duplicate number
        items_json=json.dumps([{"name": l.name, "description": l.description, "amount": f"{l.amount:.2f}"} for l in draft.lines]),
        deposit=draft.deposit,
        brokerage=draft.brokerage,
        token_deduction=draft.token_deduction,
        total=draft.total,
        currency=draft.currency,
        due_date=now + timedelta(days=settings.invoice_due_days),
        override_note=override.note if draft.overridden else None,
        signature="",
    )
    invoice.signature = sign_data(invoice_payload(invoice, closing, audit), settings.audit_secret)

    try:
        session.add_all([closing, invoice])
        session.flush()
        common = dict(listing_id=listing.id, closing_id=closing.id, currency=draft.currency, source=LedgerSource.API)
        ledger_service.record(
            session, kind=EntryKind.INVOICE_PREPARED, amount=draft.total, idempotency_key=f"inv-prepared:{invoice.id}",
            note=f"deposit {draft.deposit} + brokerage {draft.brokerage} - visit token {draft.token_deduction} ({req.capture_ref})",
            **common,
        )
        if draft.overridden:
            ledger_service.record(
                session, kind=EntryKind.AMOUNT_OVERRIDE, amount=draft.gross, idempotency_key=f"inv-override:{invoice.id}",
                note=f"Manager {actor.name} changed {', '.join(draft.overridden)} from the signed audit: {override.note}",
                raw={"verified": {"deposit": str(report.terms.deposit), "brokerage": str(report.terms.brokerage_fee)},
                     "invoiced": {"deposit": str(draft.deposit), "brokerage": str(draft.brokerage)}},
                **common,
            )
        listing.status = ListingStatus.RENTED  # the flat is locked; the ad stops being shown
        outbox.enqueue(session, CREATE_INVOICE, invoice.id)  # same commit: never a closing without its job
        session.commit()
    except Exception:
        session.rollback()
        raise
    return closing, invoice


def mark_paid(
    session: Session,
    invoice: Invoice,
    amount: Decimal,
    *,
    source: LedgerSource,
    paypal_ref: str | None,
    raw: dict | None = None,
    occurred_at: datetime | None = None,
) -> bool:
    """Record a payment PayPal confirmed (verified webhook or a direct API read). Idempotent:
    the ledger key is per PayPal invoice, so the webhook and a refresh can't count it twice.
    Returns True if this call changed the invoice."""
    closing = session.get(Closing, invoice.closing_id)
    ledger_service.record(
        session,
        listing_id=closing.listing_id,
        closing_id=closing.id,
        kind=EntryKind.INVOICE_PAID,
        amount=amount,
        currency=invoice.currency,
        idempotency_key=f"inv-paid:{invoice.paypal_invoice_id or invoice.id}",
        source=source,
        paypal_ref=paypal_ref,
        occurred_at=occurred_at,
        raw=raw,
    )
    if invoice.status is InvoiceStatus.PAID:
        return False
    invoice.status = InvoiceStatus.PAID
    invoice.paid_at = occurred_at or datetime.now(UTC)
    if closing.status is not ClosingStatus.DISPUTED:
        closing.status = ClosingStatus.PAID
    return True


def request_cancel(session: Session, invoice: Invoice) -> None:
    if invoice.status in (InvoiceStatus.PAID, InvoiceStatus.REFUNDED, InvoiceStatus.CANCELLED):
        raise ClosingConflict(f"A {invoice.status} invoice can't be cancelled")
    outbox.enqueue(session, CANCEL_INVOICE, invoice.id)
    session.commit()
