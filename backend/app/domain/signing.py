"""Seal an audit so any later change is detectable (fixes M4).

The signed bytes are the canonical JSON of {audit_id, report}. The report already holds
everything that was judged: the ad's terms and text, every commute claim, the broker's photo
fingerprints, the owner's terms, each check's result and source (model names, rules-file
version) and the ruleset version.

HMAC, not a plain hash: anyone can recompute SHA-256 of edited data, but only the server,
which holds AUDIT_SECRET, can produce a matching HMAC.
"""

import hashlib
import hmac
import json
from typing import Any


def canonical_json(data: Any) -> bytes:
    """One exact byte string per value: sorted keys, no spaces, UTF-8. Order would otherwise
    change the bytes (and the signature) without changing the meaning."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def seal_payload(audit_id: str, report_json: str) -> bytes:
    return canonical_json({"audit_id": audit_id, "report": json.loads(report_json)})


def sign(audit_id: str, report_json: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), seal_payload(audit_id, report_json), hashlib.sha256).hexdigest()


def is_valid(audit_id: str, report_json: str, signature: str | None, secret: str) -> bool:
    if not signature:
        return False
    # compare_digest takes the same time however many characters match, so it leaks nothing.
    return hmac.compare_digest(sign(audit_id, report_json, secret), signature)
