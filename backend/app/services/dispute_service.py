"""Disputes: open from a webhook, assemble the evidence pack, draft, approve, submit.

The evidence pack is built only from records PropLock already holds, and every item carries
a source id ("audit:…", "invoice:…", "ledger:…") so the draft can cite it and a person can
check each claim. A draft never leaves PropLock until a manager approves the exact text.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    Audit,
    Closing,
    ClosingStatus,
    Dispute,
    DisputeStatus,
    Invoice,
    LedgerSource,
    ListingPhoto,
    PhotoKind,
    TrustEvent,
    User,
)
from app.domain.ledger import EntryKind
from app.domain.signing import is_valid
from app.schemas.audit import AuditReport
from app.services import ledger_service, outbox
from app.services.closing_service import SUBMIT_EVIDENCE, invoice_signature_ok


class DisputeConflict(Exception):
    pass


def _closing_for(session: Session, resource: dict) -> Closing | None:
    for txn in resource.get("disputed_transactions") or []:
        number = txn.get("invoice_number") or txn.get("custom")
        if number:
            invoice = session.scalar(select(Invoice).where(Invoice.number == number))
            if invoice:
                return session.get(Closing, invoice.closing_id)
        for item in txn.get("items") or []:
            invoice = session.scalar(select(Invoice).where(Invoice.number == item.get("invoice_number")))
            if invoice:
                return session.get(Closing, invoice.closing_id)
    return None


def open_dispute(session: Session, resource: dict, occurred_at: datetime | None = None) -> Dispute:
    """Add (not commit) the dispute, its ledger entry and its evidence pack. Idempotent per PayPal id."""
    paypal_id = resource.get("dispute_id")
    if not paypal_id:
        raise ValueError("dispute event without dispute_id")
    existing = session.scalar(select(Dispute).where(Dispute.paypal_dispute_id == paypal_id))
    if existing:
        return existing
    closing = _closing_for(session, resource)
    amount = resource.get("dispute_amount") or {}
    dispute = Dispute(
        paypal_dispute_id=paypal_id,
        closing_id=closing.id if closing else None,
        reason=str(resource.get("reason", "UNKNOWN"))[:80],
        amount=Decimal(amount.get("value", "0")),
        currency=amount.get("currency_code", "USD"),
    )
    session.add(dispute)
    session.flush()
    if closing:
        closing.status = ClosingStatus.DISPUTED
        ledger_service.record(
            session, listing_id=closing.listing_id, closing_id=closing.id, kind=EntryKind.DISPUTE_OPENED,
            amount=dispute.amount, currency=dispute.currency, idempotency_key=f"dispute-opened:{paypal_id}",
            source=LedgerSource.WEBHOOK, paypal_ref=paypal_id, note=dispute.reason, occurred_at=occurred_at,
        )
        session.flush()
    dispute.evidence_pack_json = json.dumps(evidence_pack(session, dispute), default=str)
    return dispute


def _item(source_id: str, kind: str, summary: str, **facts) -> dict:
    return {"source_id": source_id, "kind": kind, "summary": summary, "facts": facts}


def evidence_pack(session: Session, dispute: Dispute) -> dict:
    """Facts from our own records only. Missing evidence is listed as missing, never filled in."""
    items: list[dict] = [
        _item(f"dispute:{dispute.paypal_dispute_id}", "dispute", f"PayPal dispute {dispute.paypal_dispute_id}: "
              f"{dispute.reason}, {dispute.amount} {dispute.currency}", reason=dispute.reason, amount=str(dispute.amount))
    ]
    missing: list[str] = []
    if dispute.closing_id is None:
        missing.append("No PropLock invoice matches this dispute, so no closing records are attached.")
        return {"dispute_id": dispute.paypal_dispute_id, "items": items, "missing": missing}

    from app.api.public import current_mismatches  # the same live-data check /verify uses

    closing = session.get(Closing, dispute.closing_id)
    audit = session.get(Audit, closing.audit_id)
    report = AuditReport.model_validate_json(audit.report_json)
    sig_ok = is_valid(audit.id, audit.report_json, audit.signature, get_settings().audit_secret)
    mismatches = current_mismatches(session, audit, report)
    terms = report.terms
    items.append(_item(
        f"audit:{audit.id}", "signed_listing_audit",
        f"Listing audit {audit.id} ({audit.status}) signed {audit.created_at:%Y-%m-%d}; signature "
        f"{'valid' if sig_ok else 'INVALID'}; verified rent {terms.rent}, deposit {terms.deposit}, brokerage {terms.brokerage_fee}.",
        decision=report.decision, signature_valid=sig_ok, live_mismatches=mismatches,
        rent=str(terms.rent), deposit=str(terms.deposit), brokerage=str(terms.brokerage_fee),
        furnishing=terms.furnishing, checks={c.check: c.verdict for c in report.checks},
    ))
    for photo in session.scalars(select(ListingPhoto).where(ListingPhoto.listing_id == closing.listing_id,
                                                           ListingPhoto.kind == PhotoKind.CHECKIN)):
        items.append(_item(
            f"photo:{photo.id}", "broker_checkin_photo",
            f"Broker check-in photo at the flat, taken {photo.captured_at or photo.created_at:%Y-%m-%d %H:%M} UTC "
            f"({photo.gps_source or 'no'} GPS {photo.latitude}, {photo.longitude}).",
            phash=photo.phash, gps_source=photo.gps_source,
        ))
    missing.append("Tenant visit check-in (Feature 3 PIN/GPS) is not recorded yet: Feature 3 is not built.")

    invoice = session.scalar(select(Invoice).where(Invoice.closing_id == closing.id))
    if invoice:
        items.append(_item(
            f"invoice:{invoice.id}", "invoice",
            f"Invoice {invoice.number} (PayPal {invoice.paypal_invoice_id}) to {closing.tenant_email}: deposit "
            f"{invoice.deposit} + brokerage {invoice.brokerage} - visit token {invoice.token_deduction} = {invoice.total} "
            f"{invoice.currency}; status {invoice.status}; signature {'valid' if invoice_signature_ok(session, invoice) else 'INVALID'}.",
            amounts_match_audit=invoice.override_note is None, override_note=invoice.override_note,
            sent_at=invoice.sent_at, paid_at=invoice.paid_at,
        ))
    for entry in ledger_service.closing_entries(session, closing):
        items.append(_item(
            f"ledger:{entry.id}", "ledger_entry",
            f"{entry.occurred_at:%Y-%m-%d %H:%M} UTC {entry.kind} {entry.amount} {entry.currency} ({entry.direction})"
            + (f", PayPal ref {entry.paypal_ref}" if entry.paypal_ref else ""),
            source=entry.source,
        ))
    broker = session.get(User, closing.broker_id)
    events = session.scalars(select(TrustEvent).where(TrustEvent.broker_id == broker.id)).all()
    items.append(_item(
        f"trust:{broker.id}", "broker_trust", f"Broker {broker.name}: trust score {broker.trust_score} from {len(events)} recorded event(s).",
        score=broker.trust_score, events=[e.kind for e in events],
    ))
    return {"dispute_id": dispute.paypal_dispute_id, "closing_id": closing.id, "items": items, "missing": missing}


def pack(dispute: Dispute) -> dict:
    return json.loads(dispute.evidence_pack_json) if dispute.evidence_pack_json else {"items": [], "missing": []}


def save_draft(session: Session, dispute: Dispute, text: str, source: str) -> Dispute:
    if dispute.status not in (DisputeStatus.OPEN,):
        raise DisputeConflict(f"A {dispute.status} dispute can't be redrafted")
    dispute.draft_response = text
    dispute.draft_source = source
    session.commit()
    return dispute


def approve(session: Session, dispute: Dispute, manager: User, text: str) -> Dispute:
    """A person approves the exact text; only then is a submit job queued (same commit)."""
    if dispute.status is not DisputeStatus.OPEN:
        raise DisputeConflict(f"A {dispute.status} dispute can't be approved again")
    dispute.final_response = text
    dispute.approved_by = manager.id
    dispute.status = DisputeStatus.APPROVED
    outbox.enqueue(session, SUBMIT_EVIDENCE, dispute.id, run_at=datetime.now(UTC))
    session.commit()
    return dispute
