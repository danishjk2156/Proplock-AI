"""Feature 3: the visit fee as a real PayPal authorization, refereed at the door.

  open_order()   the tenant clicks PayPal's button: an Orders v2 intent=AUTHORIZE order
  authorize()    after the tenant approves it: PayPal authorises (holds) the fee; the visit is
                 COMMITTED, HOLD_AUTHORIZED goes to the ledger, and the door PIN exists
  record()       one piece of proof (PIN, GPS, photo, a tap) is appended; the referee runs
  settle()       the referee decided: the visit is SETTLING and one outbox job captures or voids
                 the hold (escrow_jobs), so PayPal is only called after the decision is saved

The PIN is never stored. It is derived from the tenant's hold token, so only the tenant (who
holds the token) can see it, and the server keeps just an HMAC to check what the broker types.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import images
from app.adapters.paypal_orders import OrderRequest, Orders
from app.config import get_settings
from app.db.models import (
    LedgerSource,
    Listing,
    ListingStatus,
    Property,
    Visit,
    VisitCheckin,
    VisitStatus,
)
from app.domain import referee as rf
from app.domain import visit_rules
from app.domain.ledger import EntryKind
from app.domain.referee import Decision, Evidence, Outcome, Party, Proof
from app.services import ledger_service, outbox, trust

CAPTURE_HOLD = "capture_hold"
VOID_HOLD = "void_hold"
BROKER_NO_SHOW = ("broker_no_show", -10)


class EscrowConflict(Exception):
    """The visit isn't in a state that allows this (409)."""


class EscrowRefused(Exception):
    """The proof itself is unusable: wrong PIN, PIN locked, unreadable photo (422)."""


@dataclass(frozen=True)
class Recorded:
    visit: Visit
    decision: Decision
    message: str | None = None


def rules() -> rf.Rules:
    return rf.Rules()


# --- PIN ---


def tenant_pin(visit: Visit, hold_token: str) -> str:
    """The door PIN, derived from the tenant's own hold token (so it is never stored)."""
    digest = hmac.new(get_settings().audit_secret.encode(), f"pin:{visit.id}:{hold_token}".encode(), hashlib.sha256)
    return f"{int(digest.hexdigest(), 16) % 10_000:04d}"


# --- PayPal order and authorization ---


def open_order(session: Session, visit: Visit, orders: Orders, now: datetime) -> str:
    """Create (once) the AUTHORIZE order for this visit's fee. The amount comes from our row."""
    if visit_rules.effective_status(visit.status, visit.expires_at, now) is not VisitStatus.SOFT_HOLD:
        raise EscrowConflict(f"This visit is {visit_rules.effective_status(visit.status, visit.expires_at, now).value}, not held")
    if visit.paypal_order_id:
        return visit.paypal_order_id
    listing = session.get(Listing, visit.listing_id)
    req = OrderRequest(visit.id, Decimal(visit.fee), visit.currency,
                       f"PropLock visit hold, listing {listing.property_id}, {visit.starts_at:%d %b %H:%M} UTC")
    visit.paypal_order_id = orders.create_authorize_order(req, request_id=f"visit:{visit.id}:create")
    session.commit()
    return visit.paypal_order_id


def authorize(session: Session, visit: Visit, hold_token: str, order_id: str | None, orders: Orders, now: datetime) -> Visit:
    """PayPal authorises the fee and the visit is booked. Approved after the hold ran out: the
    authorization is voided straight away (we never hold money for a slot we gave away)."""
    if visit.authorization_ref:
        return visit  # a double click books it once
    if not visit.paypal_order_id:
        raise EscrowConflict("Start the PayPal payment first")
    if order_id and order_id != visit.paypal_order_id:
        raise EscrowConflict("That PayPal order belongs to another visit")
    status = visit_rules.effective_status(visit.status, visit.expires_at, now)
    if status not in (VisitStatus.SOFT_HOLD, VisitStatus.EXPIRED, VisitStatus.RELEASED):
        raise EscrowConflict(f"This visit is {status.value}")
    auth = orders.authorize(visit.paypal_order_id, request_id=f"visit:{visit.id}:authorize")
    visit.authorization_ref = auth.id
    visit.authorized_at = now
    _ledger(session, visit, EntryKind.HOLD_AUTHORIZED, auth.id, now)
    if status is not VisitStatus.SOFT_HOLD:
        visit.status = status  # write EXPIRED if that is what it really is
        visit.outcome = Outcome.TOO_LATE.value
        visit.outcome_reason = "PayPal approved after the 15-minute hold ran out, so the hold was released at once."
        outbox.enqueue(session, VOID_HOLD, visit.id, run_at=now)
        session.commit()
        outbox.try_ref_now(session, visit.id, None, now, orders=orders)
        raise EscrowConflict("The 15-minute hold ran out before PayPal approved; the authorization was released")
    visit.status = visit_rules.transition(VisitStatus.SOFT_HOLD, VisitStatus.COMMITTED)
    visit.committed_at = now
    visit.pin_hash = rf.pin_hash(get_settings().audit_secret, visit.id, tenant_pin(visit, hold_token))
    session.commit()
    return visit


def _ledger(session: Session, visit: Visit, kind: EntryKind, ref: str, now: datetime,
            source: LedgerSource = LedgerSource.FEATURE3, note: str | None = None) -> None:
    """Same idempotency key as Feature 3's ledger contract, so a job and a webhook count once."""
    ledger_service.record(
        session, listing_id=visit.listing_id, kind=kind, amount=visit.fee, currency=visit.currency,
        idempotency_key=f"f3:{kind}:{ref}", source=source, paypal_ref=ref, occurred_at=now, note=note,
        raw={"visit_id": visit.id, "tenant_email": visit.tenant_email},
    )


# --- proof ---


def evidence(session: Session, visit: Visit) -> list[Evidence]:
    rows = session.scalars(select(VisitCheckin).where(VisitCheckin.visit_id == visit.id).order_by(VisitCheckin.created_at))
    return [
        Evidence(r.id, Party(r.party), Proof(r.kind), r.created_at,
                 float(r.distance_m) if r.distance_m is not None else None,
                 float(r.accuracy_m) if r.accuracy_m is not None else None)
        for r in rows
    ]


def window(visit: Visit) -> rf.VisitWindow:
    return rf.VisitWindow(visit.starts_at, visit.ends_at, visit.authorized_at or visit.committed_at or visit.created_at)


def deadline(visit: Visit) -> datetime:
    return rf.settle_deadline(window(visit), rules())


def _distance(session: Session, visit: Visit, lat: Decimal, lon: Decimal) -> Decimal:
    prop = session.get(Property, session.get(Listing, visit.listing_id).property_id)
    return Decimal(str(round(images.haversine_m(lat, lon, prop.latitude, prop.longitude), 1)))


def _open(visit: Visit) -> None:
    if visit.status is not VisitStatus.COMMITTED:
        if visit.status in (VisitStatus.SETTLING, VisitStatus.CAPTURED, VisitStatus.VOIDED):
            raise EscrowConflict(f"This visit is already settled: {visit.outcome_reason or visit.status.value}")
        raise EscrowConflict(f"Only a booked visit takes check-ins; this one is {visit.status.value}")


def record(session: Session, visit: Visit, party: Party, kind: Proof, now: datetime, orders: Orders, *,
           gps: tuple[Decimal, Decimal, Decimal | None] | None = None, photo: tuple[str, str] | None = None) -> Recorded:
    """Append one row of proof, then let the referee decide. Returns the (possibly settled) visit."""
    _open(visit)
    row = VisitCheckin(visit_id=visit.id, party=party.value, kind=kind.value, created_at=now)
    if gps:
        row.latitude, row.longitude, row.accuracy_m = gps
        row.distance_m = _distance(session, visit, gps[0], gps[1])
    if photo:
        row.photo_path, row.photo_phash = photo
    session.add(row)
    session.flush()
    message = None
    if gps and float(row.distance_m) > rules().radius_m:
        message = f"Recorded, but you are {row.distance_m:.0f} m from the flat; proof needs {rules().radius_m:.0f} m or less."
    elif gps and not rf.in_window(window(visit), now, rules()):
        message = "Recorded, but check-ins only count from 30 minutes before the visit to 30 minutes after it."
    return _decide(session, visit, now, orders, message)


def enter_pin(session: Session, visit: Visit, pin: str, now: datetime, orders: Orders) -> Recorded:
    _open(visit)
    left = rules().pin_attempts - visit.pin_attempts
    if left <= 0:
        raise EscrowRefused("Too many wrong PINs; use GPS check-in instead")
    if not rf.in_window(window(visit), now, rules()):
        raise EscrowRefused("The PIN can only be entered from 30 minutes before the visit to 30 minutes after it")
    if not (visit.pin_hash and rf.pin_matches(get_settings().audit_secret, visit.id, pin, visit.pin_hash)):
        visit.pin_attempts += 1
        session.add(VisitCheckin(visit_id=visit.id, party=Party.BROKER.value, kind=Proof.PIN_BAD.value, created_at=now))
        session.commit()
        raise EscrowRefused(f"Wrong PIN; {left - 1} tries left")
    session.add(VisitCheckin(visit_id=visit.id, party=Party.BROKER.value, kind=Proof.PIN_OK.value, created_at=now))
    session.flush()
    return _decide(session, visit, now, orders, "PIN accepted: the visit is checked in.")


def save_photo(data: bytes, upload_dir: Path) -> tuple[str, str, tuple[Decimal, Decimal] | None]:
    try:
        info = images.inspect_upload(data)
    except images.ImageRejected as e:
        raise EscrowRefused(f"photo: {e}") from e
    return images.save(data, info.format, upload_dir / "visits"), info.phash, info.gps


# --- deciding and settling ---


def _decide(session: Session, visit: Visit, now: datetime, orders: Orders, message: str | None = None) -> Recorded:
    decision = rf.decide(window(visit), evidence(session, visit), now, rules())
    if decision.outcome is None:
        session.commit()
        return Recorded(visit, decision, message)
    settle(session, visit, decision, now, orders)
    return Recorded(visit, decision, message)


def settle(session: Session, visit: Visit, decision: Decision, now: datetime, orders: Orders | None) -> None:
    """Save the verdict and queue exactly one PayPal call, in one commit; then try it now."""
    visit.status = visit_rules.transition(visit.status, VisitStatus.SETTLING)
    visit.outcome = decision.outcome.value
    visit.outcome_reason = decision.reason
    outbox.enqueue(session, CAPTURE_HOLD if decision.captures else VOID_HOLD, visit.id, run_at=now)
    if decision.outcome is Outcome.BROKER_NO_SHOW:
        listing = session.get(Listing, visit.listing_id)
        if listing.status is ListingStatus.LIVE:
            listing.status = ListingStatus.EXPIRED  # off the public site until the broker re-confirms it
        trust.record(session, visit.broker_id, BROKER_NO_SHOW)
    session.commit()
    if orders is not None:
        outbox.try_ref_now(session, visit.id, None, now, orders=orders)
        session.refresh(visit)


def settle_now(session: Session, visit: Visit, now: datetime, orders: Orders) -> Recorded:
    """A manager settles a booked visit as if its deadline had passed (demo and operations)."""
    _open(visit)
    return _decide(session, visit, max(now, deadline(visit)), orders)


def settle_due(session: Session, now: datetime | None = None, orders: Orders | None = None) -> int:
    """The worker's sweep: booked visits past their deadline get the referee's default."""
    now = now or datetime.now(UTC)
    due = [v for v in session.scalars(select(Visit).where(Visit.status == VisitStatus.COMMITTED,
                                                          Visit.ends_at < now))
           if deadline(v) <= now]
    for visit in due:
        _decide(session, visit, now, orders)
    return len(due)


def summary(session: Session, visit: Visit, now: datetime) -> dict:
    """What both sides may see about the proof so far (never the PIN)."""
    rows = evidence(session, visit)
    w, r = window(visit), rules()
    good = [e for e in rows if rf.counts(e, w, r)]
    taps = {e.kind for e in rows}
    return {
        "pin_entered": any(e.kind is Proof.PIN_OK for e in good),
        "pin_attempts_left": max(0, r.pin_attempts - visit.pin_attempts),
        "tenant_at_flat": any(e.party is Party.TENANT and e.kind is Proof.GPS for e in good),
        "tenant_photo": any(e.kind is Proof.PHOTO for e in good),
        "broker_at_flat": any(e.party is Party.BROKER and e.kind is Proof.GPS for e in good),
        "tenant_choice": next((e.kind.value for e in reversed(rows) if e.kind in (Proof.PASS, Proof.LOCK)), None),
        "broker_approved_lock": Proof.APPROVE_LOCK in taps,
        "no_show_claimed": Proof.CLAIM_NO_SHOW in taps,
        "broker_absent_claimed": Proof.BROKER_ABSENT in taps,
        "grace_ends_at": visit.starts_at + r.grace,
        "settle_deadline": deadline(visit) if visit.authorized_at else None,
        "waiting_for": rf.decide(w, rows, now, r).reason if visit.status is VisitStatus.COMMITTED else None,
        "log": [{"party": e.party.value, "kind": e.kind.value, "at": e.at,
                 "distance_m": e.distance_m, "counts": rf.counts(e, w, r)} for e in rows],
    }
