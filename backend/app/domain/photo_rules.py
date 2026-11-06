"""Check A: are these real, current photos of THIS flat? (fixes D1-D3)

The service measures each photo (distance from the flat, age, reuse on other flats) and the
vision model describes extra photos; this module only judges those facts.

- Check-in photo (required): taken within 100 m of the flat's verified pin, in the last 30 days.
- Any photo: must not be a copy of a photo used for a different flat.
- Extra photos: if they carry GPS it must be at the flat; the vision model must see the same
  flat as the owner's photos, no portal watermark, and enough confidence to trust its answer.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel, Field, field_validator

from app.db.models import Furnishing, PhotoKind
from app.domain.check_result import Check, CheckResult, Evidence, Verdict
from app.domain.ground_truth import FURNISHING_RANK

MAX_DISTANCE_M = 100
MAX_AGE = timedelta(days=30)
MIN_CONFIDENCE = 0.6


class PhotoInspection(BaseModel):
    """What the vision model says about one extra photo, compared with the owner's photos."""

    same_flat: bool | None  # None: no owner photos to compare with, or it can't tell
    watermark: bool  # portal logo, watermark or text overlay: a sign the photo was copied
    furniture_level: Furnishing
    confidence: float = Field(ge=0, le=1)
    reason: str = ""

    @field_validator("furniture_level", mode="before")
    @classmethod
    def _furniture(cls, value):
        if isinstance(value, str):
            for member in Furnishing:
                if member.value.lower() == value.strip().lower():
                    return member
        return value


@dataclass(frozen=True)
class PhotoInput:
    """One photo as the audit graph receives it: measured facts plus where the file is."""

    photo_id: str
    kind: PhotoKind
    path: str
    phash: str
    distance_m: float | None
    captured_at: datetime
    duplicate_of: str | None


@dataclass(frozen=True)
class PhotoFacts:
    photo_id: str
    kind: PhotoKind
    distance_m: float | None  # None: the photo has no location
    captured_at: datetime
    duplicate_of: str | None = None  # e.g. "a photo of PROP-CHN-002"
    inspection: PhotoInspection | None = None  # extra photos only
    inspection_error: str = ""  # why there is no inspection


def _row(field: str, claimed: str, allowed: str, ok: bool | None, note: str, fix: str = "") -> Evidence:
    return Evidence(field=field, claimed=claimed, allowed=allowed, ok=ok, note=note, suggestion=fix)


def _location(label: str, photo: PhotoFacts, required: bool) -> Evidence | None:
    if photo.distance_m is None:
        if not required:
            return None
        return _row(f"{label} location", "no GPS", f"<= {MAX_DISTANCE_M} m from the flat", False,
                    "Take the check-in photo at the flat with location access on.",
                    "Retake the check-in photo at the flat with location access on.")
    return _row(f"{label} location", f"{photo.distance_m:.0f} m from the flat", f"<= {MAX_DISTANCE_M} m",
                photo.distance_m <= MAX_DISTANCE_M, "Measured from the manager-verified pin.",
                "Remove this photo and take a new one at the flat." if photo.kind is PhotoKind.CHECKIN
                else "Remove this photo: it was taken somewhere else.")


def _inspection_rows(label: str, photo: PhotoFacts) -> list[Evidence]:
    insp = photo.inspection
    if insp is None:
        return [_row(f"{label} same flat", "not inspected", "same flat as the owner's photos", None,
                     f"No vision check ({photo.inspection_error}).", "A manager will compare it by hand.")]
    if insp.confidence < MIN_CONFIDENCE:
        return [_row(f"{label} same flat", f"confidence {insp.confidence:.2f}", f">= {MIN_CONFIDENCE}", None,
                     f"The model wasn't sure enough to judge: {insp.reason}",
                     "A manager will compare it by hand, or use a clearer photo.")]
    rows = [_row(f"{label} watermark", "watermark or logo" if insp.watermark else "none", "none",
                 not insp.watermark, "A portal watermark suggests the photo was copied from another ad.",
                 "Remove this photo and use your own, without logos or text.")]
    same = {True: "same flat", False: "different flat", None: "can't tell"}[insp.same_flat]
    rows.append(_row(f"{label} same flat", same, "same flat as the owner's photos",
                     insp.same_flat, f"confidence {insp.confidence:.2f}: {insp.reason}",
                     "Remove this photo or replace it with one taken in this flat."))
    return rows


def check_photos(photos: list[PhotoFacts], claimed_furnishing: Furnishing, now: datetime, source: str) -> CheckResult:
    evidence: list[Evidence] = []
    checkins = [p for p in photos if p.kind is PhotoKind.CHECKIN]
    if not checkins:
        evidence.append(_row("check-in photo", "none", "1 photo taken at the flat", False,
                             "Upload a check-in photo taken at the flat (it records GPS and time).",
                             "Upload a check-in photo taken at the flat with location access on."))

    for n, photo in enumerate(photos, start=1):
        label = f"photo {n} ({photo.kind.value.lower()})"
        is_checkin = photo.kind is PhotoKind.CHECKIN
        if (row := _location(label, photo, required=is_checkin)) is not None:
            evidence.append(row)
        if is_checkin:
            age = now - photo.captured_at
            evidence.append(_row(f"{label} age", f"{age.days} days", f"<= {MAX_AGE.days} days",
                                 age <= MAX_AGE, f"Taken {photo.captured_at:%Y-%m-%d %H:%M} UTC.",
                                 "Take a new check-in photo at the flat."))
        evidence.append(_row(f"{label} reuse", f"copy of {photo.duplicate_of}" if photo.duplicate_of else "unique",
                             "not used for any other flat", photo.duplicate_of is None,
                             "Compared by perceptual hash with every photo of every other flat.",
                             "Remove this photo: it is used for a different flat."))
        if not is_checkin:
            evidence.extend(_inspection_rows(label, photo))

    # Furnishing: only a clear contradiction fails ("Fully Furnished" ad, every judged photo empty).
    judged = [p.inspection for p in photos if p.inspection and p.inspection.confidence >= MIN_CONFIDENCE]
    if judged:
        seen = max((i.furniture_level for i in judged), key=FURNISHING_RANK.__getitem__)
        gap = FURNISHING_RANK[claimed_furnishing] - FURNISHING_RANK[seen]
        evidence.append(_row("furnishing in photos", seen.value, f"consistent with {claimed_furnishing.value}",
                             gap < 2, "Fails only when the ad says Fully Furnished and the photos show none.",
                             f"Describe the flat as {seen.value}, or add photos that show the furniture."))

    failed = sum(e.ok is False for e in evidence)
    unknown = sum(e.ok is None for e in evidence)
    if failed:
        verdict, summary = Verdict.FAIL, f"{failed} photo problem(s) found."
    elif unknown:
        verdict, summary = Verdict.UNVERIFIABLE, f"{unknown} photo check(s) couldn't be made."
    else:
        verdict, summary = Verdict.PASS, f"All {len(photos)} photo(s) check out."
    return CheckResult(check=Check.PHOTOS, verdict=verdict, evidence=tuple(evidence), source=source, summary=summary)
