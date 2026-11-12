"""Feature 3's referee: every row of the anti-cheat matrix, and the edges that would let one side cheat."""

from datetime import UTC, datetime, timedelta
from itertools import count

import pytest

from app.domain import referee as rf
from app.domain.referee import Decision, Evidence, Outcome, Party, Proof, Rules, VisitWindow

START = datetime(2026, 11, 13, 5, 30, tzinfo=UTC)  # 11:00 IST
END = START + timedelta(hours=1)
V = VisitWindow(starts_at=START, ends_at=END, authorized_at=START - timedelta(hours=20))
M = timedelta(minutes=1)
T, B = Party.TENANT, Party.BROKER
_ids = count()


def ev(party, kind, minutes=0, distance=20.0, accuracy=15.0) -> Evidence:
    """A proof row `minutes` after the slot starts (negative = before)."""
    return Evidence(f"e{next(_ids)}", party, kind, START + minutes * M, distance, accuracy)


def gps(party, minutes=0, distance=20.0, accuracy=15.0):
    return ev(party, Proof.GPS, minutes, distance, accuracy)


def pin(minutes=2):
    return ev(B, Proof.PIN_OK, minutes, None, None)


def at(minutes) -> datetime:
    return START + minutes * M


def decide(rows, now_minutes=45) -> Decision:
    return rf.decide(V, rows, at(now_minutes))


# --- A: tenant visited and passes ---


def test_pin_then_pass_voids():
    d = decide([pin(), ev(T, Proof.PASS, 20)])
    assert d.outcome is Outcome.PASSED and not d.captures and len(d.cites) == 2


def test_tenant_gps_then_pass_voids_even_without_the_pin():
    assert decide([gps(T, 1), ev(T, Proof.PASS, 10)]).outcome is Outcome.PASSED


def test_visited_and_nobody_locks_by_the_deadline_voids():
    assert decide([pin()], now_minutes=60 + 12 * 60).outcome is Outcome.PASSED


def test_visited_but_undecided_waits():
    d = decide([pin()])
    assert d.outcome is None and "waiting for Pass" in d.reason


# --- B: tenant wants the flat ---


def test_lock_plus_broker_approval_captures():
    d = decide([pin(), ev(T, Proof.LOCK, 30), ev(B, Proof.APPROVE_LOCK, 31)])
    assert d.outcome is Outcome.LOCKED and d.captures


def test_lock_without_approval_waits():
    assert decide([pin(), ev(T, Proof.LOCK, 30)]).outcome is None


def test_an_approval_given_before_the_lock_does_not_count():
    assert decide([pin(), ev(B, Proof.APPROVE_LOCK, 10), ev(T, Proof.LOCK, 30)]).outcome is None


def test_broker_cannot_lock_without_the_tenant_there():
    assert decide([gps(B), ev(T, Proof.LOCK, 30), ev(B, Proof.APPROVE_LOCK, 31)]).outcome is None


def test_the_tenants_last_choice_wins():
    rows = [pin(), ev(T, Proof.LOCK, 20), ev(T, Proof.PASS, 25), ev(B, Proof.APPROVE_LOCK, 30)]
    assert decide(rows).outcome is Outcome.PASSED


def test_lock_never_approved_falls_back_to_a_void_at_the_deadline():
    assert decide([pin(), ev(T, Proof.LOCK, 20)], now_minutes=60 + 12 * 60).outcome is Outcome.PASSED


# --- C: tenant didn't show ---


def test_broker_at_the_flat_claims_after_the_grace_and_captures():
    d = decide([gps(B, 0), ev(B, Proof.CLAIM_NO_SHOW, 31)], now_minutes=31)
    assert d.outcome is Outcome.TENANT_NO_SHOW and d.captures


def test_no_show_cannot_be_claimed_inside_the_grace_period():
    d = decide([gps(B, 0), ev(B, Proof.CLAIM_NO_SHOW, 29)], now_minutes=29)
    assert d.outcome is None and "30 minutes" in d.reason


def test_no_show_claimed_from_home_is_refused():
    d = decide([gps(B, 0, distance=4000), ev(B, Proof.CLAIM_NO_SHOW, 40)])
    assert d.outcome is None and "own GPS" in d.reason


def test_a_claim_from_home_is_not_rescued_by_arriving_later():
    rows = [gps(B, 0, distance=4000), ev(B, Proof.CLAIM_NO_SHOW, 31), gps(B, 40)]
    assert decide(rows, now_minutes=41).outcome is None
    assert decide(rows + [ev(B, Proof.CLAIM_NO_SHOW, 41)], now_minutes=41).outcome is Outcome.TENANT_NO_SHOW


def test_a_claim_inside_the_grace_period_stays_void_after_it():
    rows = [gps(B, 0), ev(B, Proof.CLAIM_NO_SHOW, 20)]
    assert decide(rows, now_minutes=40).outcome is None


def test_broker_gps_the_day_before_does_not_count():
    assert decide([gps(B, -24 * 60), ev(B, Proof.CLAIM_NO_SHOW, 40)]).outcome is None


def test_a_correct_pin_makes_a_no_show_impossible():
    d = decide([pin(5), gps(B, 0), ev(B, Proof.CLAIM_NO_SHOW, 40)])
    assert d.outcome is None  # the tenant was provably there


def test_tenant_gps_at_the_flat_also_blocks_a_no_show():
    assert decide([gps(T, 3), gps(B, 0), ev(B, Proof.CLAIM_NO_SHOW, 40)]).outcome is None


def test_inaccurate_gps_does_not_count():
    assert decide([gps(B, 0, accuracy=500), ev(B, Proof.CLAIM_NO_SHOW, 40)]).outcome is None


# --- D: broker didn't show ---


def test_tenant_gps_photo_and_no_broker_voids():
    rows = [gps(T, 1), ev(T, Proof.PHOTO, 2), ev(T, Proof.BROKER_ABSENT, 35)]
    d = decide(rows, now_minutes=35)
    assert d.outcome is Outcome.BROKER_NO_SHOW and not d.captures


def test_broker_no_show_needs_the_photo():
    assert decide([gps(T, 1), ev(T, Proof.BROKER_ABSENT, 35)], now_minutes=35).outcome is None


def test_broker_no_show_needs_the_grace_period():
    rows = [gps(T, 1), ev(T, Proof.PHOTO, 2), ev(T, Proof.BROKER_ABSENT, 10)]
    assert decide(rows, now_minutes=10).outcome is None


def test_a_photo_taken_far_away_does_not_count():
    rows = [gps(T, 1), ev(T, Proof.PHOTO, 2, distance=900), ev(T, Proof.BROKER_ABSENT, 35)]
    assert decide(rows, now_minutes=35).outcome is None


def test_broker_who_checked_in_cannot_be_called_absent():
    rows = [gps(T, 1), ev(T, Proof.PHOTO, 2), gps(B, 5), ev(T, Proof.BROKER_ABSENT, 35)]
    d = decide(rows, now_minutes=35)
    assert d.outcome is None and "can't be claimed" in d.reason


def test_after_a_pin_the_tenant_cannot_call_the_broker_absent():
    rows = [gps(T, 1), ev(T, Proof.PHOTO, 2), pin(3), ev(T, Proof.BROKER_ABSENT, 35)]
    assert decide(rows, now_minutes=35).outcome is None


# --- nobody proved anything ---


def test_no_proof_by_the_deadline_voids():
    d = decide([], now_minutes=60 + 12 * 60)
    assert d.outcome is Outcome.NO_PROOF and not d.captures


def test_nothing_yet_waits():
    assert decide([], now_minutes=10).outcome is None


def test_both_claim_no_show_and_nothing_else_is_proved():
    rows = [ev(B, Proof.CLAIM_NO_SHOW, 40), ev(T, Proof.BROKER_ABSENT, 40)]
    assert decide(rows).outcome is None
    assert decide(rows, now_minutes=60 + 12 * 60).outcome is Outcome.NO_PROOF


# --- window and deadline ---


@pytest.mark.parametrize("minutes, counted", [(-31, False), (-30, True), (89, True), (90, True), (91, False)])
def test_proof_counts_30_minutes_either_side_of_the_slot(minutes, counted):
    assert rf.counts(gps(T, minutes), V, Rules()) is counted


def test_gps_radius_is_150_metres():
    assert rf.counts(gps(T, 0, distance=150), V, Rules())
    assert not rf.counts(gps(T, 0, distance=151), V, Rules())


def test_a_pin_after_the_window_is_not_proof():
    assert not rf.counts(pin(200), V, Rules())


def test_the_deadline_stays_inside_the_honor_period():
    late = VisitWindow(starts_at=START, ends_at=END, authorized_at=END - timedelta(hours=65))
    assert rf.settle_deadline(late, Rules()) == late.authorized_at + timedelta(hours=70)
    assert rf.settle_deadline(V, Rules()) == END + timedelta(hours=12)


# --- PIN ---


def test_pins_are_four_digits_and_stored_only_as_a_keyed_hash():
    pins = {rf.new_pin() for _ in range(200)}
    assert all(len(p) == 4 and p.isdigit() for p in pins) and len(pins) > 100
    stored = rf.pin_hash("secret", "visit-1", "0042")
    assert "0042" not in stored and len(stored) == 64
    assert rf.pin_matches("secret", "visit-1", " 0042 ", stored)
    assert not rf.pin_matches("secret", "visit-2", "0042", stored)  # salted by the visit
    assert not rf.pin_matches("other", "visit-1", "0042", stored)
