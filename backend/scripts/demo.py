"""End-to-end demo of Feature 1, on a throwaway database. Nothing touches proplock.db.

The story: a dishonest ad is rejected with evidence -> the pre-check suggests fixes -> the
broker resubmits -> VERIFIED and LIVE as a PayPal product -> the public page -> someone
tampers with the database -> /verify catches it.

Run from backend/:  .venv/Scripts/python -m scripts.demo [--offline]
  --offline   don't call Groq even if GROQ_API_KEY is set (a simple local claim reader is used)
Routes always come from the demo route table. PayPal is the real sandbox if keys are set,
otherwise DEMO_MODE "FAKE-" products. Exit code 0 = every step behaved as expected.
"""

import argparse
import io
import random
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import sessionmaker

from app.adapters.llm import ExtractedClaims, get_llm
from app.adapters.maps import DemoTableMaps, get_maps
from app.adapters.paypal import DemoPayPal, get_paypal
from app.config import get_settings, get_upload_dir
from app.db import models  # noqa: F401  (registers tables)
from app.db.base import Base, get_session, make_engine
from app.db.seed import seed
from app.main import create_app

BLR_PIN = ("12.9719500", "77.6412000")  # 8 m from the seeded Indiranagar flat
SHADY = {
    "title": "LUXURY 2BHK, 2 MIN WALK TO INDIRANAGAR METRO!!",
    "description": "Fully furnished 2BHK on 12th Main. 25 min drive to Bagmane Tech Park.",
    "rent": "40000",
    "deposit": "140000",
    "brokerage_fee": "40000",
    "furnishing": "Fully Furnished",
}
FIXED = {
    "title": "2BHK, 11 min walk to Indiranagar Metro",
    "description": "Unfurnished 2BHK on 12th Main. 25 min drive to Bagmane Tech Park.",
    "rent": "35000",
    "deposit": "70000",
    "brokerage_fee": "35000",
    "furnishing": "Unfurnished",
}


class LocalClaimReader:
    """Offline stand-in for the Groq extractor: understands "N min walk/drive to Place"."""

    name = "local-regex (offline demo)"
    PATTERN = re.compile(r"(\d+)\s*mins?\s+(walk|drive)\s+to\s+([A-Za-z][A-Za-z ]+?)(?:[.,!]|$)", re.IGNORECASE)

    def extract_claims(self, title: str, description: str) -> ExtractedClaims:
        text = f"{title}. {description}"
        commute = [
            {"destination": place.strip().title(), "minutes": int(n), "mode": "walking" if how.lower() == "walk" else "driving"}
            for n, how, place in self.PATTERN.findall(text)
        ]
        furnishing = next((f for f in ("Fully Furnished", "Semi-Furnished", "Unfurnished") if f.lower() in text.lower()), None)
        return ExtractedClaims.model_validate({"commute": commute, "furnishing": furnishing})


def checkin_photo() -> bytes:
    rng = random.Random(2026)
    img = Image.frombytes("L", (32, 32), bytes(rng.randrange(256) for _ in range(32 * 32))).resize((320, 320))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG")
    return buf.getvalue()


class Story:
    def __init__(self):
        self.ok = True

    def step(self, n: int, title: str) -> None:
        print(f"\n{n}. {title}")

    def say(self, text: str) -> None:
        print(f"   {text}")

    def expect(self, what: str, actual, expected) -> None:
        good = actual == expected
        self.ok &= good
        print(f"   {'OK  ' if good else 'FAIL'} {what}: {actual}" + ("" if good else f" (expected {expected})"))


def failed_rows(report: dict) -> list[str]:
    return [
        f"[{c['check']}] {e['field']}: claimed {e['claimed']}, allowed {e['allowed']}"
        for c in report["checks"] for e in c["evidence"] if e["ok"] is False
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="never call Groq")
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="proplock-demo-"))
    db_path = workdir / "demo.db"
    engine = make_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        keys = {name: {"X-API-Key": key} for name, key in seed(session).items()}

    settings = get_settings()
    llm = LocalClaimReader() if args.offline or not settings.groq_api_key else get_llm()
    paypal = get_paypal() if settings.paypal_client_id and settings.paypal_client_secret else DemoPayPal()

    def session_override():
        with Session() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_upload_dir] = lambda: workdir / "uploads"
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_maps] = DemoTableMaps
    app.dependency_overrides[get_paypal] = lambda: paypal
    client = TestClient(app)
    story = Story()
    broker = keys["broker_quickcash"]

    print("PropLock AI, Feature 1: Listing Audit & Commute-Truth Agent")
    print(f"Database: {db_path}")
    print(f"Claim reader: {llm.name} | routes: demo-table | PayPal: {paypal.name}")

    story.step(1, "The agency desk has registered the Indiranagar flat and given QuickCash a mandate")
    story.say("Owner agreed: rent 32,000-35,000 | deposit up to 70,000 | Unfurnished | brokerage up to 1 month")
    listing_id = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=broker).json()["id"]
    client.post(
        f"/broker/listings/{listing_id}/photos",
        data={"kind": "CHECKIN", "latitude": BLR_PIN[0], "longitude": BLR_PIN[1]},
        files=[("files", ("checkin.jpg", checkin_photo(), "image/jpeg"))],
        headers=broker,
    )
    story.say("QuickCash opens a listing and takes a check-in photo at the flat (8 m from the pin)")

    story.step(2, "QuickCash submits a dishonest ad")
    story.say(f'"{SHADY["title"]}" | rent {SHADY["rent"]} | deposit {SHADY["deposit"]} | {SHADY["furnishing"]}')
    first = client.post(f"/broker/listings/{listing_id}/submit", json=SHADY, headers=broker).json()
    story.expect("decision", first["report"]["decision"], "NEEDS_FIX")
    for row in failed_rows(first["report"]):
        story.say(f"  x {row}")

    story.step(3, "Pre-check: QuickCash fixes the money but keeps '2 MIN WALK' and asks what else is wrong")
    half_fixed = {**FIXED, "title": SHADY["title"]}
    pre = client.post("/broker/precheck", json={"listing_id": listing_id, "ad": half_fixed}, headers=broker).json()
    story.expect("would be", pre["would_be"], "NEEDS_FIX")
    for suggestion in pre["suggestions"]:
        story.say(f"  -> {suggestion}")
    pre = client.post("/broker/precheck", json={"listing_id": listing_id, "ad": FIXED}, headers=broker).json()
    story.expect("after following the suggestions, would be", pre["would_be"], "VERIFIED")

    story.step(4, "QuickCash resubmits the honest ad")
    second = client.post(f"/broker/listings/{listing_id}/submit", json=FIXED, headers=broker).json()
    story.expect("decision", second["report"]["decision"], "VERIFIED")
    story.expect("listing status (signed, then minted as a PayPal product)", second["listing_status"], "LIVE")
    audit_id = second["audit_id"]

    story.step(5, "The tenant's public page")
    page = client.get(f"/public/listings/{listing_id}").json()
    story.say(f'"{page["title"]}" | {page["furnishing"]} | {page["city"]}')
    story.say(f"rent {page['rent']} + deposit {page['deposit']} + brokerage {page['brokerage_fee']} = move-in {page['move_in_total']}")
    for c in page["commute"]:
        story.say(f"{c['minutes']} min {c['mode']} to {c['destination']}: {'checked at rush hour' if c['checked_at_rush_hour'] else 'not checked'}")
    story.say(f"broker {page['broker']['name']} | trust score {page['broker']['trust_score']}")
    story.say(f"PayPal product {page['paypal_product_id']}{' (demo)' if page['demo_product'] else ''} | {page['verify_url']}")
    verify = client.get(page["verify_url"]).json()
    story.expect("signature valid", verify["signature_valid"], True)

    story.step(6, "Someone edits the rent straight in the database (35,000 -> 30,000)")
    with sqlite3.connect(db_path) as db:
        db.execute("update listing_versions set rent = 30000 where version_no = 2")
    verify = client.get(page["verify_url"]).json()
    story.expect("signature valid", verify["signature_valid"], True)
    story.expect("matches current data", verify["matches_current_data"], False)
    story.say(f"mismatch: {verify['mismatches']}")

    story.step(7, "Someone edits the signed report itself instead")
    with sqlite3.connect(db_path) as db:
        db.execute("update listing_versions set rent = 35000 where version_no = 2")
        db.execute("update audits set report_json = replace(report_json, '\"35000.00\"', '\"30000.00\"') where id = ?",
                   (audit_id,))
    verify = client.get(page["verify_url"]).json()
    story.expect("signature valid", verify["signature_valid"], False)

    print(f"\n{'Every step behaved as expected.' if story.ok else 'Some steps did NOT behave as expected.'}")
    engine.dispose()
    return 0 if story.ok else 1


if __name__ == "__main__":
    sys.exit(main())
