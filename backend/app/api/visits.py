"""Feature 2 and 3 endpoints.

Tenants (no login; their hold token, sent as X-Hold-Token, is the key to their visit) talk to
the booking assistant, hold a slot, approve the fee in PayPal (an authorization hold), and at the
flat show their PIN, check in by GPS and choose Pass or Lock. Brokers enter the PIN, check in,
approve a lock or claim a no-show. The referee (app/domain/referee.py) decides what PayPal does.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.adapters.paypal import PayPalError
from app.adapters.paypal_orders import Orders, get_orders
from app.agents.visit_agent import AssistantUnavailable, BookingAssistant, get_booking_assistant
from app.auth import current_user, require_role
from app.config import get_settings, get_upload_dir
from app.db.base import get_session
from app.db.models import Role, User, Visit
from app.domain import visit_rules as rules
from app.domain.referee import Party, Proof
from app.domain.visit_rules import Qualification, SlotRefused
from app.schemas.visit import (
    AssistantIn,
    AssistantOut,
    AuthorizeIn,
    CalendarOut,
    DecisionIn,
    GpsIn,
    HoldIn,
    HoldOut,
    OrderOut,
    PaymentsConfig,
    PinIn,
    SlotsOut,
    VisitOut,
)
from app.services import escrow_service, visit_service
from app.services.escrow_service import EscrowConflict, EscrowRefused, Recorded
from app.services.rate_limit import RateLimiter
from app.services.visit_service import NotBookable, VisitConflict

router = APIRouter(tags=["visits (features 2-3)"])
broker_only = require_role(Role.BROKER)
manager_only = require_role(Role.MANAGER)
assistant_limit = RateLimiter(limit=20, per_seconds=60)  # each message is an LLM call
hold_limit = RateLimiter(limit=10, per_seconds=60)  # stops one visitor blocking a whole calendar
checkin_limit = RateLimiter(limit=30, per_seconds=60)

OFFLINE_ANSWER = (
    "The booking assistant is offline right now, so I can't answer questions. Everything on this "
    "page is verified, and you can still pick a visit time below."
)


def _bookable(session: Session, listing_id: str, now: datetime):
    try:
        return visit_service.bookable(session, listing_id, now)
    except NotBookable as e:
        raise HTTPException(status.HTTP_410_GONE if e.gone else status.HTTP_404_NOT_FOUND, str(e)) from e


def _visit_out(session: Session, visit: Visit, now: datetime, token: str | None = None, message: str | None = None) -> dict:
    booked = visit.authorized_at is not None and visit.pin_hash is not None
    return {
        "id": visit.id,
        "listing_id": visit.listing_id,
        "starts_at": visit.starts_at,
        "ends_at": visit.ends_at,
        "status": rules.effective_status(visit.status, visit.expires_at, now),
        "expires_at": visit.expires_at,
        "fee": visit.fee,
        "currency": visit.currency,
        "paypal_order_id": visit.paypal_order_id,
        "authorization_ref": visit.authorization_ref,
        "capture_ref": visit.capture_ref,
        "demo_authorization": bool(visit.authorization_ref and visit.authorization_ref.startswith("FAKE-")),
        "outcome": visit.outcome,
        "outcome_reason": visit.outcome_reason,
        "pin": escrow_service.tenant_pin(visit, token) if token and booked else None,
        "proof": escrow_service.summary(session, visit, now) if visit.authorized_at else None,
        "message": message,
    }


def _tenant_visit(session: Session, visit_id: str, token: str | None) -> Visit:
    visit = visit_service.for_tenant(session, visit_id, token or "")
    if visit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Visit not found")
    return visit


def _staff_visit(session: Session, visit_id: str, user: User) -> Visit:
    visit = session.get(Visit, visit_id)
    if visit is None or (user.role is Role.BROKER and visit.broker_id != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Visit not found")
    return visit


def _limited(key: str) -> None:
    if not checkin_limit.allow(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many check-ins; try again in a minute")


def _act(session: Session, visit: Visit, now: datetime, run, token: str | None = None) -> dict:
    """Run one escrow step and map its errors to HTTP. PayPal trouble leaves the job queued."""
    try:
        result = run()
    except EscrowConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except EscrowRefused as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    except PayPalError as e:
        session.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"PayPal didn't answer: {e}") from e
    message = result.message if isinstance(result, Recorded) else None
    session.refresh(visit)
    return _visit_out(session, visit, now, token, message)


# --- Feature 2: assistant, slots, hold ---


@router.post("/public/listings/{listing_id}/assistant", response_model=AssistantOut)
def ask_assistant(
    listing_id: str,
    body: AssistantIn,
    request: Request,
    session: Session = Depends(get_session),
    assistant: BookingAssistant = Depends(get_booking_assistant),
):
    """One chat turn. The reply's ready_to_book is re-checked by our rules before it is returned."""
    now = datetime.now(UTC)
    _, view = _bookable(session, listing_id, now)
    client = request.client.host if request.client else "unknown"
    if not assistant_limit.allow(f"{client}:{listing_id}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many messages; try again in a minute")
    window = get_settings().visit_window_days
    if not body.message.strip():
        reply = Qualification(
            answer=f"Hi! I can answer questions about {view['title']} from its verified listing, and book you a visit in the next {window} days.",
            collected=body.collected,
            next_question="What monthly budget do you have in mind, and when would you like to move in?",
        )
        return AssistantOut(available=True, reply=rules.enforce(reply, view["rent"], now, window))
    try:
        reply = assistant.reply(view, body.history, body.message, body.collected, now)
    except AssistantUnavailable as e:
        offline = Qualification(answer=OFFLINE_ANSWER, collected=body.collected)
        return AssistantOut(available=False, reply=rules.enforce(offline, view["rent"], now, window), unavailable_reason=str(e))
    reply = reply.model_copy(update={"collected": body.collected.merged(reply.collected)})
    return AssistantOut(available=True, reply=rules.enforce(reply, view["rent"], now, window))


@router.get("/public/listings/{listing_id}/slots", response_model=SlotsOut)
def open_slots(listing_id: str, session: Session = Depends(get_session)):
    now = datetime.now(UTC)
    listing, _ = _bookable(session, listing_id, now)
    s = get_settings()
    return SlotsOut(
        slots=[vars(slot) for slot in visit_service.available_slots(session, listing, now)],
        window_days=s.visit_window_days,
        hold_minutes=s.visit_soft_hold_minutes,
        transit_buffer_minutes=s.visit_transit_buffer_minutes,
    )


@router.post("/public/listings/{listing_id}/visits", response_model=HoldOut, status_code=status.HTTP_201_CREATED)
def hold_slot(listing_id: str, body: HoldIn, request: Request, session: Session = Depends(get_session)):
    """Keep a slot for a few minutes while the tenant authorises the visit fee."""
    now = datetime.now(UTC)
    listing, _ = _bookable(session, listing_id, now)
    client = request.client.host if request.client else "unknown"
    if not hold_limit.allow(client):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many holds; try again in a minute")
    try:
        visit, token = visit_service.hold(
            session, listing, tenant_name=body.tenant_name, tenant_email=body.tenant_email,
            starts_at=body.starts_at, ends_at=body.ends_at, collected=body.collected, now=now,
        )
    except SlotRefused as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return HoldOut(**_visit_out(session, visit, now), hold_token=token)


@router.get("/public/visits/{visit_id}", response_model=VisitOut)
def get_visit(visit_id: str, x_hold_token: str | None = Header(None), session: Session = Depends(get_session)):
    """The tenant's own visit: status, the door PIN once booked, and the check-in proof so far."""
    return _visit_out(session, _tenant_visit(session, visit_id, x_hold_token), datetime.now(UTC), x_hold_token)


@router.post("/public/visits/{visit_id}/release", response_model=VisitOut)
def release_visit(visit_id: str, x_hold_token: str | None = Header(None), session: Session = Depends(get_session)):
    """The tenant lets the held slot go before authorising it."""
    visit = _tenant_visit(session, visit_id, x_hold_token)
    now = datetime.now(UTC)
    try:
        visit_service.release(session, visit, now)
    except VisitConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return _visit_out(session, visit, now)


# --- Feature 3: PayPal authorization ---


@router.get("/public/payments/config", response_model=PaymentsConfig)
def payments_config(orders: Orders = Depends(get_orders)):
    s = get_settings()
    return PaymentsConfig(paypal_client_id=None if orders.demo else s.paypal_client_id, demo=orders.demo,
                          currency=s.invoice_currency)


@router.post("/public/visits/{visit_id}/paypal-order", response_model=OrderOut)
def paypal_order(visit_id: str, x_hold_token: str | None = Header(None), session: Session = Depends(get_session),
                 orders: Orders = Depends(get_orders)):
    """Create the AUTHORIZE order PayPal's button shows. The amount is the visit's own fee."""
    visit = _tenant_visit(session, visit_id, x_hold_token)
    now = datetime.now(UTC)
    try:
        order_id = escrow_service.open_order(session, visit, orders, now)
    except EscrowConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except PayPalError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"PayPal didn't answer: {e}") from e
    return OrderOut(order_id=order_id, demo=orders.demo)


@router.post("/public/visits/{visit_id}/authorize", response_model=VisitOut)
def authorize_visit(visit_id: str, body: AuthorizeIn | None = None, x_hold_token: str | None = Header(None),
                    session: Session = Depends(get_session), orders: Orders = Depends(get_orders)):
    """After the tenant approved the order: PayPal holds (doesn't charge) the fee; the visit is booked."""
    visit = _tenant_visit(session, visit_id, x_hold_token)
    now = datetime.now(UTC)
    order_id = body.order_id if body else None
    return _act(session, visit, now,
                lambda: escrow_service.authorize(session, visit, x_hold_token, order_id, orders, now), x_hold_token)


# --- Feature 3: check-in proof ---


@router.post("/public/visits/{visit_id}/checkin", response_model=VisitOut)
def tenant_checkin(visit_id: str, body: GpsIn, x_hold_token: str | None = Header(None),
                   session: Session = Depends(get_session), orders: Orders = Depends(get_orders)):
    visit = _tenant_visit(session, visit_id, x_hold_token)
    _limited(f"t:{visit.id}")
    now = datetime.now(UTC)
    return _act(session, visit, now, lambda: escrow_service.record(
        session, visit, Party.TENANT, Proof.GPS, now, orders, gps=(body.latitude, body.longitude, body.accuracy_m)),
        x_hold_token)


@router.post("/public/visits/{visit_id}/photo", response_model=VisitOut)
def tenant_photo(
    visit_id: str,
    file: UploadFile = File(...),
    latitude: Decimal | None = Form(None),
    longitude: Decimal | None = Form(None),
    accuracy_m: Decimal | None = Form(None),
    x_hold_token: str | None = Header(None),
    session: Session = Depends(get_session),
    orders: Orders = Depends(get_orders),
    upload_dir: Path = Depends(get_upload_dir),
):
    """A live photo at the flat: proof for "the broker didn't come". The device's GPS (or EXIF) places it."""
    visit = _tenant_visit(session, visit_id, x_hold_token)
    _limited(f"t:{visit.id}")
    now = datetime.now(UTC)

    def run():
        path, phash, exif_gps = escrow_service.save_photo(file.file.read(), upload_dir)
        gps = (latitude, longitude, accuracy_m) if latitude is not None and longitude is not None else (
            (exif_gps[0], exif_gps[1], None) if exif_gps else None)
        if gps is None:
            raise EscrowRefused("The photo needs a location: allow location access, or use a photo with GPS")
        return escrow_service.record(session, visit, Party.TENANT, Proof.PHOTO, now, orders, gps=gps, photo=(path, phash))

    return _act(session, visit, now, run, x_hold_token)


@router.post("/public/visits/{visit_id}/decision", response_model=VisitOut)
def tenant_decision(visit_id: str, body: DecisionIn, x_hold_token: str | None = Header(None),
                    session: Session = Depends(get_session), orders: Orders = Depends(get_orders)):
    """Pass, Lock (needs the broker's approval), or "the broker didn't come"."""
    visit = _tenant_visit(session, visit_id, x_hold_token)
    now = datetime.now(UTC)
    return _act(session, visit, now, lambda: escrow_service.record(
        session, visit, Party.TENANT, Proof(body.choice), now, orders), x_hold_token)


@router.post("/broker/visits/{visit_id}/pin", response_model=VisitOut)
def broker_pin(visit_id: str, body: PinIn, broker: User = Depends(broker_only), session: Session = Depends(get_session),
               orders: Orders = Depends(get_orders)):
    """The broker types the tenant's PIN at the door: proof both of them were there."""
    visit = _staff_visit(session, visit_id, broker)
    now = datetime.now(UTC)
    return _act(session, visit, now, lambda: escrow_service.enter_pin(session, visit, body.pin, now, orders))


@router.post("/broker/visits/{visit_id}/checkin", response_model=VisitOut)
def broker_checkin(visit_id: str, body: GpsIn, broker: User = Depends(broker_only),
                   session: Session = Depends(get_session), orders: Orders = Depends(get_orders)):
    visit = _staff_visit(session, visit_id, broker)
    _limited(f"b:{visit.id}")
    now = datetime.now(UTC)
    return _act(session, visit, now, lambda: escrow_service.record(
        session, visit, Party.BROKER, Proof.GPS, now, orders, gps=(body.latitude, body.longitude, body.accuracy_m)))


@router.post("/broker/visits/{visit_id}/approve-lock", response_model=VisitOut)
def broker_approve_lock(visit_id: str, broker: User = Depends(broker_only), session: Session = Depends(get_session),
                        orders: Orders = Depends(get_orders)):
    visit = _staff_visit(session, visit_id, broker)
    now = datetime.now(UTC)
    return _act(session, visit, now, lambda: escrow_service.record(session, visit, Party.BROKER, Proof.APPROVE_LOCK, now, orders))


@router.post("/broker/visits/{visit_id}/claim-no-show", response_model=VisitOut)
def broker_claim_no_show(visit_id: str, broker: User = Depends(broker_only), session: Session = Depends(get_session),
                         orders: Orders = Depends(get_orders)):
    visit = _staff_visit(session, visit_id, broker)
    now = datetime.now(UTC)
    return _act(session, visit, now, lambda: escrow_service.record(session, visit, Party.BROKER, Proof.CLAIM_NO_SHOW, now, orders))


@router.post("/manager/visits/{visit_id}/settle-now", response_model=VisitOut)
def manager_settle_now(visit_id: str, manager: User = Depends(manager_only), session: Session = Depends(get_session),
                       orders: Orders = Depends(get_orders)):
    """Settle a booked visit as if its deadline had passed: the referee's rules, applied now."""
    visit = _staff_visit(session, visit_id, manager)
    now = datetime.now(UTC)
    return _act(session, visit, now, lambda: escrow_service.settle_now(session, visit, now, orders))


@router.get("/visits", response_model=CalendarOut)
def visit_calendar(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Brokers see their own visits; managers see every broker's."""
    now = datetime.now(UTC)
    s = get_settings()
    return CalendarOut(
        now=now,
        window_days=s.visit_window_days,
        utc_offset_minutes=s.visit_utc_offset_minutes,
        visit_hours=s.visit_hours,
        transit_buffer_minutes=s.visit_transit_buffer_minutes,
        visits=visit_service.calendar(session, user, now),
    )
