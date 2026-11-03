import pytest

from app.domain.check_result import Check, CheckResult, Verdict
from app.domain.decision import Decision, decide

ALL = [Check.PHOTOS, Check.COMMUTE, Check.COMPLIANCE, Check.GROUND_TRUTH]


def _result(check, verdict):
    return CheckResult(check=check, verdict=verdict, source="test")


def _all(**overrides):
    return [_result(c, overrides.get(c.value, Verdict.PASS)) for c in ALL]


def test_all_pass_is_verified():
    assert decide(_all(), ALL) is Decision.VERIFIED


def test_any_fail_needs_fix():
    assert decide(_all(commute=Verdict.FAIL), ALL) is Decision.NEEDS_FIX


def test_any_unverifiable_goes_to_review():
    assert decide(_all(photos=Verdict.UNVERIFIABLE), ALL) is Decision.IN_REVIEW


def test_fail_beats_unverifiable():
    # The broker has to fix the lie anyway; a manager reviewing it first would waste their time.
    results = _all(commute=Verdict.UNVERIFIABLE, ground_truth=Verdict.FAIL)

    assert decide(results, ALL) is Decision.NEEDS_FIX


def test_missing_required_check_is_never_verified():
    results = [_result(Check.GROUND_TRUTH, Verdict.PASS)]

    assert decide(results, ALL) is Decision.IN_REVIEW


def test_only_required_checks_count():
    # Phase 2 runs Check D alone; a stray result from an unrequired check must not change the outcome.
    results = [_result(Check.GROUND_TRUTH, Verdict.PASS), _result(Check.PHOTOS, Verdict.FAIL)]

    assert decide(results, [Check.GROUND_TRUTH]) is Decision.VERIFIED


def test_a_duplicate_pass_cannot_hide_a_fail():
    results = [_result(Check.GROUND_TRUTH, Verdict.FAIL), _result(Check.GROUND_TRUTH, Verdict.PASS)]

    assert decide(results, [Check.GROUND_TRUTH]) is Decision.NEEDS_FIX


def test_nothing_required_is_an_error():
    with pytest.raises(ValueError):
        decide([], [])
