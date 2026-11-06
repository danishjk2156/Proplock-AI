import json

import pytest

from app.config import Settings
from app.domain.signing import canonical_json, is_valid, seal_payload, sign

SECRET = "test-secret"
REPORT = json.dumps({"decision": "VERIFIED", "terms": {"rent": "35000.00", "title": "2BHK ₹35k"}, "photos": []})


def test_canonical_json_ignores_key_order_and_spacing():
    a = canonical_json({"b": 1, "a": {"y": 2, "x": 3}})
    b = canonical_json(json.loads('{ "a": {"x": 3, "y": 2},  "b": 1 }'))

    assert a == b == b'{"a":{"x":3,"y":2},"b":1}'


def test_payload_includes_the_audit_id():
    assert seal_payload("a1", REPORT) != seal_payload("a2", REPORT)


def test_same_input_same_signature():
    assert sign("a1", REPORT, SECRET) == sign("a1", REPORT, SECRET)
    assert len(sign("a1", REPORT, SECRET)) == 64  # hex SHA-256


def test_any_change_to_the_report_breaks_the_signature():
    signature = sign("a1", REPORT, SECRET)
    tampered = REPORT.replace("35000.00", "30000.00")

    assert is_valid("a1", REPORT, signature, SECRET)
    assert not is_valid("a1", tampered, signature, SECRET)


def test_reformatting_the_stored_json_does_not_break_it():
    # Same content, different whitespace and key order: still the same seal.
    reformatted = json.dumps(json.loads(REPORT), indent=2, sort_keys=False, ensure_ascii=True)

    assert is_valid("a1", reformatted, sign("a1", REPORT, SECRET), SECRET)


def test_wrong_secret_or_missing_signature_is_invalid():
    signature = sign("a1", REPORT, SECRET)

    assert not is_valid("a1", REPORT, signature, "another-secret")
    assert not is_valid("a1", REPORT, None, SECRET)
    assert not is_valid("a1", REPORT, "", SECRET)


def test_production_refuses_the_default_secret():
    with pytest.raises(ValueError, match="Set AUDIT_SECRET"):
        Settings(_env_file=None, app_env="production")
    assert Settings(_env_file=None, app_env="production", audit_secret="x" * 40).audit_secret == "x" * 40
