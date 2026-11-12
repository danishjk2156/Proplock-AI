"""Feature 2: book a site visit on a verified listing.

hold() keeps a slot for a few minutes (SOFT_HOLD). Feature 3 (escrow_service) then books it
once PayPal authorises the visit fee, referees the visit and captures or voids the hold; a
capture is the token Feature 4's closing invoice deducts. A hold nobody authorises expires and
frees the slot again.

Double booking is prevented by taking a write lock on the broker's row before the calendar is
read, so two tenants booking the same broker are checked one after the other (a row lock on
Postgres; the database write lock on SQLite).
"""

import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth import hash_key
from app.config import get_settings
from app.db.models import Listing, ListingStatus, Role, User, Visit, VisitStatus
from app.domain import visit_rules as rules
from app.domain.visit_rules import Busy, Collected, SlotRefused
from app.services import escrow_service, listing_service


class NotBookable(Exception):
    """The listing isn't LIVE and verified, so nobody can book a visit to it."""

    def __init__(self, message: str, gone: bool = False):
        super().__init__(message)
        self.gone = gone


class VisitConflict(Exception):
    """The visit is not in a state that allows this (expired, already booked, released)."""


@dataclass(frozen=True)
class Slot:
    starts_at: datetime
    ends_at: datetime
    fee: Decimal
    currency: str


def bookable(session: Session, listing_id: str, now: datetime) -> tuple[Listing, dict]:
    """The listing and its public (signed) view, or NotBookable."""
    listing = session.get(Listing, listing_id)
    if listing is None:
        raise NotBookable("Listing not found")
    if listing_service.expire_if_stale(session, listing, now):
        session.commit()
    if listing.status in (ListingStatus.EXPIRED, ListingStatus.RENTED):
        raise NotBookable(f"This listing is {listing.status.value.lower()}", gone=True)
    view = listing_service.public_view(session, listing)
    if view is None:
        raise NotBookable("This listing is not verified")
    return listing, view


def fee_for(now: datetime, starts_at: datetime) -> Decimal:
    s = get_settings()
    return rules.visit_fee(now, starts_at, s.visit_fee_base, s.visit_fee_early_discount_pct, s.visit_fee_late_surcharge_pct)


def _busy(session: Session, broker_id: str, now: datetime) -> list[Busy]:
    rows = session.scalars(
        select(Visit).where(
            Visit.broker_id == broker_id,
            Visit.status.in_(list(rules.BLOCKING)),
            Visit.ends_at > now - timedelta(days=1),
        )
    )
    return [Busy(v.listing_id, v.starts_at, v.ends_at) for v in rows if rules.is_active(v.status, v.expires_at, now)]


def _buffer() -> timedelta:
    return timedelta(minutes=get_settings().visit_transit_buffer_minutes)


def available_slots(session: Session, listing: Listing, now: datetime) -> list[Slot]:
    s = get_settings()
    busy = _busy(session, listing.broker_id, now)
    currency = s.invoice_currency
    return [
        Slot(start, end, fee_for(now, start), currency)
        for start, end in rules.candidate_slots(now, s.visit_window_days, rules.parse_hours(s.visit_hours), s.visit_utc_offset_minutes)
        if rules.clash(listing.id, start, end, busy, _buffer()) is None
    ]


def hold(
    session: Session,
    listing: Listing,
    *,
    tenant_name: str,
    tenant_email: str,
    starts_at: datetime,
    ends_at: datetime,
    collected: Collected | None,
    now: datetime,
) -> tuple[Visit, str]:
    """Keep the slot for visit_soft_hold_minutes. Returns the visit and its one-time hold token."""
    s = get_settings()
    rules.check_window(now, starts_at, ends_at, s.visit_window_days)
    # The lock: a no-op write to the broker's row, so concurrent bookings for this broker queue here.
    session.execute(update(User).where(User.id == listing.broker_id).values(id=User.id))
    reason = rules.clash(listing.id, starts_at, ends_at, _busy(session, listing.broker_id, now), _buffer())
    if reason:
        session.rollback()
        raise SlotRefused(reason)
    token = secrets.token_urlsafe(24)
    visit = Visit(
        listing_id=listing.id,
        broker_id=listing.broker_id,
        tenant_name=tenant_name,
        tenant_email=tenant_email,
        starts_at=starts_at,
        ends_at=ends_at,
        status=VisitStatus.SOFT_HOLD,
        expires_at=now + timedelta(minutes=s.visit_soft_hold_minutes),
        fee=fee_for(now, starts_at),
        currency=s.invoice_currency,
        hold_token_hash=hash_key(token),
        qualification_json=collected.model_dump_json(exclude_none=True) if collected else None,
        created_at=now,
    )
    session.add(visit)
    session.commit()
    return visit, token


def for_tenant(session: Session, visit_id: str, token: str) -> Visit | None:
    """The visit, if this is its hold token. A wrong token looks exactly like a missing visit."""
    visit = session.get(Visit, visit_id)
    if visit is None or not hmac.compare_digest(visit.hold_token_hash, hash_key(token)):
        return None
    return visit


def _move(visit: Visit, target: VisitStatus, now: datetime) -> None:
    current = rules.effective_status(visit.status, visit.expires_at, now)
    try:
        visit.status = rules.transition(current, target)
    except rules.IllegalTransition as e:
        visit.status = current  # write EXPIRED if that is what it really is
        raise VisitConflict(str(e)) from e


def release(session: Session, visit: Visit, now: datetime) -> Visit:
    try:
        _move(visit, VisitStatus.RELEASED, now)
    finally:
        session.commit()
    return visit


def sweep_expired(session: Session, now: datetime | None = None) -> int:
    """Write EXPIRED on soft holds past their time. Idempotent; the worker runs it every poll."""
    now = now or datetime.now(UTC)
    result = session.execute(
        update(Visit).where(Visit.status == VisitStatus.SOFT_HOLD, Visit.expires_at <= now).values(status=VisitStatus.EXPIRED)
    )
    session.commit()
    return result.rowcount or 0


def calendar(session: Session, user: User, now: datetime, days_back: int = 1) -> list[dict]:
    """The visit calendar from yesterday to the end of the booking window: a broker sees their
    own visits, a manager everyone's. Each row carries its effective status."""
    s = get_settings()
    query = select(Visit).where(
        Visit.starts_at >= now - timedelta(days=days_back),
        Visit.starts_at <= now + timedelta(days=s.visit_window_days + 1),
    )
    if user.role is Role.BROKER:
        query = query.where(Visit.broker_id == user.id)
    visits = session.scalars(query.order_by(Visit.starts_at)).all()
    brokers = {u.id: u for u in session.scalars(select(User).where(User.id.in_({v.broker_id for v in visits})))}
    titles = {}
    for listing_id in {v.listing_id for v in visits}:
        found = listing_service.latest_audit(session, session.get(Listing, listing_id))
        titles[listing_id] = found[0].title if found else listing_id
    return [
        {
            "id": v.id,
            "listing_id": v.listing_id,
            "listing_title": titles[v.listing_id],
            "broker_id": v.broker_id,
            "broker_name": brokers[v.broker_id].name,
            "tenant_name": v.tenant_name,
            "tenant_email": v.tenant_email,
            "starts_at": v.starts_at,
            "ends_at": v.ends_at,
            "status": rules.effective_status(v.status, v.expires_at, now),
            "expires_at": v.expires_at,
            "fee": v.fee,
            "currency": v.currency,
            "authorization_ref": v.authorization_ref,
            "capture_ref": v.capture_ref,
            "qualification": json.loads(v.qualification_json) if v.qualification_json else None,
            "outcome": v.outcome,
            "outcome_reason": v.outcome_reason,
            "proof": escrow_service.summary(session, v, now) if v.authorized_at else None,
        }
        for v in visits
    ]
