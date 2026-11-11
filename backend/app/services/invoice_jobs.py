"""Outbox handlers for Feature 4. Each one calls PayPal with a request id derived from our own
record (never random), so a job that crashed after PayPal acted is replayed, not repeated.

create_invoice -> send_invoice -> reminders (due-1, due+1, due+3, at most 3, none once paid).
"""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.adapters.paypal_invoicing import InvoiceLine, InvoiceRequest
from app.config import get_settings
from app.db.models import (
    Closing,
    ClosingStatus,
    Dispute,
    DisputeStatus,
    Invoice,
    InvoiceStatus,
    JobStatus,
    LedgerSource,
    OutboxJob,
)
from app.domain.ledger import EntryKind
from app.services import ledger_service, outbox
from app.services.closing_service import (
    CANCEL_INVOICE,
    CREATE_INVOICE,
    REMIND_INVOICE,
    SEND_INVOICE,
    SUBMIT_EVIDENCE,
    invoice_signature_ok,
)

logger = logging.getLogger(__name__)
MAX_REMINDERS = 3


def _refuse(job: OutboxJob, why: str) -> None:
    """A job that must never run (tampered record, missing approval): FAILED, not retried."""
    job.status = JobStatus.FAILED
    job.last_error = why
    logger.error("Outbox job %s refused: %s", job.id, why)


def _ledger(session: Session, invoice: Invoice, kind: EntryKind, key: str, amount: Decimal | None = None, **kw) -> None:
    closing = session.get(Closing, invoice.closing_id)
    ledger_service.record(
        session,
        listing_id=closing.listing_id,
        closing_id=closing.id,
        kind=kind,
        amount=invoice.total if amount is None else amount,
        currency=invoice.currency,
        idempotency_key=key,
        source=LedgerSource.API,
        paypal_ref=invoice.paypal_invoice_id,
        **kw,
    )


def request_for(invoice: Invoice, closing: Closing) -> InvoiceRequest:
    import json

    lines = tuple(InvoiceLine(i["name"], i["description"], Decimal(i["amount"])) for i in json.loads(invoice.items_json))
    note = (
        f"Move-in invoice for PropLock verified listing {closing.listing_id}. "
        f"Amounts match signed audit {closing.audit_id}. "
        f"Visit token already paid (PayPal capture {closing.capture_ref}): {invoice.token_deduction} {invoice.currency} deducted."
    )
    return InvoiceRequest(
        number=invoice.number,
        currency=invoice.currency,
        lines=lines,
        discount=Decimal(invoice.token_deduction),
        recipient_name=closing.tenant_name,
        recipient_email=closing.tenant_email,
        due_date=invoice.due_date.date(),
        note=note,
        reference=invoice.id,
    )


def create_invoice(session: Session, job: OutboxJob, adapters, now: datetime) -> None:
    invoice = session.get(Invoice, job.ref_id)
    if invoice.status is not InvoiceStatus.PENDING:
        return  # an earlier run already created it
    if not invoice_signature_ok(session, invoice):
        return _refuse(job, f"invoice {invoice.id} fails its signature check; not sent to PayPal")
    closing = session.get(Closing, invoice.closing_id)
    created = adapters.invoicing.create_draft(request_for(invoice, closing), request_id=f"create-{invoice.id}")
    invoice.paypal_invoice_id = created.paypal_invoice_id
    invoice.status = InvoiceStatus.DRAFT
    _ledger(session, invoice, EntryKind.INVOICE_DRAFTED, f"inv-drafted:{invoice.id}", occurred_at=now)
    outbox.enqueue(session, SEND_INVOICE, invoice.id, run_at=now)


def reminder_times(due: datetime, now: datetime) -> list[datetime]:
    offsets = [int(x) for x in get_settings().invoice_reminder_offsets.split(",") if x.strip()]
    return [t for t in (due + timedelta(days=d) for d in offsets[:MAX_REMINDERS]) if t > now]


def send_invoice(session: Session, job: OutboxJob, adapters, now: datetime) -> None:
    invoice = session.get(Invoice, job.ref_id)
    if invoice.status is not InvoiceStatus.DRAFT:
        return
    link = adapters.invoicing.send(invoice.paypal_invoice_id, request_id=f"send-{invoice.id}")
    invoice.status = InvoiceStatus.SENT
    invoice.sent_at = now
    invoice.payer_view_url = link or invoice.payer_view_url
    closing = session.get(Closing, invoice.closing_id)
    if closing.status is ClosingStatus.OPEN:
        closing.status = ClosingStatus.INVOICED
    _ledger(session, invoice, EntryKind.INVOICE_SENT, f"inv-sent:{invoice.id}", occurred_at=now,
            note=f"Sent to {closing.tenant_email}")
    for when in reminder_times(invoice.due_date, now):
        outbox.enqueue(session, REMIND_INVOICE, invoice.id, run_at=when)


def remind_invoice(session: Session, job: OutboxJob, adapters, now: datetime) -> None:
    invoice = session.get(Invoice, job.ref_id)
    if invoice.status is not InvoiceStatus.SENT or invoice.reminders_sent >= MAX_REMINDERS:
        return  # paid, cancelled, or enough reminders: nothing to do, the job just ends
    days = (now.date() - invoice.due_date.date()).days
    when = f"{-days} day(s) before" if days < 0 else f"{days} day(s) after" if days else "on"
    adapters.invoicing.remind(
        invoice.paypal_invoice_id, request_id=f"remind-{job.id}",
        note=f"Your PropLock move-in invoice {invoice.number} ({invoice.total} {invoice.currency}) is due {when} the due date.",
    )
    invoice.reminders_sent += 1
    _ledger(session, invoice, EntryKind.INVOICE_REMINDED, f"inv-remind:{job.id}", occurred_at=now,
            note=f"Reminder {invoice.reminders_sent} of {MAX_REMINDERS}")


def cancel_invoice(session: Session, job: OutboxJob, adapters, now: datetime) -> None:
    invoice = session.get(Invoice, job.ref_id)
    if invoice.status in (InvoiceStatus.CANCELLED, InvoiceStatus.PAID, InvoiceStatus.REFUNDED):
        return
    if invoice.paypal_invoice_id:
        adapters.invoicing.cancel(invoice.paypal_invoice_id, request_id=f"cancel-{invoice.id}",
                                  note="This PropLock invoice was cancelled by the agency.")
    invoice.status = InvoiceStatus.CANCELLED
    session.get(Closing, invoice.closing_id).status = ClosingStatus.CANCELLED
    _ledger(session, invoice, EntryKind.INVOICE_CANCELLED, f"inv-cancelled:{invoice.id}", occurred_at=now)


def submit_evidence(session: Session, job: OutboxJob, adapters, now: datetime) -> None:
    dispute = session.get(Dispute, job.ref_id)
    if dispute.status is DisputeStatus.SUBMITTED:
        return
    if dispute.status is not DisputeStatus.APPROVED or not dispute.approved_by or not dispute.final_response:
        return _refuse(job, f"dispute {dispute.id} has no approved response; nothing is sent without a person's approval")
    adapters.invoicing.provide_dispute_evidence(
        dispute.paypal_dispute_id, dispute.final_response, request_id=f"evidence-{dispute.id}"
    )
    dispute.status = DisputeStatus.SUBMITTED
    dispute.submitted_at = now
    if dispute.closing_id:
        closing = session.get(Closing, dispute.closing_id)
        ledger_service.record(
            session, listing_id=closing.listing_id, closing_id=closing.id, kind=EntryKind.DISPUTE_EVIDENCE_SUBMITTED,
            amount=dispute.amount, currency=dispute.currency, idempotency_key=f"dispute-evidence:{dispute.id}",
            source=LedgerSource.API, paypal_ref=dispute.paypal_dispute_id, occurred_at=now,
        )


HANDLERS = {
    CREATE_INVOICE: ("invoicing", create_invoice),
    SEND_INVOICE: ("invoicing", send_invoice),
    REMIND_INVOICE: ("invoicing", remind_invoice),
    CANCEL_INVOICE: ("invoicing", cancel_invoice),
    SUBMIT_EVIDENCE: ("invoicing", submit_evidence),
}
