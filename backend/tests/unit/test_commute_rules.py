from datetime import UTC, datetime

import pytest

from app.db.models import ClaimSource, CommuteMode
from app.domain.check_result import Check, Verdict
from app.domain.claims import ClaimedCommute
from app.domain.commute_rules import IST, RouteLookup, allowed_minutes, check_commute, next_weekday_peak


def _claim(minutes=5, destination="Indiranagar Metro", source=ClaimSource.FORM):
    return ClaimedCommute(destination=destination, minutes=minutes, mode=CommuteMode.WALKING, source=source)


def _found(minutes):
    return RouteLookup(minutes, "test-maps")


MISSING = RouteLookup(None, "test-maps", "destination not in the demo route table")


@pytest.mark.parametrize(("claimed", "allowed"), [(2, 7), (5, 10), (25, 30), (30, 36), (60, 72)])
def test_tolerance_is_twenty_percent_but_at_least_five_minutes(claimed, allowed):
    assert allowed_minutes(claimed) == allowed


@pytest.mark.parametrize(
    ("actual", "verdict"),
    [(22, Verdict.FAIL), (11, Verdict.FAIL), (10, Verdict.PASS), (7, Verdict.PASS), (3, Verdict.PASS)],
)
def test_five_minute_claim_against_actual_times(actual, verdict):
    result = check_commute([_claim(5)], [_found(actual)], source="test-maps")

    assert result.check is Check.COMMUTE
    assert result.verdict is verdict
    [e] = result.evidence
    assert (e.claimed, e.allowed) == ("5 min", "<= 10 min")
    assert f"Actual {actual} min" in e.note


def test_no_route_data_is_unverifiable_never_a_guess():
    result = check_commute([_claim(5)], [MISSING], source="demo-table")

    assert result.verdict is Verdict.UNVERIFIABLE
    [e] = result.evidence
    assert e.ok is None
    assert "destination not in the demo route table" in e.note
    assert "Actual" not in e.note  # no number is ever made up


def test_one_false_claim_fails_even_if_another_is_unknown():
    claims = [_claim(5), _claim(10, "Bagmane Tech Park")]

    result = check_commute(claims, [_found(22), MISSING], source="test-maps")

    assert result.verdict is Verdict.FAIL
    assert [e.ok for e in result.evidence] == [False, None]


def test_all_claims_true_passes():
    claims = [_claim(5), _claim(20, "Bagmane Tech Park")]

    assert check_commute(claims, [_found(9), _found(24)], source="test-maps").verdict is Verdict.PASS


def test_no_claims_passes():
    result = check_commute([], [], source="test-maps")

    assert (result.verdict, result.summary) == (Verdict.PASS, "No commute claims.")


def test_evidence_says_where_the_claim_came_from():
    result = check_commute([_claim(source=ClaimSource.EXTRACTED)], [_found(22)], source="test-maps")

    assert "Claim from the ad text." in result.evidence[0].note


def test_lookups_must_match_claims():
    with pytest.raises(ValueError):
        check_commute([_claim()], [], source="test-maps")


def _ist(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_ist(2026, 11, 3, 7, 0), _ist(2026, 11, 3, 8, 30)),  # Tuesday before 08:30 -> same morning
        (_ist(2026, 11, 3, 8, 30), _ist(2026, 11, 4, 8, 30)),  # exactly 08:30 is not "in the future"
        (_ist(2026, 11, 3, 9, 0), _ist(2026, 11, 4, 8, 30)),  # Tuesday after -> Wednesday
        (_ist(2026, 11, 6, 10, 0), _ist(2026, 11, 9, 8, 30)),  # Friday after -> Monday
        (_ist(2026, 11, 7, 6, 0), _ist(2026, 11, 9, 8, 30)),  # Saturday -> Monday
    ],
)
def test_next_weekday_peak(now, expected):
    result = next_weekday_peak(now)

    assert result == expected
    assert result.tzinfo is UTC  # sent to the API as UTC
