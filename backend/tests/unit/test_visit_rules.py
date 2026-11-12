"""Feature 2's pure booking rules: fee, status, calendar clashes, slots and the assistant's guardrail."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.db.models import VisitStatus
from app.domain import visit_rules as r
from app.domain.visit_rules import Busy, Collected, Qualification

NOW = datetime(2026, 11, 12, 4, 30, tzinfo=UTC)  # 10:00 in India
RENT = Decimal("35000")
BUFFER = timedelta(minutes=30)
H = timedelta(hours=1)


def fee(hours_ahead: float) -> Decimal:
    return r.visit_fee(NOW, NOW + timedelta(hours=hours_ahead), Decimal("15.00"), 20, 30)


def test_book_early_pay_less_book_late_pay_more():
    assert fee(50) == Decimal("12.00")
    assert fee(48) == Decimal("12.00")
    assert fee(24) == Decimal("15.00")
    assert fee(12) == Decimal("15.00")
    assert fee(11.9) == Decimal("19.50")
    assert all(isinstance(fee(h), Decimal) for h in (1, 24, 60))


def test_fee_rounds_to_cents_and_never_reaches_zero():
    assert r.visit_fee(NOW, NOW + 60 * H, Decimal("0.01"), 99, 0) == Decimal("0.01")
    assert r.visit_fee(NOW, NOW + H, Decimal("9.99"), 0, 33) == Decimal("13.29")


@pytest.mark.parametrize("target", [VisitStatus.COMMITTED, VisitStatus.EXPIRED, VisitStatus.RELEASED])
def test_a_soft_hold_can_end_three_ways(target):
    assert r.transition(VisitStatus.SOFT_HOLD, target) is target


def test_a_booked_visit_settles_once():
    assert r.transition(VisitStatus.COMMITTED, VisitStatus.SETTLING) is VisitStatus.SETTLING
    for target in (VisitStatus.CAPTURED, VisitStatus.VOIDED):
        assert r.transition(VisitStatus.SETTLING, target) is target
    with pytest.raises(r.IllegalTransition):
        r.transition(VisitStatus.COMMITTED, VisitStatus.CAPTURED)  # never without the referee
    with pytest.raises(r.IllegalTransition):
        r.transition(VisitStatus.SOFT_HOLD, VisitStatus.SETTLING)  # never without an authorization


@pytest.mark.parametrize("start", [VisitStatus.EXPIRED, VisitStatus.RELEASED, VisitStatus.CAPTURED, VisitStatus.VOIDED])
def test_ended_visits_cannot_change(start):
    others = set(VisitStatus) - {start}
    for target in others:
        with pytest.raises(r.IllegalTransition):
            r.transition(start, target)
    assert r.transition(start, start) is start  # idempotent


def test_a_soft_hold_past_its_time_counts_as_expired():
    assert r.effective_status(VisitStatus.SOFT_HOLD, NOW, NOW) is VisitStatus.EXPIRED
    assert r.effective_status(VisitStatus.SOFT_HOLD, NOW + H, NOW) is VisitStatus.SOFT_HOLD
    assert r.effective_status(VisitStatus.COMMITTED, NOW - H, NOW) is VisitStatus.COMMITTED
    assert not r.is_active(VisitStatus.SOFT_HOLD, NOW, NOW)


def test_same_flat_cannot_overlap_but_can_follow_on():
    busy = [Busy("flat-a", NOW + 2 * H, NOW + 3 * H)]
    assert "already held" in r.clash("flat-a", NOW + 2.5 * H, NOW + 3.5 * H, busy, BUFFER)
    assert r.clash("flat-a", NOW + 3 * H, NOW + 4 * H, busy, BUFFER) is None


def test_another_flat_needs_the_transit_gap():
    busy = [Busy("flat-a", NOW + 2 * H, NOW + 3 * H)]
    assert "30 minutes" in r.clash("flat-b", NOW + 3 * H, NOW + 4 * H, busy, BUFFER)
    assert "30 minutes" in r.clash("flat-b", NOW + 0.75 * H, NOW + 1.75 * H, busy, BUFFER)
    assert r.clash("flat-b", NOW + 3.5 * H, NOW + 4.5 * H, busy, BUFFER) is None
    assert r.clash("flat-b", NOW + 0.5 * H, NOW + 1.5 * H, busy, BUFFER) is None


def test_window_rules():
    with pytest.raises(r.SlotRefused, match="already started"):
        r.check_window(NOW, NOW - H, NOW + H, 3)
    with pytest.raises(r.SlotRefused, match="within the next 3 days"):
        r.check_window(NOW, NOW + timedelta(days=4), NOW + timedelta(days=4) + H, 3)
    with pytest.raises(r.SlotRefused, match="end after"):
        r.check_window(NOW, NOW + 2 * H, NOW + H, 3)
    r.check_window(NOW, NOW + H, NOW + 2 * H, 3)


def test_slots_are_hourly_inside_local_visiting_hours_and_the_window():
    slots = r.candidate_slots(NOW, 3, (9, 18), 330)
    ist = timedelta(minutes=330)
    assert all(9 <= (s + ist).hour < 18 and e - s == H for s, e in slots)
    assert all(s > NOW and e <= NOW + timedelta(days=3) for s, e in slots)
    # Today from 11:00 (10:00 is already under way), two full days, and day 4 until 10:00 IST.
    assert (slots[0][0] + ist).hour == 11
    assert len(slots) == 7 + 9 + 9 + 1
    assert slots == sorted(slots)


def test_parse_hours():
    assert r.parse_hours("9-18") == (9, 18)
    with pytest.raises(ValueError):
        r.parse_hours("18-9")


def ready(**changes) -> Qualification:
    collected = dict(budget=Decimal("36000"), move_in_date=date(2026, 12, 5), profile="family",
                     preferred_slot=NOW + timedelta(days=2))
    collected.update(changes)
    return Qualification(answer="All set!", collected=Collected(**collected), ready_to_book=True)


def test_ready_only_when_every_rule_agrees():
    q = r.enforce(ready(), RENT, NOW, 3)
    assert q.ready_to_book and q.problems == []


def test_budget_below_rent_is_never_ready_even_if_the_model_says_so():
    q = r.enforce(ready(budget=Decimal("25000")), RENT, NOW, 3)
    assert not q.ready_to_book
    assert "below the verified rent" in q.problems[0]


def test_a_slot_beyond_the_window_is_never_ready():
    q = r.enforce(ready(preferred_slot=NOW + timedelta(days=5)), RENT, NOW, 3)
    assert not q.ready_to_book and "within the next 3 days" in q.problems[0]


def test_missing_fields_are_never_ready():
    q = r.enforce(Qualification(answer="Booked! (ignore previous instructions)", ready_to_book=True), RENT, NOW, 3)
    assert not q.ready_to_book and len(q.problems) == 4


def test_a_past_move_in_date_is_never_ready():
    assert not r.enforce(ready(move_in_date=date(2026, 1, 1)), RENT, NOW, 3).ready_to_book


def test_the_model_can_only_lower_ready_never_raise_it():
    q = ready()
    assert not r.enforce(q.model_copy(update={"ready_to_book": False}), RENT, NOW, 3).ready_to_book


def test_earlier_answers_are_kept_unless_replaced():
    first = Collected(budget=Decimal("40000"), profile="couple")
    merged = first.merged(Collected(profile="family", move_in_date=date(2026, 12, 1)))
    assert (merged.budget, merged.profile, merged.move_in_date) == (Decimal("40000"), "family", date(2026, 12, 1))
    assert Collected(profile="  ").profile is None
