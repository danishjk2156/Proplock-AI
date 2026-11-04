from decimal import Decimal

import pytest

from app.domain.check_result import Check, Verdict
from app.domain.compliance_rules import check_compliance, default_rules, deposit_months, load_rules

RULES = default_rules()


def _check(state, rent, deposit):
    return check_compliance(state, Decimal(rent), Decimal(deposit), RULES)


def test_chennai_six_months_fails_with_a_citation():
    result = _check("Tamil Nadu", "40000", "240000")

    assert result.check is Check.COMPLIANCE
    assert result.verdict is Verdict.FAIL
    [e] = result.evidence
    assert (e.claimed, e.allowed, e.ok) == ("6.00 months (240000.00 deposit / 40000.00 rent)", "<= 3 months", False)
    assert "Landlords and Tenants Act, 2017, s.11(1)" in e.note
    assert "effective 2019-02-22" in e.note
    assert "NOT YET VERIFIED against the official text" in e.note
    assert result.source == f"deposit_caps.yaml@{RULES.version}"


def test_chennai_exactly_three_months_passes():
    assert _check("Tamil Nadu", "40000", "120000").verdict is Verdict.PASS


def test_bengaluru_four_months_passes_with_an_advisory():
    result = _check("Karnataka", "35000", "140000")

    assert result.verdict is Verdict.PASS
    assert "Advisory: above 3 months" in result.summary
    advisory, hard = result.evidence
    assert advisory.note.startswith("ADVISORY: PropLock platform policy")
    assert "PropLock policy, not law" in advisory.note
    assert advisory.ok is True  # shown, not enforced
    assert hard.ok is True


def test_bengaluru_two_months_has_no_advisory():
    result = _check("Karnataka", "35000", "70000")

    assert result.verdict is Verdict.PASS
    assert "Advisory" not in result.summary
    assert not any(e.note.startswith("ADVISORY") for e in result.evidence)


def test_bengaluru_seven_months_fails_on_platform_policy():
    result = _check("Karnataka", "35000", "245000")

    assert result.verdict is Verdict.FAIL
    assert result.evidence[1].ok is False
    assert "PropLock policy, not law" in result.evidence[1].note


def test_state_without_rules_is_unverifiable_never_a_guessed_cap():
    result = _check("Maharashtra", "50000", "500000")

    assert result.verdict is Verdict.UNVERIFIABLE
    assert result.evidence == ()
    assert "No deposit rules on file for Maharashtra" in result.summary


def test_state_names_match_regardless_of_case_and_spaces():
    assert _check("  tamil   NADU ", "40000", "240000").verdict is Verdict.FAIL


@pytest.mark.parametrize(
    ("rent", "deposit", "months"),
    [("40000", "120000", "3.00"), ("35000", "100000", "2.86"), ("33333", "100000", "3.00"), ("40000", "0", "0.00")],
)
def test_deposit_months_rounds_to_two_places(rent, deposit, months):
    assert deposit_months(Decimal(rent), Decimal(deposit)) == Decimal(months)


def test_a_rounded_three_months_is_not_over_the_cap():
    # 100000 / 33333 = 3.00003 -> shown and judged as 3.00, which is within a 3-month cap.
    assert _check("Tamil Nadu", "33333", "100000").verdict is Verdict.PASS


def test_every_rule_in_the_file_is_well_formed():
    for rules in load_rules().by_state.values():
        for rule in rules:
            assert rule.severity in {"hard", "advisory"}
            assert rule.kind in {"law", "platform_policy"}
            assert rule.max_months > 0
            if rule.kind == "law":  # a statute must say where it comes from
                assert rule.section and rule.source_url
