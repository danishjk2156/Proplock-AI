"""Feature 4 dispute defense: cited drafts only, and nothing reaches PayPal without approval."""

import json

import pytest

from app.agents.dispute_copilot import check_citations, template_draft
from tests.f4_helpers import closed, demo_mode, keys  # noqa: F401  (fixtures)


@pytest.fixture
def dispute(client, keys, demo_mode):
    body = closed(client, keys)
    resp = client.post(f"/dev/disputes/simulate?closing_id={body['id']}", headers=keys["manager"])
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_dispute_screens_are_for_managers(client, keys, dispute):
    assert client.get("/manager/disputes", headers=keys["broker_rajesh"]).status_code == 403
    [row] = client.get("/manager/disputes", headers=keys["manager"]).json()
    assert row["id"] == dispute["id"] and row["paypal_dispute_id"].startswith("FAKE-")


def test_without_a_model_the_draft_is_the_cited_template(client, keys, dispute, fake_invoicing):
    out = client.post(f"/manager/disputes/{dispute['id']}/draft", headers=keys["manager"]).json()

    assert out["draft_source"].startswith("template")
    assert check_citations(out["draft_response"], out["evidence_pack"]) == []
    assert "Not available" in out["draft_response"]  # missing evidence said plainly
    assert out["status"] == "OPEN"
    assert [c for c in fake_invoicing.calls if c[0] == "evidence"] == []  # drafting sends nothing


def test_a_model_draft_must_cite_real_records(client, keys, dispute, fake_dispute_writer):
    audit_item = next(i for i in dispute["evidence_pack"]["items"] if i["kind"] == "signed_listing_audit")
    fake_dispute_writer.text = f"The listing terms were verified and signed [{audit_item['source_id']}]."
    good = client.post(f"/manager/disputes/{dispute['id']}/draft", headers=keys["manager"]).json()
    assert good["draft_source"] == "fake"

    fake_dispute_writer.text = "Trust us, it was fine [audit:made-up-id]."
    bad = client.post(f"/manager/disputes/{dispute['id']}/draft", headers=keys["manager"]).json()
    assert bad["draft_source"].startswith("template")  # the invented citation was rejected


def test_approval_submits_the_exact_text_once(client, keys, dispute, fake_invoicing, db_session):
    text = "We delivered exactly what was verified. " + template_draft(dispute["evidence_pack"])[:500]

    out = client.post(f"/manager/disputes/{dispute['id']}/approve", json={"response": text}, headers=keys["manager"]).json()
    again = client.post(f"/manager/disputes/{dispute['id']}/approve", json={"response": text}, headers=keys["manager"])

    assert out["status"] == "SUBMITTED" and out["final_response"] == text and out["approved_by"]
    assert again.status_code == 409
    assert [c for c in fake_invoicing.calls if c[0] == "evidence"] == [("evidence", f"evidence-{dispute['id']}")]


def test_brokers_cannot_approve(client, keys, dispute):
    resp = client.post(f"/manager/disputes/{dispute['id']}/approve", json={"response": "x" * 30}, headers=keys["broker_rajesh"])
    assert resp.status_code == 403


def test_citation_checker():
    pack = {"items": [{"source_id": "audit:a1"}, {"source_id": "invoice:i-1"}]}
    assert check_citations("Signed [audit:a1], billed [invoice:i-1].", pack) == []
    assert check_citations("No citations at all.", pack) == ["cites no evidence"]
    assert check_citations("Bad [ledger:zzz].", pack) == ["cites unknown source ledger:zzz"]
    assert json.dumps(template_draft({"items": [], "missing": []}))
