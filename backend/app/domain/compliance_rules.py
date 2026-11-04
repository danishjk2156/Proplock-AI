"""Check C: is the deposit allowed in this state? (fixes D6)

The caps are data in app/rules/deposit_caps.yaml, each with its citation, effective date
and whether it has been verified. This module only applies them. Statute and PropLock's
own policy are labelled differently, and a state with no rules is UNVERIFIABLE.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

import yaml

from app.domain.check_result import Check, CheckResult, Evidence, Verdict

DEPOSIT_CAPS = Path(__file__).resolve().parents[1] / "rules" / "deposit_caps.yaml"


@dataclass(frozen=True)
class DepositRule:
    max_months: Decimal
    severity: str  # "hard" -> FAIL above the cap; "advisory" -> PASS with a warning
    kind: str  # "law" or "platform_policy"
    citation: str
    section: str | None
    source_url: str | None
    effective_date: str
    verified: bool
    note: str = ""

    def label(self) -> str:
        where = f"{self.citation}, s.{self.section}" if self.section else self.citation
        tags = ["law" if self.kind == "law" else "PropLock policy, not law", f"effective {self.effective_date}"]
        if not self.verified:
            tags.append("NOT YET VERIFIED against the official text")
        return f"{where} [{'; '.join(tags)}]"


@dataclass(frozen=True)
class DepositRules:
    version: str
    by_state: dict[str, tuple[DepositRule, ...]]

    def for_state(self, state: str) -> tuple[DepositRule, ...]:
        return self.by_state.get(" ".join(state.lower().split()), ())


def load_rules(path: Path = DEPOSIT_CAPS) -> DepositRules:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    by_state = {
        " ".join(state.lower().split()): tuple(
            DepositRule(**{**rule, "max_months": Decimal(str(rule["max_months"])), "note": rule.get("note", "")})
            for rule in rules
        )
        for state, rules in data["states"].items()
    }
    return DepositRules(version=str(data["version"]), by_state=by_state)


@lru_cache
def default_rules() -> DepositRules:
    return load_rules()


def deposit_months(rent: Decimal, deposit: Decimal) -> Decimal:
    return (deposit / rent).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def check_compliance(state: str, rent: Decimal, deposit: Decimal, rules: DepositRules) -> CheckResult:
    source = f"deposit_caps.yaml@{rules.version}"
    applicable = rules.for_state(state)
    if not applicable:
        return CheckResult(
            check=Check.COMPLIANCE,
            verdict=Verdict.UNVERIFIABLE,
            source=source,
            summary=f"No deposit rules on file for {state}; a manager must check this one.",
        )

    months = deposit_months(rent, deposit)
    evidence: list[Evidence] = []
    failed, advisories = [], []
    for rule in applicable:
        within = months <= rule.max_months
        if rule.severity == "hard" and not within:
            failed.append(rule)
        if rule.severity == "advisory" and not within:
            advisories.append(rule)
        prefix = "ADVISORY: " if rule.severity == "advisory" and not within else ""
        evidence.append(
            Evidence(
                field="deposit_months" if rule.severity == "hard" else "deposit_months (advisory)",
                claimed=f"{months} months ({deposit:.2f} deposit / {rent:.2f} rent)",
                allowed=f"<= {rule.max_months.normalize()} months",
                # An advisory never fails the listing; it is shown, not enforced.
                ok=within or rule.severity == "advisory",
                note=f"{prefix}{rule.label()}. {rule.note}".strip(),
            )
        )

    if failed:
        verdict = Verdict.FAIL
        summary = f"Deposit of {months} months is above the {failed[0].max_months.normalize()}-month cap in {state}."
    else:
        verdict = Verdict.PASS
        summary = f"Deposit of {months} months is allowed in {state}."
        if advisories:
            summary += f" Advisory: above {advisories[0].max_months.normalize()} months, shown to the tenant."
    return CheckResult(
        check=Check.COMPLIANCE, verdict=verdict, evidence=tuple(evidence), source=source, summary=summary
    )
