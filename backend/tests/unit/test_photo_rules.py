from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Furnishing, PhotoKind
from app.domain.check_result import Check, Verdict
from app.domain.photo_rules import PhotoFacts, PhotoInspection, check_photos

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def _checkin(distance=12.0, age=timedelta(hours=1), duplicate=None):
    return PhotoFacts("c1", PhotoKind.CHECKIN, distance, NOW - age, duplicate)


def _extra(inspection=None, error="", distance=None, duplicate=None):
    return PhotoFacts("e1", PhotoKind.EXTRA, distance, NOW, duplicate, inspection, error)


def _insp(same_flat=True, watermark=False, level=Furnishing.UNFURNISHED, confidence=0.9):
    return PhotoInspection(same_flat=same_flat, watermark=watermark, furniture_level=level, confidence=confidence)


def _run(photos, claimed=Furnishing.UNFURNISHED):
    return check_photos(photos, claimed, NOW, source="test")


def _failed(result):
    return [e.field for e in result.evidence if e.ok is False]


def test_good_checkin_alone_passes():
    result = _run([_checkin()])

    assert result.check is Check.PHOTOS
    assert result.verdict is Verdict.PASS


def test_missing_checkin_fails_and_says_what_to_do():
    result = _run([])

    assert result.verdict is Verdict.FAIL
    assert "Upload a check-in photo" in result.evidence[0].note


@pytest.mark.parametrize(
    ("photo", "failed"),
    [
        (_checkin(distance=2000), ["photo 1 (checkin) location"]),  # taken 2 km away
        (_checkin(distance=None), ["photo 1 (checkin) location"]),  # no GPS at all
        (_checkin(age=timedelta(days=31)), ["photo 1 (checkin) age"]),
        (_checkin(duplicate="a photo of PROP-CHN-002"), ["photo 1 (checkin) reuse"]),
    ],
    ids=["2-km-away", "no-gps", "too-old", "reused"],
)
def test_bad_checkin_fails(photo, failed):
    result = _run([photo])

    assert result.verdict is Verdict.FAIL
    assert _failed(result) == failed


def test_100_m_and_30_days_are_still_fine():
    assert _run([_checkin(distance=100, age=timedelta(days=30))]).verdict is Verdict.PASS


def test_extra_photo_from_the_same_flat_passes():
    result = _run([_checkin(), _extra(_insp())])

    assert result.verdict is Verdict.PASS


@pytest.mark.parametrize(
    ("extra", "failed"),
    [
        (_extra(_insp(same_flat=False)), "photo 2 (extra) same flat"),
        (_extra(_insp(watermark=True)), "photo 2 (extra) watermark"),
        (_extra(_insp(), distance=3500), "photo 2 (extra) location"),
        (_extra(_insp(), duplicate="a photo of PROP-CHN-002"), "photo 2 (extra) reuse"),
    ],
    ids=["other-flat", "watermark", "gps-elsewhere", "reused"],
)
def test_bad_extra_photo_fails(extra, failed):
    result = _run([_checkin(), extra])

    assert result.verdict is Verdict.FAIL
    assert _failed(result) == [failed]


@pytest.mark.parametrize(
    "extra",
    [
        _extra(None, error="GROQ_VISION_MODEL is not set"),  # no vision available
        _extra(_insp(confidence=0.4)),  # model not sure
        _extra(_insp(same_flat=None)),  # nothing to compare with, or can't tell
    ],
    ids=["no-vision", "low-confidence", "cant-tell"],
)
def test_extra_photo_that_cant_be_judged_is_unverifiable(extra):
    result = _run([_checkin(), extra])

    assert result.verdict is Verdict.UNVERIFIABLE
    assert any(e.ok is None for e in result.evidence)


def test_unverifiable_reason_is_shown():
    result = _run([_checkin(), _extra(None, error="GROQ_VISION_MODEL is not set")])

    assert "GROQ_VISION_MODEL is not set" in result.evidence[-1].note


def test_a_failure_beats_an_unknown():
    result = _run([_checkin(distance=2000), _extra(None, error="no vision")])

    assert result.verdict is Verdict.FAIL


def test_fully_furnished_ad_with_only_empty_rooms_fails():
    result = _run([_checkin(), _extra(_insp(level=Furnishing.UNFURNISHED))], claimed=Furnishing.FULLY)

    assert _failed(result) == ["furnishing in photos"]


def test_one_step_furnishing_gap_is_not_a_failure():
    # A semi-furnished ad with one empty-room photo isn't a lie: other rooms may have furniture.
    result = _run([_checkin(), _extra(_insp(level=Furnishing.UNFURNISHED))], claimed=Furnishing.SEMI)

    assert result.verdict is Verdict.PASS


def test_vision_furniture_level_accepts_any_case():
    assert PhotoInspection.model_validate(
        {"same_flat": True, "watermark": False, "furniture_level": "fully furnished", "confidence": 0.8}
    ).furniture_level is Furnishing.FULLY
