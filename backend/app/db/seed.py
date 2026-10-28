"""Demo data: one manager, two brokers, two flats, three mandates.

Run from backend/ after `alembic upgrade head`:
    .venv/Scripts/python -m app.db.seed

New API keys are printed once and saved to backend/demo_keys.json (git-ignored).
Running it again does nothing if the manager already exists.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import generate_key, hash_key
from app.db.base import SessionLocal
from app.db.models import Furnishing, Mandate, Property, Role, User

KEYS_FILE = Path(__file__).resolve().parents[2] / "demo_keys.json"
MANAGER_EMAIL = "manager@xyzproperties.test"


def _user(session: Session, keys: dict[str, str], label: str, **fields) -> User:
    key = generate_key(fields["role"])
    user = User(api_key_hash=hash_key(key), **fields)
    session.add(user)
    keys[label] = key
    return user


def seed(session: Session) -> dict[str, str] | None:
    if session.scalar(select(User).where(User.email == MANAGER_EMAIL)):
        return None

    keys: dict[str, str] = {}
    manager = _user(session, keys, "manager", name="XYZ Properties Desk", email=MANAGER_EMAIL,
                    role=Role.MANAGER, agency="XYZ Properties")
    honest = _user(session, keys, "broker_rajesh", name="Rajesh", email="rajesh@brokers.test",
                   role=Role.BROKER, agency="Bangalore Premium Realty", licence_number="KA-RERA-AG-0001")
    shady = _user(session, keys, "broker_quickcash", name="QuickCash Rentals", email="quickcash@brokers.test",
                  role=Role.BROKER, agency="QuickCash Rentals", licence_number="KA-RERA-AG-0099")
    session.flush()

    blr = Property(
        id="PROP-BLR-001", owner_name="Ramesh Kumar", address="12th Main Rd, HAL 2nd Stage, Indiranagar",
        city="Bengaluru", state="Karnataka", postal_code="560038",
        latitude=Decimal("12.9718910"), longitude=Decimal("77.6411510"),
        furnishing=Furnishing.UNFURNISHED, created_by=manager.id,
    )
    chn = Property(
        id="PROP-CHN-002", owner_name="Sundar Rajan", address="Plot 42, Rajiv Gandhi Salai, Thoraipakkam",
        city="Chennai", state="Tamil Nadu", postal_code="600097",
        latitude=Decimal("12.9431870"), longitude=Decimal("80.2374820"),
        furnishing=Furnishing.SEMI, created_by=manager.id,
    )
    session.add_all([blr, chn])
    session.flush()

    until = datetime.now(UTC) + timedelta(days=60)
    session.add_all([
        Mandate(property_id=blr.id, broker_id=honest.id, min_rent=Decimal("32000"), max_rent=Decimal("35000"),
                max_deposit=Decimal("70000"), valid_until=until, created_by=manager.id),
        Mandate(property_id=blr.id, broker_id=shady.id, min_rent=Decimal("32000"), max_rent=Decimal("35000"),
                max_deposit=Decimal("70000"), valid_until=until, created_by=manager.id),
        Mandate(property_id=chn.id, broker_id=shady.id, min_rent=Decimal("38000"), max_rent=Decimal("40000"),
                max_deposit=Decimal("120000"), valid_until=until, created_by=manager.id),
    ])
    session.commit()
    return keys


def main() -> None:
    with SessionLocal() as session:
        keys = seed(session)
    if keys is None:
        print(f"Already seeded. Keys are in {KEYS_FILE.name} (if it was kept).")
        return
    KEYS_FILE.write_text(json.dumps(keys, indent=2), encoding="utf-8")
    print(f"Seeded. API keys (also saved to {KEYS_FILE.name}):")
    for label, key in keys.items():
        print(f"  {label:18} {key}")


if __name__ == "__main__":
    main()
