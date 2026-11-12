"""Feature 3's check-in referee: what happens to a booked visit's PayPal hold. Pure: no database,
no network, no LLM. It reads only stored proof (append-only check-in rows) and the clock.

The four ways a visit ends (Solution Overview §6.1), plus a safe default:

  A  PASSED          tenant was there (PIN, or tenant GPS) and passes, or nobody locks by the deadline → void
  B  LOCKED          tenant was there, tapped Lock, and the broker approved                         → capture
  C  TENANT_NO_SHOW  broker's own GPS at the flat, no tenant proof, after the grace period            → capture
  D  BROKER_NO_SHOW  tenant's GPS + live photo at the flat, no broker proof or PIN, after the grace    → void
  -  NO_PROOF        nothing proved by the settle deadline                                            → void

A correct PIN proves both people were at the door, so after it neither no-show claim can win.
Taps (Pass, Lock, claims) are requests: the referee decides, and only from proof.
"""

import hashlib
import hmac
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Party(StrEnum):
    TENANT = "TENANT"
    BROKER = "BROKER"


class Proof(StrEnum):
    PIN_OK = "PIN_OK"  # broker typed the tenant's PIN correctly
    PIN_BAD = "PIN_BAD"
    GPS = "GPS"
    PHOTO = "PHOTO"  # tenant's live photo at the flat (outcome D)
    PASS = "PASS"  # tenant: not for me
    LOCK = "LOCK"  # tenant: I want this flat
    APPROVE_LOCK = "APPROVE_LOCK"  # broker agrees to the lock
    CLAIM_NO_SHOW = "CLAIM_NO_SHOW"  # broker: the tenant never came
    BROKER_ABSENT = "BROKER_ABSENT"  # tenant: the broker never came


class Outcome(StrEnum):
    PASSED = "PASSED"
    LOCKED = "LOCKED"
    TENANT_NO_SHOW = "TENANT_NO_SHOW"
    BROKER_NO_SHOW = "BROKER_NO_SHOW"
    NO_PROOF = "NO_PROOF"
    TOO_LATE = "TOO_LATE"  # not the referee's: PayPal approved after the soft hold ran out → void


CAPTURES = {Outcome.LOCKED, Outcome.TENANT_NO_SHOW}  # every other outcome voids


@dataclass(frozen=True)
class Rules:
    radius_m: float = 150
    max_accuracy_m: float = 100
    slack: timedelta = timedelta(minutes=30)  # proof counts from start - slack to end + slack
    grace: timedelta = timedelta(minutes=30)  # a no-show can be claimed from start + grace
    settle_after_end: timedelta = timedelta(hours=12)
    max_after_authorization: timedelta = timedelta(hours=70)  # inside PayPal's ~3-day honor period
    pin_attempts: int = 5


@dataclass(frozen=True)
class Evidence:
    id: str
    party: Party
    kind: Proof
    at: datetime
    distance_m: float | None = None
    accuracy_m: float | None = None


@dataclass(frozen=True)
class VisitWindow:
    starts_at: datetime
    ends_at: datetime
    authorized_at: datetime


@dataclass(frozen=True)
class Decision:
    outcome: Outcome | None  # None: not decided yet, wait for more proof or the deadline
    reason: str
    cites: tuple[str, ...] = ()

    @property
    def captures(self) -> bool:
        return self.outcome in CAPTURES


def settle_deadline(v: VisitWindow, rules: Rules) -> datetime:
    return min(v.ends_at + rules.settle_after_end, v.authorized_at + rules.max_after_authorization)


def in_window(v: VisitWindow, at: datetime, rules: Rules) -> bool:
    return v.starts_at - rules.slack <= at <= v.ends_at + rules.slack


def counts(e: Evidence, v: VisitWindow, rules: Rules) -> bool:
    """Is this row proof of presence? GPS must be close, accurate enough and on time."""
    if e.kind is Proof.PIN_OK:
        return in_window(v, e.at, rules)
    if e.kind in (Proof.GPS, Proof.PHOTO):
        return (
            in_window(v, e.at, rules)
            and e.distance_m is not None and e.distance_m <= rules.radius_m
            and (e.accuracy_m is None or e.accuracy_m <= rules.max_accuracy_m)
        )
    return False


def decide(v: VisitWindow, evidence: Iterable[Evidence], now: datetime, rules: Rules = Rules()) -> Decision:
    rows = sorted(evidence, key=lambda e: e.at)
    proof = [e for e in rows if counts(e, v, rules)]
    pin = [e for e in proof if e.kind is Proof.PIN_OK]
    tenant_gps = [e for e in proof if e.party is Party.TENANT and e.kind is Proof.GPS]
    tenant_photo = [e for e in proof if e.party is Party.TENANT and e.kind is Proof.PHOTO]
    broker_gps = [e for e in proof if e.party is Party.BROKER and e.kind is Proof.GPS]

    def last(party: Party, *kinds: Proof) -> Evidence | None:
        found = [e for e in rows if e.party is party and e.kind in kinds]
        return found[-1] if found else None

    def ids(*groups) -> tuple[str, ...]:
        return tuple(e.id for g in groups for e in (g if isinstance(g, list) else [g]) if e)

    deadline = settle_deadline(v, rules)
    after_grace = now >= v.starts_at + rules.grace
    tenant_here = bool(pin or tenant_gps)
    choice = last(Party.TENANT, Proof.PASS, Proof.LOCK)

    if tenant_here:
        presence = pin or tenant_gps
        if choice and choice.kind is Proof.LOCK:
            approve = last(Party.BROKER, Proof.APPROVE_LOCK)
            if approve and approve.at >= choice.at:
                return Decision(Outcome.LOCKED, "The tenant visited, chose to lock the flat, and the broker approved.",
                                ids(presence[:1], choice, approve))
        if choice and choice.kind is Proof.PASS:
            return Decision(Outcome.PASSED, "The tenant visited and passed on the flat; the hold is released.",
                            ids(presence[:1], choice))
        absent = last(Party.TENANT, Proof.BROKER_ABSENT)
        if absent and not pin and not broker_gps and tenant_gps and tenant_photo and after_grace:
            return Decision(Outcome.BROKER_NO_SHOW,
                            "The tenant was at the flat with a live photo, and the broker never checked in; the hold is released.",
                            ids(tenant_gps[:1], tenant_photo[:1], absent))
        if now >= deadline:
            return Decision(Outcome.PASSED, "The tenant visited and nobody locked the flat in time; the hold is released.",
                            ids(presence[:1]))
        if absent and (pin or broker_gps):
            return Decision(None, "The broker checked in (PIN or GPS), so a broker no-show can't be claimed.")
        if choice and choice.kind is Proof.LOCK:
            return Decision(None, "The tenant chose to lock the flat; waiting for the broker to approve.")
        return Decision(None, "The tenant checked in; waiting for Pass, or Lock and the broker's approval.")

    claim = last(Party.BROKER, Proof.CLAIM_NO_SHOW)
    # The broker must already be proved at the flat when claiming: a claim from home stays void
    # even if they turn up later, and the claim itself must come after the grace period.
    there_first = [e for e in broker_gps if claim and e.at <= claim.at]
    if claim and there_first and claim.at >= v.starts_at + rules.grace:
        return Decision(Outcome.TENANT_NO_SHOW,
                        "The broker was at the flat and the tenant never checked in after the grace period; the fee is captured.",
                        ids(there_first[:1], claim))
    if now >= deadline:
        return Decision(Outcome.NO_PROOF, "Nobody proved anything by the deadline; the hold goes back to the tenant.")
    if claim and not there_first:
        return Decision(None, "A no-show needs the broker's own GPS at the flat during the slot, before claiming it.")
    if claim:
        return Decision(None, f"A no-show can only be claimed {int(rules.grace.total_seconds() // 60)} minutes after the start.")
    return Decision(None, "Waiting for a check-in at the flat.")


# --- PIN ---


def new_pin() -> str:
    return f"{secrets.randbelow(10_000):04d}"


def pin_hash(secret: str, visit_id: str, pin: str) -> str:
    """Keyed and salted by the visit, so the stored value is useless without the server secret."""
    return hmac.new(secret.encode(), f"{visit_id}:{pin}".encode(), hashlib.sha256).hexdigest()


def pin_matches(secret: str, visit_id: str, pin: str, stored: str) -> bool:
    return hmac.compare_digest(pin_hash(secret, visit_id, pin.strip()), stored)
