"""Outbox handlers for Feature 3: capture or void a visit's PayPal authorization.

Each runs only after the referee's verdict is saved (visit SETTLING), with a request id built
from the visit id, so a crash between PayPal acting and our commit is replayed, never repeated.
Ledger entries use Feature 3's idempotency keys (f3:{kind}:{paypal id}), so the job and a
PayPal webhook reporting the same event count once.
"""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import OutboxJob, Visit, VisitStatus
from app.domain.ledger import EntryKind
from app.domain.referee import Outcome
from app.services.escrow_service import CAPTURE_HOLD, VOID_HOLD, _ledger

logger = logging.getLogger(__name__)

NOTES = {
    Outcome.LOCKED.value: "Tenant locked the flat; the visit token is credited on the move-in invoice.",
    Outcome.TENANT_NO_SHOW.value: "Tenant didn't show; the fee compensates the broker (paid out by the agency).",
}


def capture_hold(session: Session, job: OutboxJob, adapters, now: datetime) -> None:
    visit = session.get(Visit, job.ref_id)
    if visit.status is not VisitStatus.SETTLING:
        return  # already captured (or a webhook said it was voided): nothing to do
    cap = adapters.orders.capture(visit.authorization_ref, visit.paypal_order_id, request_id=f"visit:{visit.id}:capture")
    visit.capture_ref = cap.id
    visit.status = VisitStatus.CAPTURED
    visit.settled_at = now
    _ledger(session, visit, EntryKind.HOLD_CAPTURED, cap.id, now, note=NOTES.get(visit.outcome))


def void_hold(session: Session, job: OutboxJob, adapters, now: datetime) -> None:
    visit = session.get(Visit, job.ref_id)
    late = visit.outcome == Outcome.TOO_LATE.value  # approved after the soft hold ran out
    if visit.settled_at or not visit.authorization_ref or not (late or visit.status is VisitStatus.SETTLING):
        return
    adapters.orders.void(visit.authorization_ref, request_id=f"visit:{visit.id}:void")
    if not late:
        visit.status = VisitStatus.VOIDED
    visit.settled_at = now
    _ledger(session, visit, EntryKind.HOLD_VOIDED, visit.authorization_ref, now, note=visit.outcome_reason)


HANDLERS = {
    CAPTURE_HOLD: ("orders", capture_hold),
    VOID_HOLD: ("orders", void_hold),
}
