"""PayPal webhooks: store every event, trust only verified ones, apply each event id once.

Order matters: the raw event is saved first (so nothing is lost even if handling fails), then
it is applied only if PayPal verified it. A replayed event id that was already applied is a
no-op, and every ledger write is idempotent too, so even a double delivery can't count twice.
"""

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Closing,
    ClosingStatus,
    Dispute,
    DisputeStatus,
    Invoice,
    InvoiceStatus,
    LedgerSource,
    Visit,
    VisitStatus,
    WebhookEvent,
)
from app.domain.ledger import EntryKind
from app.services import closing_service, dispute_service, escrow_service, ledger_service

logger = logging.getLogger(__name__)


class WebhookRejected(Exception):
    """Unverified or malformed: stored, never applied (400)."""


def _amount(value: dict | None) -> Decimal | None:
    return Decimal(value["value"]) if value and value.get("value") is not None else None


def _invoice_for(session: Session, resource: dict) -> Invoice | None:
    invoice_id = resource.get("id") or (resource.get("invoice") or {}).get("id")
    if invoice_id:
        found = session.scalar(select(Invoice).where(Invoice.paypal_invoice_id == invoice_id))
        if found:
            return found
    number = (resource.get("detail") or {}).get("invoice_number")
    return session.scalar(select(Invoice).where(Invoice.number == number)) if number else None


def _occurred(event: dict) -> datetime:
    try:
        return datetime.fromisoformat(event["create_time"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError):
        return datetime.now(UTC)


def on_invoice_paid(session: Session, event: dict) -> str:
    resource = event.get("resource", {})
    resource = resource.get("invoice", resource)  # PayPal nests the invoice in some versions
    invoice = _invoice_for(session, resource)
    if invoice is None:
        return "no matching invoice"
    paid = _amount((resource.get("payments") or {}).get("paid_amount")) or _amount(resource.get("amount")) or Decimal(invoice.total)
    closing_service.mark_paid(session, invoice, paid, source=LedgerSource.WEBHOOK, paypal_ref=invoice.paypal_invoice_id,
                              raw={"event_id": event.get("id")}, occurred_at=_occurred(event))
    return f"invoice {invoice.number} paid"


def on_invoice_cancelled(session: Session, event: dict) -> str:
    resource = event.get("resource", {})
    invoice = _invoice_for(session, resource.get("invoice", resource))
    if invoice is None:
        return "no matching invoice"
    if invoice.status not in (InvoiceStatus.PAID, InvoiceStatus.REFUNDED):
        invoice.status = InvoiceStatus.CANCELLED
        session.get(Closing, invoice.closing_id).status = ClosingStatus.CANCELLED
    closing = session.get(Closing, invoice.closing_id)
    ledger_service.record(session, listing_id=closing.listing_id, closing_id=closing.id, kind=EntryKind.INVOICE_CANCELLED,
                          amount=invoice.total, currency=invoice.currency, idempotency_key=f"inv-cancelled:{invoice.id}",
                          source=LedgerSource.WEBHOOK, paypal_ref=invoice.paypal_invoice_id, occurred_at=_occurred(event))
    return f"invoice {invoice.number} cancelled"


def on_invoice_refunded(session: Session, event: dict) -> str:
    resource = event.get("resource", {})
    resource = resource.get("invoice", resource)
    invoice = _invoice_for(session, resource)
    if invoice is None:
        return "no matching invoice"
    refunds = (resource.get("refunds") or {}).get("refund_amount")
    amount = _amount(refunds) or Decimal(invoice.total)
    closing = session.get(Closing, invoice.closing_id)
    ledger_service.record(session, listing_id=closing.listing_id, closing_id=closing.id, kind=EntryKind.INVOICE_REFUNDED,
                          amount=amount, currency=invoice.currency, idempotency_key=f"inv-refund:{event.get('id')}",
                          source=LedgerSource.WEBHOOK, paypal_ref=invoice.paypal_invoice_id, occurred_at=_occurred(event))
    invoice.status = InvoiceStatus.REFUNDED
    return f"invoice {invoice.number} refunded {amount}"


def on_dispute_created(session: Session, event: dict) -> str:
    dispute = dispute_service.open_dispute(session, event.get("resource", {}), occurred_at=_occurred(event))
    return f"dispute {dispute.paypal_dispute_id} opened"


def on_dispute_changed(session: Session, event: dict) -> str:
    resource = event.get("resource", {})
    dispute = session.scalar(select(Dispute).where(Dispute.paypal_dispute_id == resource.get("dispute_id")))
    if dispute is None:
        dispute = dispute_service.open_dispute(session, resource, occurred_at=_occurred(event))
    if event.get("event_type") == "CUSTOMER.DISPUTE.RESOLVED" or resource.get("status") == "RESOLVED":
        dispute.status = DisputeStatus.RESOLVED
        if dispute.closing_id:
            closing = session.get(Closing, dispute.closing_id)
            outcome = (resource.get("dispute_outcome") or {}).get("outcome_code", "RESOLVED")
            ledger_service.record(session, listing_id=closing.listing_id, closing_id=closing.id,
                                  kind=EntryKind.DISPUTE_RESOLVED, amount=dispute.amount, currency=dispute.currency,
                                  idempotency_key=f"dispute-resolved:{dispute.id}", source=LedgerSource.WEBHOOK,
                                  paypal_ref=dispute.paypal_dispute_id, note=outcome, occurred_at=_occurred(event))
            invoice = session.scalar(select(Invoice).where(Invoice.closing_id == closing.id))
            closing.status = ClosingStatus.PAID if invoice and invoice.status is InvoiceStatus.PAID else closing.status
    return f"dispute {dispute.paypal_dispute_id} {dispute.status}"


# --- Feature 3: visit holds. Webhooks confirm or correct what the outbox did; they never decide. ---


def _visit_for(session: Session, auth_id: str | None, capture_id: str | None = None) -> Visit | None:
    if capture_id:
        found = session.scalar(select(Visit).where(Visit.capture_ref == capture_id))
        if found:
            return found
    return session.scalar(select(Visit).where(Visit.authorization_ref == auth_id)) if auth_id else None


def on_capture_completed(session: Session, event: dict) -> str:
    resource = event.get("resource", {})
    capture_id = resource.get("id")
    auth_id = ((resource.get("supplementary_data") or {}).get("related_ids") or {}).get("authorization_id")
    visit = _visit_for(session, auth_id, capture_id)
    if visit is None:
        return "no matching visit"
    if visit.status is VisitStatus.SETTLING:
        visit.status = VisitStatus.CAPTURED
        visit.capture_ref = capture_id
        visit.settled_at = _occurred(event)
    elif visit.status is not VisitStatus.CAPTURED:
        return f"visit {visit.id} is {visit.status.value}; capture {capture_id} needs a person to look"
    escrow_service._ledger(session, visit, EntryKind.HOLD_CAPTURED, visit.capture_ref or capture_id, _occurred(event),
                           source=LedgerSource.WEBHOOK)
    return f"visit {visit.id} captured"


def on_capture_denied(session: Session, event: dict) -> str:
    resource = event.get("resource", {})
    auth_id = ((resource.get("supplementary_data") or {}).get("related_ids") or {}).get("authorization_id")
    visit = _visit_for(session, auth_id, resource.get("id"))
    if visit is None:
        return "no matching visit"
    visit.outcome_reason = f"{visit.outcome_reason or ''} PayPal denied the capture; a manager must look.".strip()
    return f"visit {visit.id} capture denied"


def on_authorization_voided(session: Session, event: dict) -> str:
    resource = event.get("resource", {})
    visit = _visit_for(session, resource.get("id"))
    if visit is None:
        return "no matching visit"
    if visit.status is VisitStatus.COMMITTED:  # released outside PropLock (e.g. expired at PayPal)
        visit.status = VisitStatus.SETTLING
        visit.outcome = visit.outcome or "NO_PROOF"
        visit.outcome_reason = "PayPal released the hold before the visit was settled."
    if visit.status is VisitStatus.SETTLING:
        visit.status = VisitStatus.VOIDED
    if not visit.settled_at:
        visit.settled_at = _occurred(event)
    escrow_service._ledger(session, visit, EntryKind.HOLD_VOIDED, visit.authorization_ref, _occurred(event),
                           source=LedgerSource.WEBHOOK, note=visit.outcome_reason)
    return f"visit {visit.id} voided"


def on_order_approved(session: Session, event: dict) -> str:
    order_id = (event.get("resource") or {}).get("id")
    visit = session.scalar(select(Visit).where(Visit.paypal_order_id == order_id)) if order_id else None
    return f"order {order_id} approved; the tenant's page authorises it" if visit else "no matching visit"


HANDLERS = {
    "PAYMENT.CAPTURE.COMPLETED": on_capture_completed,
    "PAYMENT.CAPTURE.DENIED": on_capture_denied,
    "PAYMENT.AUTHORIZATION.VOIDED": on_authorization_voided,
    "CHECKOUT.ORDER.APPROVED": on_order_approved,
    "INVOICING.INVOICE.PAID": on_invoice_paid,
    "INVOICING.INVOICE.CANCELLED": on_invoice_cancelled,
    "INVOICING.INVOICE.REFUNDED": on_invoice_refunded,
    "CUSTOMER.DISPUTE.CREATED": on_dispute_created,
    "CUSTOMER.DISPUTE.UPDATED": on_dispute_changed,
    "CUSTOMER.DISPUTE.RESOLVED": on_dispute_changed,
}


def receive(session: Session, raw_body: bytes, headers: dict[str, str], verifier) -> tuple[str, str]:
    """Returns (outcome, message): outcome is applied | duplicate | ignored. Raises WebhookRejected."""
    try:
        event = json.loads(raw_body)
        event_id, event_type = event["id"], event["event_type"]
    except (ValueError, KeyError, TypeError) as e:
        raise WebhookRejected("Not a PayPal webhook event") from e

    stored = session.get(WebhookEvent, event_id)
    if stored is not None and stored.processed_at is not None:
        return "duplicate", f"event {event_id} was already applied"

    try:
        verified = verifier.verify(headers, raw_body, event)
    except Exception as e:  # PayPal unreachable: don't trust it now; PayPal retries later
        logger.error("Webhook %s verification failed: %s", event_id, e)
        verified = False
    if stored is None:
        stored = WebhookEvent(paypal_event_id=event_id, event_type=event_type, raw_json=raw_body.decode("utf-8", "replace"))
        session.add(stored)
    stored.verified = verified
    stored.error = None if verified else "signature not verified"
    session.commit()  # the raw event is kept whatever happens next
    if not verified:
        raise WebhookRejected("Webhook signature could not be verified; nothing was changed")

    handler = HANDLERS.get(event_type)
    try:
        message = handler(session, event) if handler else f"{event_type} is not handled"
        stored.processed_at = datetime.now(UTC)
        stored.error = None
        session.commit()
    except Exception as e:
        session.rollback()
        stored = session.get(WebhookEvent, event_id)
        stored.error = f"{type(e).__name__}: {e}"[:1000]
        session.commit()
        raise
    return ("applied" if handler else "ignored"), message
