"""Dispute defense (managers): read the evidence pack, draft a response, approve the exact text.

Approval is the only way anything reaches PayPal: it queues an outbox job that submits the
approved text. The LLM only ever writes a draft.
"""

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.paypal_invoicing import Invoicing, get_invoicing
from app.agents.dispute_copilot import DisputeWriter, get_dispute_writer, write_draft
from app.auth import require_role
from app.config import get_settings
from app.db.base import get_session
from app.db.models import Closing, Dispute, Invoice, Role, User
from app.schemas.closing import DisputeApproveIn, DisputeOut
from app.services import dispute_service, outbox
from app.services.dispute_service import DisputeConflict

router = APIRouter(tags=["disputes"])
manager_only = require_role(Role.MANAGER)


def _out(dispute: Dispute) -> DisputeOut:
    out = DisputeOut.model_validate(dispute)
    out.evidence_pack = dispute_service.pack(dispute)
    return out


def _get(session: Session, dispute_id: str) -> Dispute:
    dispute = session.get(Dispute, dispute_id)
    if dispute is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispute not found")
    return dispute


@router.get("/manager/disputes", response_model=list[DisputeOut])
def list_disputes(_: User = Depends(manager_only), session: Session = Depends(get_session)):
    return [_out(d) for d in session.scalars(select(Dispute).order_by(Dispute.created_at.desc()))]


@router.get("/manager/disputes/{dispute_id}", response_model=DisputeOut)
def get_dispute(dispute_id: str, _: User = Depends(manager_only), session: Session = Depends(get_session)):
    return _out(_get(session, dispute_id))


@router.post("/manager/disputes/{dispute_id}/draft", response_model=DisputeOut)
def draft_response(
    dispute_id: str,
    _: User = Depends(manager_only),
    session: Session = Depends(get_session),
    writer: DisputeWriter = Depends(get_dispute_writer),
):
    """Rebuild the evidence pack from today's records and draft a cited response (not sent)."""
    dispute = _get(session, dispute_id)
    try:
        dispute.evidence_pack_json = json.dumps(dispute_service.evidence_pack(session, dispute), default=str)
        text, source = write_draft(dispute_service.pack(dispute), writer)
        return _out(dispute_service.save_draft(session, dispute, text, source))
    except DisputeConflict as e:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e


@router.post("/manager/disputes/{dispute_id}/approve", response_model=DisputeOut)
def approve_response(
    dispute_id: str,
    body: DisputeApproveIn,
    manager: User = Depends(manager_only),
    session: Session = Depends(get_session),
    invoicing: Invoicing = Depends(get_invoicing),
):
    """A person approves the exact text; the outbox then submits it to PayPal as evidence."""
    dispute = _get(session, dispute_id)
    try:
        dispute_service.approve(session, dispute, manager, body.response)
    except DisputeConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    outbox.try_ref_now(session, dispute.id, invoicing)
    session.refresh(dispute)
    return _out(dispute)


@router.post("/dev/disputes/simulate", response_model=DisputeOut, tags=["dev"])
def simulate_dispute(
    closing_id: str,
    _: User = Depends(manager_only),
    session: Session = Depends(get_session),
):
    """DEMO_MODE only: open a dispute on a closing's invoice as a CUSTOMER.DISPUTE.CREATED
    webhook would, so the dispute screen can be shown without a real buyer. FAKE- id."""
    if not get_settings().demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    closing = session.get(Closing, closing_id)
    invoice = session.scalar(select(Invoice).where(Invoice.closing_id == closing_id)) if closing else None
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No invoice for that closing")
    resource = {
        "dispute_id": f"FAKE-PP-D-{datetime.now(UTC):%Y%m%d%H%M%S%f}",
        "reason": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "dispute_amount": {"currency_code": invoice.currency, "value": f"{invoice.total:.2f}"},
        "disputed_transactions": [{"invoice_number": invoice.number}],
    }
    dispute = dispute_service.open_dispute(session, resource)
    session.commit()
    return _out(dispute)
