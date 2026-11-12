"""Feature 2's rules for booking a site visit. Pure: no database, no network.

- A visit is booked at most `window_days` ahead: PayPal honours an authorization for 3 days.
- A slot is held for a few minutes (SOFT_HOLD) while the tenant authorises the fee, then it is
  COMMITTED, or it EXPIRES, or the tenant RELEASES it. Nothing else changes a visit's status.
- A broker cannot be at two flats at once: a visit at a different flat needs a transit gap.
- The visit fee is dearer the closer the visit: early = lower fee, last-minute = higher fee.
- The booking assistant's "ready to book" is only advice; these rules decide.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from pydantic import AwareDatetime, BaseModel, Field, field_validator

from app.db.models import VisitStatus

CENT = Decimal("0.01")
SLOT_LENGTH = timedelta(hours=1)


# --- Fee ---


def visit_fee(now: datetime, starts_at: datetime, base: Decimal, early_discount_pct: int, late_surcharge_pct: int) -> Decimal:
    """48 h or more ahead: base minus the discount. Under 12 h: base plus the surcharge. Else base."""
    hours_ahead = (starts_at - now) / timedelta(hours=1)
    if hours_ahead >= 48:
        fee = base * (100 - early_discount_pct) / 100
    elif hours_ahead < 12:
        fee = base * (100 + late_surcharge_pct) / 100
    else:
        fee = base
    return max(Decimal(fee).quantize(CENT, ROUND_HALF_UP), CENT)


# --- Status ---


class IllegalTransition(Exception):
    pass


TRANSITIONS: dict[VisitStatus, frozenset[VisitStatus]] = {
    VisitStatus.SOFT_HOLD: frozenset({VisitStatus.COMMITTED, VisitStatus.EXPIRED, VisitStatus.RELEASED}),
    # Feature 3: the referee settles a booked visit once; the outbox then captures or voids it.
    VisitStatus.COMMITTED: frozenset({VisitStatus.SETTLING}),
    VisitStatus.SETTLING: frozenset({VisitStatus.CAPTURED, VisitStatus.VOIDED}),
    VisitStatus.EXPIRED: frozenset(),
    VisitStatus.RELEASED: frozenset(),
    VisitStatus.CAPTURED: frozenset(),
    VisitStatus.VOIDED: frozenset(),
}

# Statuses that still occupy the broker's calendar (a voided visit frees its slot).
BLOCKING = frozenset({VisitStatus.SOFT_HOLD, VisitStatus.COMMITTED, VisitStatus.SETTLING, VisitStatus.CAPTURED})


def transition(current: VisitStatus, target: VisitStatus) -> VisitStatus:
    """The new status, or IllegalTransition. Moving to the status it already has is a no-op."""
    if current is target:
        return target
    if target not in TRANSITIONS[current]:
        raise IllegalTransition(f"A visit that is {current.value} cannot become {target.value}")
    return target


def effective_status(status: VisitStatus, expires_at: datetime, now: datetime) -> VisitStatus:
    """A soft hold past its expiry is expired, whether or not the sweep has written that yet."""
    if status is VisitStatus.SOFT_HOLD and expires_at <= now:
        return VisitStatus.EXPIRED
    return status


def is_active(status: VisitStatus, expires_at: datetime, now: datetime) -> bool:
    """Does this visit still occupy the broker's calendar?"""
    return effective_status(status, expires_at, now) in BLOCKING


# --- Calendar ---


class SlotRefused(Exception):
    """The requested slot breaks a booking rule or clashes with the broker's calendar."""


@dataclass(frozen=True)
class Busy:
    """An active visit on the broker's calendar."""

    listing_id: str
    starts_at: datetime
    ends_at: datetime


def clash(listing_id: str, starts_at: datetime, ends_at: datetime, busy: Iterable[Busy], transit_buffer: timedelta) -> str | None:
    """Why the slot can't be booked, or None. Same flat: no overlap. Another flat: the broker
    also needs `transit_buffer` before and after to get there."""
    for b in busy:
        if b.listing_id == listing_id:
            if starts_at < b.ends_at and ends_at > b.starts_at:
                return "That slot is already held or booked for this flat"
        elif starts_at < b.ends_at + transit_buffer and ends_at > b.starts_at - transit_buffer:
            minutes = int(transit_buffer.total_seconds() // 60)
            return f"The broker has a visit at another flat then and needs {minutes} minutes to travel between flats"
    return None


def check_window(now: datetime, starts_at: datetime, ends_at: datetime, window_days: int) -> None:
    if ends_at <= starts_at:
        raise SlotRefused("A visit must end after it starts")
    if starts_at <= now:
        raise SlotRefused("That slot has already started")
    if ends_at > now + timedelta(days=window_days):
        raise SlotRefused(f"Visits can only be booked within the next {window_days} days")


def parse_hours(spec: str) -> tuple[int, int]:
    """"9-18" -> (9, 18): the first slot starts at 09:00 and the last one ends at 18:00."""
    first, _, last = spec.partition("-")
    open_h, close_h = int(first), int(last)
    if not 0 <= open_h < close_h <= 24:
        raise ValueError(f"visit hours must look like 9-18, not {spec!r}")
    return open_h, close_h


def candidate_slots(now: datetime, window_days: int, hours: tuple[int, int], utc_offset_minutes: int) -> list[tuple[datetime, datetime]]:
    """Every 1-hour slot inside visiting hours (local time) that starts after now and ends
    within the window, in UTC."""
    tz = timezone(timedelta(minutes=utc_offset_minutes))
    local_today = now.astimezone(tz).date()
    horizon = now + timedelta(days=window_days)
    out = []
    for day in range(window_days + 1):
        d = local_today + timedelta(days=day)
        for hour in range(hours[0], hours[1]):
            start = datetime(d.year, d.month, d.day, hour, tzinfo=tz).astimezone(UTC)
            end = start + SLOT_LENGTH
            if start > now and end <= horizon:
                out.append((start, end))
    return out


# --- Booking assistant ---


class Collected(BaseModel):
    """What the tenant has told the booking assistant so far."""

    budget: Decimal | None = Field(None, gt=0, description="monthly rent the tenant can pay, in rupees")
    move_in_date: date | None = None
    profile: str | None = Field(None, max_length=60)  # family, bachelor, couple, students, ...
    preferred_slot: AwareDatetime | None = None

    @field_validator("profile", mode="before")
    @classmethod
    def _blank_is_none(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    def merged(self, newer: "Collected") -> "Collected":
        """Answers from earlier turns stay unless the tenant gives a new one."""
        return Collected(**{k: v if v is not None else getattr(self, k) for k, v in newer})


class Qualification(BaseModel):
    answer: str = Field(max_length=2000)
    collected: Collected = Collected()
    next_question: str | None = Field(None, max_length=500)
    ready_to_book: bool = False
    problems: list[str] = []  # why ready_to_book is false although the assistant said true


def qualification_problems(c: Collected, rent: Decimal, now: datetime, window_days: int) -> list[str]:
    """What still stands between this tenant and a visit. Empty means they may book."""
    problems = []
    if c.budget is None:
        problems.append("budget not given")
    elif c.budget < rent:
        problems.append(f"budget ₹{c.budget:,.0f} is below the verified rent ₹{rent:,.0f}")
    if c.move_in_date is None:
        problems.append("move-in date not given")
    elif c.move_in_date < now.date():
        problems.append("move-in date is in the past")
    if not c.profile:
        problems.append("tenant profile not given")
    if c.preferred_slot is None:
        problems.append("preferred visit time not given")
    elif not now < c.preferred_slot <= now + timedelta(days=window_days):
        problems.append(f"preferred visit time is not within the next {window_days} days")
    return problems


def enforce(q: Qualification, rent: Decimal, now: datetime, window_days: int) -> Qualification:
    """The assistant may only say "ready" when our own rules agree; it can never override them."""
    problems = qualification_problems(q.collected, rent, now, window_days)
    return q.model_copy(update={"ready_to_book": q.ready_to_book and not problems, "problems": problems})
