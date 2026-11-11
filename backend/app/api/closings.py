"""Feature 4 endpoints: close a verified listing, read the ledger, and manage the invoice."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.paypal import PayPalError
from app.adapters.paypal_invoicing import Invoicing, get_invoicing, png_data_url
from app.auth import current_user, require_role
from app.config import get_settings
from app.db.base import get_session
from app.db.models import Closing, Invoice, InvoiceStatus, LedgerSource, Listing, Role, User
from app.domain.invoice_rules import InvoiceRefused
from app.domain.ledger import EntryKind, LedgerEvent
from app.schemas.closing import (
    CloseIn,
    ClosingDetail,
    LedgerOut,
    ManagerCloseIn,
    QrOut,
    SimulateCaptureIn,
    SimulateCaptureOut,
)
from app.services import closing_service, ledger_service, ledger_views, outbox
from app.services.closing_service import ClosingConflict, CloseRequest
from app.services.ledger_service import LedgerConflict, UnknownListing

router = APIRouter(tags=["closing & ledger"])
broker_only = require_role(Role.BROKER)
manager_only = require_role(Role.MANAGER)


def _close(session: Session, actor: User, listing: Listing, body: CloseIn, invoicing: Invoicing, *, is_manager: bool):
    extra = body.model_dump(include={"deposit", "brokerage", "override_note"}) if is_manager else {}
    req = CloseRequest(body.tenant_name, body.tenant_email, body.capture_ref, **extra)
    try:
        closing, invoice = closing_service.close_listing(session, actor, listing, req, is_manager=is_manager)
    except InvoiceRefused as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    except ClosingConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    # Saved first; now try create -> send straight away. If PayPal is down the jobs stay queued.
    outbox.try_ref_now(session, invoice.id, invoicing)
    session.refresh(closing)
    return ledger_views.detail(session, closing)


@router.post("/broker/listings/{listing_id}/close", response_model=ClosingDetail, status_code=status.HTTP_201_CREATED)
def broker_close(
    listing_id: str,
    body: CloseIn,
    broker: User = Depends(broker_only),
    session: Session = Depends(get_session),
    invoicing: Invoicing = Depends(get_invoicing),
):
    """The tenant locked the flat: invoice the verified deposit + brokerage minus the visit token."""
    listing = session.get(Listing, listing_id)
    if listing is None or listing.broker_id != broker.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")
    return _close(session, broker, listing, body, invoicing, is_manager=False)


@router.post("/manager/listings/{listing_id}/close", response_model=ClosingDetail, status_code=status.HTTP_201_CREATED)
def manager_close(
    listing_id: str,
    body: ManagerCloseIn,
    manager: User = Depends(manager_only),
    session: Session = Depends(get_session),
    invoicing: Invoicing = Depends(get_invoicing),
):
    """Like the broker's close, but amounts may differ from the signed audit with an override note."""
    listing = session.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")
    return _close(session, manager, listing, body, invoicing, is_manager=True)


@router.get("/ledger", response_model=LedgerOut)
def get_ledger(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Every closing and money event the caller may see (brokers: their own; managers: all)."""
    return ledger_views.ledger(session, user)


def _visible_closing(session: Session, user: User, closing_id: str) -> Closing:
    closing = session.get(Closing, closing_id)
    if closing is None or (user.role is Role.BROKER and closing.broker_id != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Closing not found")
    return closing


def _invoice(session: Session, closing: Closing) -> Invoice:
    invoice = session.scalar(select(Invoice).where(Invoice.closing_id == closing.id))
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This closing has no invoice")
    return invoice


@router.get("/closings/{closing_id}", response_model=ClosingDetail)
def get_closing(closing_id: str, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return ledger_views.detail(session, _visible_closing(session, user, closing_id))


@router.post("/closings/{closing_id}/refresh", response_model=ClosingDetail)
def refresh_from_paypal(
    closing_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
    invoicing: Invoicing = Depends(get_invoicing),
):
    """Retry pending jobs for this invoice, then read its status from PayPal directly. A payment
    read straight from PayPal's API is trusted like a verified webhook (and deduplicated with it)."""
    closing = _visible_closing(session, user, closing_id)
    invoice = _invoice(session, closing)
    outbox.try_ref_now(session, invoice.id, invoicing)
    session.refresh(invoice)
    if invoice.paypal_invoice_id and invoice.status is InvoiceStatus.SENT:
        try:
            state = invoicing.get(invoice.paypal_invoice_id)
        except PayPalError as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Couldn't read the invoice from PayPal: {e}") from e
        if state.status in ("PAID", "MARKED_AS_PAID"):
            closing_service.mark_paid(
                session, invoice, state.paid_amount or invoice.total,
                source=LedgerSource.API, paypal_ref=invoice.paypal_invoice_id, raw={"status": state.status},
            )
            session.commit()
    return ledger_views.detail(session, closing)


@router.post("/closings/{closing_id}/cancel", response_model=ClosingDetail)
def cancel_invoice(
    closing_id: str,
    _: User = Depends(manager_only),
    session: Session = Depends(get_session),
    invoicing: Invoicing = Depends(get_invoicing),
):
    """Managers only: cancel an unpaid invoice (through the outbox, like every PayPal call)."""
    closing = _visible_closing(session, _, closing_id)
    invoice = _invoice(session, closing)
    try:
        closing_service.request_cancel(session, invoice)
    except ClosingConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    outbox.try_ref_now(session, invoice.id, invoicing)
    return ledger_views.detail(session, closing)


@router.get("/closings/{closing_id}/qr", response_model=QrOut)
def invoice_qr(
    closing_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
    invoicing: Invoicing = Depends(get_invoicing),
):
    """PayPal's QR code for the tenant's pay link (read-only; moves no money)."""
    invoice = _invoice(session, _visible_closing(session, user, closing_id))
    if not invoice.paypal_invoice_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "The invoice isn't in PayPal yet")
    try:
        b64 = invoicing.qr_code(invoice.paypal_invoice_id)
    except PayPalError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Couldn't get the QR code from PayPal: {e}") from e
    return QrOut(data_url=png_data_url(b64), demo=invoice.paypal_invoice_id.startswith("FAKE-"))


# --- Feature 3 stand-in (dev only) ---


@router.post("/dev/ledger/simulate-capture", response_model=SimulateCaptureOut, tags=["dev"])
def simulate_capture(
    body: SimulateCaptureIn,
    _: User = Depends(manager_only),
    session: Session = Depends(get_session),
):
    """DEMO_MODE only: pretend Feature 3 authorised and captured a visit token, so Feature 4 can
    be shown before Feature 3 exists. The ids start with FAKE- so they can't pass as real."""
    if not get_settings().demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    stamp = datetime.now(UTC)
    tag = stamp.strftime("%Y%m%d%H%M%S%f")
    auth_ref, capture_ref = f"FAKE-AUTH-{tag}", f"FAKE-CAP-{tag}"
    try:
        for kind, ref, at in ((EntryKind.HOLD_AUTHORIZED, auth_ref, stamp - timedelta(minutes=30)),
                              (EntryKind.HOLD_CAPTURED, capture_ref, stamp)):
            ledger_service.ingest_feature3(session, LedgerEvent(
                kind=kind, listing_id=body.listing_id, amount=body.amount, currency=body.currency,
                paypal_ref=ref, occurred_at=at, tenant_email=body.tenant_email,
            ))
    except UnknownListing as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except LedgerConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return SimulateCaptureOut(authorization_ref=auth_ref, capture_ref=capture_ref, amount=body.amount, currency=body.currency)
