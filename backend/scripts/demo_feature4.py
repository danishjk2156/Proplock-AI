"""End-to-end demo of Feature 4, on a throwaway database. Nothing touches proplock.db.

The story: a verified LIVE listing -> the tenant books a visit (Feature 2), its $15 fee is held
by a (demo) PayPal authorization, and at the door she locks the flat, so the referee captures
it (Feature 3) -> the flat is locked and a PayPal invoice goes out for deposit + brokerage minus
the token -> a forged "paid" webhook is refused -> the real one marks it PAID, once -> the
ledger copilot answers a question and refuses a write -> the tenant opens a dispute -> an
evidence pack and a cited draft -> a manager approves -> evidence submitted.

Run from backend/:  .venv/Scripts/python -m scripts.demo_feature4 [--offline]
  --offline   never call Groq (a fixed query and the template draft are used)
PayPal invoicing is always the DEMO twin (FAKE-INV- ids) and webhooks are signed by this script,
so the demo needs no public URL. Exit code 0 = every step behaved as expected.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from datetime import UTC, datetime, timedelta

from app.adapters.llm import FakeVision, get_llm, get_vision
from app.adapters.paypal_orders import DemoOrders, get_orders
from app.adapters.maps import DemoTableMaps, get_maps
from app.adapters.paypal import DemoPayPal, get_paypal
from app.adapters.paypal_invoicing import (
    DEMO_SIGNATURE_HEADER,
    DemoInvoicing,
    DemoWebhookVerifier,
    demo_signature,
    get_invoicing,
    get_webhook_verifier,
)
from app.agents.dispute_copilot import UnconfiguredDisputeWriter, get_dispute_writer
from app.agents.ledger_copilot import CopilotReply, FakeQueryLLM, Filter, LedgerQuery, get_query_llm
from app.config import get_settings, get_upload_dir
from app.db import models  # noqa: F401  (registers tables)
from app.db.base import Base, get_session, make_engine
from app.db.seed import seed
from app.main import create_app
from app.services import outbox
from scripts.demo import BLR_PIN, LocalClaimReader, Story, checkin_photo

AD = {
    "title": "2BHK in Indiranagar, 11 min walk to Indiranagar Metro",
    "description": "Unfurnished 2BHK on 12th Main, east facing, covered parking.",
    "rent": "35000",
    "deposit": "70000",
    "brokerage_fee": "35000",
    "furnishing": "Unfurnished",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="never call Groq")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252

    settings = get_settings()
    settings.demo_mode = True  # this process only: simulated Feature 3 capture + FAKE- ids
    settings.invoice_currency = "USD"
    online = not args.offline and bool(settings.groq_api_key and settings.groq_text_model)

    workdir = Path(tempfile.mkdtemp(prefix="proplock-f4-demo-"))
    engine = make_engine(f"sqlite:///{(workdir / 'demo.db').as_posix()}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        keys = {name: {"X-API-Key": key} for name, key in seed(session).items()}

    invoicing = DemoInvoicing()
    query_llm = get_query_llm() if online else FakeQueryLLM(CopilotReply(query=LedgerQuery(
        target="closings", filters=[Filter(field="invoice_status", op="eq", value="PAID")])))
    writer = get_dispute_writer() if online else UnconfiguredDisputeWriter("offline demo")

    def session_override():
        with Session() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_upload_dir] = lambda: workdir / "uploads"
    app.dependency_overrides[get_llm] = LocalClaimReader
    app.dependency_overrides[get_vision] = lambda: FakeVision()  # no extra photos in this demo
    app.dependency_overrides[get_maps] = DemoTableMaps
    app.dependency_overrides[get_paypal] = DemoPayPal
    app.dependency_overrides[get_invoicing] = lambda: invoicing
    orders = DemoOrders()
    app.dependency_overrides[get_orders] = lambda: orders
    app.dependency_overrides[get_webhook_verifier] = lambda: DemoWebhookVerifier(settings.audit_secret)
    app.dependency_overrides[get_query_llm] = lambda: query_llm
    app.dependency_overrides[get_dispute_writer] = lambda: writer
    client = TestClient(app)
    story = Story()
    broker, manager = keys["broker_rajesh"], keys["manager"]

    def webhook(event: dict, signed: bool = True):
        raw = json.dumps(event).encode()
        headers = {DEMO_SIGNATURE_HEADER: demo_signature(raw, settings.audit_secret)} if signed else {}
        return client.post("/webhooks/paypal", content=raw, headers=headers)

    print("PropLock AI, Feature 4: Token-to-Invoice Ledger & Dispute Defense Copilot")
    print(f"Database: {workdir / 'demo.db'}")
    print(f"Invoicing: {invoicing.name} | copilot: {query_llm.name} | dispute drafts: {writer.name}")

    story.step(1, "Rajesh's honest ad is verified and LIVE (Feature 1)")
    listing_id = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=broker).json()["id"]
    client.post(f"/broker/listings/{listing_id}/photos", data={"kind": "CHECKIN", "latitude": BLR_PIN[0], "longitude": BLR_PIN[1]},
                files=[("files", ("checkin.jpg", checkin_photo(), "image/jpeg"))], headers=broker)
    submitted = client.post(f"/broker/listings/{listing_id}/submit", json=AD, headers=broker).json()
    story.expect("listing", submitted["listing_status"], "LIVE")
    story.say("Signed terms: deposit 70,000 + brokerage 35,000")

    story.step(2, "Asha books a visit (Feature 2), the fee is held (Feature 3) and she locks the flat")
    slots = client.get(f"/public/listings/{listing_id}/slots").json()["slots"]
    slot = next(s for s in slots if s["fee"] == "15.00")  # 12-48 h ahead: the base fee
    visit = client.post(f"/public/listings/{listing_id}/visits", json={
        "tenant_name": "Asha Rao", "tenant_email": "asha@example.com",
        "starts_at": slot["starts_at"], "ends_at": slot["ends_at"]}).json()
    hold = {"X-Hold-Token": visit["hold_token"]}
    client.post(f"/public/visits/{visit['id']}/paypal-order", headers=hold)
    booked = client.post(f"/public/visits/{visit['id']}/authorize", headers=hold).json()
    story.expect("visit", booked["status"], "COMMITTED")
    with Session() as session:  # the demo can't wait for tomorrow: the visit "starts" now
        row = session.get(models.Visit, visit["id"])
        row.starts_at, row.ends_at = datetime.now(UTC) - timedelta(minutes=5), datetime.now(UTC) + timedelta(minutes=55)
        session.commit()
    client.post(f"/broker/visits/{visit['id']}/pin", json={"pin": booked["pin"]}, headers=broker)
    client.post(f"/public/visits/{visit['id']}/decision", json={"choice": "LOCK"}, headers=hold)
    locked = client.post(f"/broker/visits/{visit['id']}/approve-lock", headers=broker).json()
    story.expect("referee", locked["outcome"], "LOCKED")
    capture = {"authorization_ref": locked["authorization_ref"], "capture_ref": locked["capture_ref"]}
    story.say(f"hold {capture['authorization_ref']} authorised at booking, captured as {capture['capture_ref']} on Lock")

    story.step(3, "Asha locks the flat: the move-in invoice goes out with the token deducted")
    closing = client.post(f"/broker/listings/{listing_id}/close", json={
        "tenant_name": "Asha Rao", "tenant_email": "asha@example.com", "capture_ref": capture["capture_ref"]}, headers=broker).json()
    inv = closing["invoice"]
    story.say(f"invoice {inv['number']} ({inv['paypal_invoice_id']}): {inv['deposit']} + {inv['brokerage']} - {inv['token_deduction']}")
    story.expect("invoice total", inv["total"], "104985.00")
    story.expect("invoice status", inv["status"], "SENT")
    story.expect("invoice signature valid", inv["signature_valid"], True)

    story.step(4, "The worker runs twice more: nothing is created or sent twice")
    with Session() as session:
        outbox.run_due(session, None, invoicing=invoicing)
        outbox.run_due(session, None, invoicing=invoicing)
    story.expect("invoices in PayPal", len(invoicing.invoices), 1)
    story.expect("PayPal calls", [c[0] for c in invoicing.calls], ["create", "send"])

    paid_event = {"id": "WH-DEMO-PAID", "event_type": "INVOICING.INVOICE.PAID", "create_time": "2026-11-12T10:00:00Z",
                  "resource": {"invoice": {"id": inv["paypal_invoice_id"], "status": "PAID",
                                           "payments": {"paid_amount": {"currency_code": "USD", "value": inv["total"]}}}}}
    story.step(5, "Someone posts a forged 'invoice paid' webhook")
    story.expect("response", webhook(paid_event, signed=False).status_code, 400)
    story.expect("invoice still", client.get(f"/closings/{closing['id']}", headers=broker).json()["invoice"]["status"], "SENT")

    story.step(6, "PayPal's real 'invoice paid' webhook arrives, then is delivered again")
    story.expect("first delivery", webhook(paid_event).json()["outcome"], "applied")
    story.expect("second delivery", webhook(paid_event).json()["outcome"], "duplicate")
    detail = client.get(f"/closings/{closing['id']}", headers=broker).json()
    story.expect("closing", detail["status"], "PAID")
    story.expect("received (summed from the ledger)", detail["received"], "105000.00")
    story.expect("outstanding", detail["outstanding"], "0.00")
    for e in detail["entries"]:
        story.say(f"  {e['occurred_at'][:16]} {e['kind']:<17} {e['direction']:<4} {e['amount']:>10} {e['paypal_ref'] or ''}")

    story.step(7, "Ask the ledger")
    refused = client.post("/ledger/copilot", json={"question": "cancel all invoices"}, headers=manager).json()
    story.expect("'cancel all invoices' refused", refused["refused"], True)
    answer = client.post("/ledger/copilot", json={"question": "Which invoices have been paid?"}, headers=manager).json()
    story.expect("'which invoices have been paid?' rows", [r["id"] for r in answer["rows"]], [closing["id"]])
    story.say(answer["answer"].replace("\n", "\n   "))

    story.step(8, "Asha opens a PayPal dispute: 'not as described'")
    dispute_event = {"id": "WH-DEMO-DISPUTE", "event_type": "CUSTOMER.DISPUTE.CREATED", "resource": {
        "dispute_id": "FAKE-PP-D-DEMO", "reason": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "dispute_amount": {"currency_code": "USD", "value": inv["total"]},
        "disputed_transactions": [{"invoice_number": inv["number"]}]}}
    story.expect("webhook", webhook(dispute_event).json()["outcome"], "applied")
    [dispute] = client.get("/manager/disputes", headers=manager).json()
    for item in dispute["evidence_pack"]["items"]:
        story.say(f"  [{item['source_id'][:30]}] {item['summary'][:90]}")
    for note in dispute["evidence_pack"]["missing"]:
        story.say(f"  missing: {note}")

    story.step(9, "The copilot drafts a cited response; nothing is sent yet")
    drafted = client.post(f"/manager/disputes/{dispute['id']}/draft", headers=manager).json()
    story.say(f"draft by {drafted['draft_source']}:")
    story.say("  " + drafted["draft_response"][:600].replace("\n", "\n     "))
    story.expect("PayPal evidence calls before approval", [c for c in invoicing.calls if c[0] == "evidence"], [])

    story.step(10, "The manager approves the exact text; the outbox submits it")
    approved = client.post(f"/manager/disputes/{dispute['id']}/approve", json={"response": drafted["draft_response"]}, headers=manager).json()
    story.expect("dispute", approved["status"], "SUBMITTED")
    story.expect("PayPal evidence calls", len([c for c in invoicing.calls if c[0] == "evidence"]), 1)

    print(f"\n{'Every step behaved as expected.' if story.ok else 'Some steps did NOT behave as expected.'}")
    engine.dispose()
    return 0 if story.ok else 1


if __name__ == "__main__":
    sys.exit(main())
