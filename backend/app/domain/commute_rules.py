"""Check B: is each commute claim true at rush hour? (fixes M3, D5)

The node looks up real travel times; this module only judges them, so the rules are
testable without any network. A claim with no route data is UNVERIFIABLE, never a guess.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone

from app.db.models import ClaimSource
from app.domain.check_result import Check, CheckResult, Evidence, Verdict
from app.domain.claims import ClaimedCommute

IST = timezone(timedelta(hours=5, minutes=30))
PEAK = time(8, 30)


def allowed_minutes(claimed: int) -> float:
    """20% slack, but at least 5 minutes, so short walks aren't failed over a traffic light."""
    return max(claimed * 1.2, claimed + 5)


def next_weekday_peak(now: datetime) -> datetime:
    """The next Monday-Friday 08:30 IST that is still in the future, returned in UTC."""
    local = now.astimezone(IST)
    candidate = datetime.combine(local.date(), PEAK, tzinfo=IST)
    while candidate <= local or candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


@dataclass(frozen=True)
class RouteLookup:
    """What the maps adapter found for one claim: minutes, or why there are none."""

    minutes: int | None
    source: str
    reason: str = ""  # set when minutes is None


def _evidence(claim: ClaimedCommute, lookup: RouteLookup) -> Evidence:
    allowed = allowed_minutes(claim.minutes)
    label = f"commute: {claim.destination} ({claim.mode.value})"
    origin = "ad text" if claim.source is ClaimSource.EXTRACTED else "form"
    if lookup.minutes is None:
        return Evidence(
            field=label,
            claimed=f"{claim.minutes} min",
            allowed=f"<= {allowed:g} min",
            ok=None,
            note=f"No route data ({lookup.reason}). Claim from the {origin}.",
        )
    return Evidence(
        field=label,
        claimed=f"{claim.minutes} min",
        allowed=f"<= {allowed:g} min",
        ok=lookup.minutes <= allowed,
        note=f"Actual {lookup.minutes} min at weekday 08:30 ({lookup.source}). Claim from the {origin}.",
    )


def check_commute(claims: list[ClaimedCommute], lookups: list[RouteLookup], source: str) -> CheckResult:
    """lookups[i] belongs to claims[i]. Any FAIL wins; otherwise any missing route means UNVERIFIABLE."""
    if len(claims) != len(lookups):
        raise ValueError("one lookup per claim")
    if not claims:
        return CheckResult(check=Check.COMMUTE, verdict=Verdict.PASS, source=source, summary="No commute claims.")

    evidence = tuple(_evidence(c, lk) for c, lk in zip(claims, lookups, strict=True))
    failed = sum(e.ok is False for e in evidence)
    unknown = sum(e.ok is None for e in evidence)
    if failed:
        verdict, summary = Verdict.FAIL, f"{failed} of {len(claims)} commute claim(s) are too optimistic."
    elif unknown:
        verdict, summary = Verdict.UNVERIFIABLE, f"{unknown} of {len(claims)} commute claim(s) have no route data."
    else:
        verdict, summary = Verdict.PASS, f"All {len(claims)} commute claim(s) hold at rush hour."
    return CheckResult(check=Check.COMMUTE, verdict=verdict, evidence=evidence, source=source, summary=summary)
