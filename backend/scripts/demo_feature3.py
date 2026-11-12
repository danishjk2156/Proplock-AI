"""End-to-end demo of Feature 3 on a throwaway database. Nothing touches proplock.db.

One verified LIVE listing; four tenants book a visit each (demo PayPal: FAKE- ids), and each
visit ends a different way at the door:
  A  PIN entered, tenant passes ................ hold voided
  B  PIN entered, tenant locks, broker approves . fee captured: the Feature 4 token
  C  broker at the flat, tenant never comes ..... fee captured as a no-show (from home: refused)
  D  tenant at the flat with a photo, no broker . hold voided, listing taken down
Plus: a wrong PIN, and a visit nobody proves anything about (settled by the deadline: voided).

Run from backend/:  .venv/Scripts/python -m scripts.demo_feature3
Exit code 0 = every step behaved as expected.
"""

import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.adapters.llm import FakeVision, get_llm, get_vision
from app.adapters.maps import DemoTableMaps, get_maps
from app.adapters.paypal import DemoPayPal, get_paypal
from app.adapters.paypal_orders import DemoOrders, get_orders
from app.api import visits as visits_api
from app.config import get_settings, get_upload_dir
from app.db import models
from app.db.base import Base, get_session, make_engine
from app.db.seed import seed
from app.main import create_app
from app.services import escrow_service, outbox
from app.services.rate_limit import RateLimiter
from scripts.demo import BLR_PIN, LocalClaimReader, Story, checkin_photo
from scripts.demo_feature4 import AD

AT_FLAT = {"latitude": BLR_PIN[0], "longitude": BLR_PIN[1], "accuracy_m": 10}
AT_HOME = {"latitude": "12.9000000", "longitude": "77.5000000", "accuracy_m": 10}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    get_settings().demo_mode = True
    visits_api.hold_limit = RateLimiter(limit=100, per_seconds=60)
    workdir = Path(tempfile.mkdtemp(prefix="proplock-f3-demo-"))
    engine = make_engine(f"sqlite:///{(workdir / 'demo.db').as_posix()}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        keys = {name: {"X-API-Key": key} for name, key in seed(session).items()}
    orders = DemoOrders()

    def session_override():
        with Session() as session:
            yield session

    app = create_app()
    app.dependency_overrides.update({
        get_session: session_override, get_upload_dir: lambda: workdir / "uploads", get_llm: LocalClaimReader,
        get_vision: lambda: FakeVision(), get_maps: DemoTableMaps, get_paypal: DemoPayPal, get_orders: lambda: orders,
    })
    client, story = TestClient(app), Story()
    broker, manager = keys["broker_rajesh"], keys["manager"]

    print("PropLock AI, Feature 3: PayPal Auth-Escrow & Check-In Referee")
    print(f"Database: {workdir / 'demo.db'} | PayPal: {orders.name}")

    story.step(1, "Rajesh's ad is verified and LIVE; five tenants each book a visit and authorise the fee")
    listing_id = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=broker).json()["id"]
    client.post(f"/broker/listings/{listing_id}/photos", data={"kind": "CHECKIN", "latitude": BLR_PIN[0], "longitude": BLR_PIN[1]},
                files=[("files", ("checkin.jpg", checkin_photo(), "image/jpeg"))], headers=broker)
    story.expect("listing", client.post(f"/broker/listings/{listing_id}/submit", json=AD, headers=broker).json()["listing_status"], "LIVE")
    slots = client.get(f"/public/listings/{listing_id}/slots").json()["slots"]
    booked = {}
    for n, (who, slot) in enumerate(zip(("Asha", "Bela", "Chandu", "Divya", "Esha"), slots[::2])):
        v = client.post(f"/public/listings/{listing_id}/visits", json={
            "tenant_name": who, "tenant_email": f"{who.lower()}@example.com",
            "starts_at": slot["starts_at"], "ends_at": slot["ends_at"]}).json()
        hold = {"X-Hold-Token": v["hold_token"]}
        client.post(f"/public/visits/{v['id']}/paypal-order", headers=hold)
        out = client.post(f"/public/visits/{v['id']}/authorize", headers=hold).json()
        booked[who] = (v["id"], hold, out["pin"])
        story.say(f"{who}: {out['status']} {out['authorization_ref']}, PIN shown only to {who}")
    with Session() as session:  # the demo can't wait for the visit: each one "started" 35 minutes ago
        for vid, _, _ in booked.values():
            row = session.get(models.Visit, vid)
            row.starts_at = datetime.now(UTC) - timedelta(minutes=35)
            row.ends_at = row.starts_at + timedelta(hours=1)
        session.commit()

    def visit(who, path, json=None, as_broker=True):
        vid, hold, _ = booked[who]
        base = f"/broker/visits/{vid}" if as_broker else f"/public/visits/{vid}"
        return client.post(f"{base}/{path}", json=json or {}, headers=broker if as_broker else hold)

    story.step(2, "A · Asha: the broker types a wrong PIN, then hers; she passes")
    story.expect("wrong PIN", visit("Asha", "pin", {"pin": "0000" if booked["Asha"][2] != "0000" else "1111"}).status_code, 422)
    story.expect("her PIN", visit("Asha", "pin", {"pin": booked["Asha"][2]}).json()["proof"]["pin_entered"], True)
    a = visit("Asha", "decision", {"choice": "PASS"}, as_broker=False).json()
    story.expect("outcome", (a["outcome"], a["status"]), ("PASSED", "VOIDED"))

    story.step(3, "B · Bela: PIN, she taps Lock, Rajesh approves")
    visit("Bela", "pin", {"pin": booked["Bela"][2]})
    story.expect("lock alone", visit("Bela", "decision", {"choice": "LOCK"}, as_broker=False).json()["status"], "COMMITTED")
    b = visit("Bela", "approve-lock").json()
    story.expect("outcome", (b["outcome"], b["status"]), ("LOCKED", "CAPTURED"))
    [closable] = client.get("/ledger/closable", headers=broker).json()
    story.expect("Feature 4 token", [(h["capture_ref"], h["tenant_name"]) for h in closable["holds"]], [(b["capture_ref"], "Bela")])

    story.step(4, "C · Chandu never comes: Rajesh claims a no-show, first from home, then from the flat")
    visit("Chandu", "checkin", AT_HOME)
    story.expect("claimed from home", visit("Chandu", "claim-no-show").json()["status"], "COMMITTED")
    story.expect("arriving later doesn't rescue that claim", visit("Chandu", "checkin", AT_FLAT).json()["status"], "COMMITTED")
    c = visit("Chandu", "claim-no-show").json()
    story.expect("outcome", (c["outcome"], c["status"]), ("TENANT_NO_SHOW", "CAPTURED"))

    story.step(5, "D · Divya waits at the flat; Rajesh never comes")
    vid, hold, _ = booked["Divya"]
    visit("Divya", "checkin", AT_FLAT, as_broker=False)
    client.post(f"/public/visits/{vid}/photo", headers=hold, data={k: str(v) for k, v in AT_FLAT.items()},
                files={"file": ("door.jpg", checkin_photo(), "image/jpeg")})
    d = visit("Divya", "decision", {"choice": "BROKER_ABSENT"}, as_broker=False).json()
    story.expect("outcome", (d["outcome"], d["status"]), ("BROKER_NO_SHOW", "VOIDED"))
    story.expect("listing", client.get(f"/public/listings/{listing_id}").status_code, 410)

    story.step(6, "Esha's visit: nobody proves anything; the worker settles it after the deadline")
    with Session() as session:
        story.expect("settled", escrow_service.settle_due(session, datetime.now(UTC) + timedelta(hours=14)), 1)
        outbox.run_due(session, None, datetime.now(UTC) + timedelta(hours=14), orders=orders)
        story.expect("Esha", session.get(models.Visit, booked["Esha"][0]).outcome, "NO_PROOF")

    story.step(7, "What PayPal was asked to do, and the ledger")
    story.expect("captures", len(orders.captures), 2)
    story.expect("voided", sorted(a.status for a in orders.authorizations.values()), ["CAPTURED", "CAPTURED", "VOIDED", "VOIDED", "VOIDED"])
    for v in client.get("/visits", headers=manager).json()["visits"]:
        story.say(f"  {v['tenant_name']:<7} {v['outcome']:<15} {v['status']:<9} {v['outcome_reason']}")

    print(f"\n{'Every step behaved as expected.' if story.ok else 'Some steps did NOT behave as expected.'}")
    engine.dispose()
    return 0 if story.ok else 1


if __name__ == "__main__":
    sys.exit(main())
