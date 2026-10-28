from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    Furnishing,
    Listing,
    ListingVersion,
    Mandate,
    Property,
    Role,
    User,
)


def _manager_broker_property(session):
    manager = User(name="XYZ Manager", email="m@xyz.test", role=Role.MANAGER, api_key_hash="a" * 64)
    broker = User(name="Rajesh", email="r@xyz.test", role=Role.BROKER, api_key_hash="b" * 64)
    session.add_all([manager, broker])
    session.flush()
    prop = Property(
        id="PROP-BLR-001",
        owner_name="Ramesh Kumar",
        address="12th Main Rd, Indiranagar",
        city="Bengaluru",
        state="Karnataka",
        postal_code="560038",
        latitude=Decimal("12.9718910"),
        longitude=Decimal("77.6411510"),
        furnishing=Furnishing.UNFURNISHED,
        created_by=manager.id,
    )
    session.add(prop)
    session.flush()
    mandate = Mandate(
        property_id=prop.id,
        broker_id=broker.id,
        min_rent=Decimal("32000.00"),
        max_rent=Decimal("35000.00"),
        max_deposit=Decimal("70000.00"),
        valid_until=datetime.now(UTC) + timedelta(days=30),
        created_by=manager.id,
    )
    session.add(mandate)
    session.flush()
    return manager, broker, prop, mandate


def _version(listing_id, no, rent="35000.10"):
    return ListingVersion(
        listing_id=listing_id,
        version_no=no,
        title="2BHK Indiranagar",
        description="Near metro",
        rent=Decimal(rent),
        deposit=Decimal("70000"),
        furnishing=Furnishing.UNFURNISHED,
    )


def test_money_round_trips_as_exact_decimal(db_session):
    _, broker, prop, mandate = _manager_broker_property(db_session)
    listing = Listing(property_id=prop.id, broker_id=broker.id, mandate_id=mandate.id)
    db_session.add(listing)
    db_session.flush()
    db_session.add(_version(listing.id, 1, rent="35000.10"))
    db_session.commit()
    db_session.expire_all()

    stored = db_session.query(ListingVersion).one()

    assert stored.rent == Decimal("35000.10")
    assert isinstance(stored.rent, Decimal)


def test_datetimes_come_back_timezone_aware(db_session):
    _, _, _, mandate = _manager_broker_property(db_session)
    db_session.commit()
    db_session.expire_all()

    stored = db_session.get(Mandate, mandate.id)

    assert stored.valid_until.tzinfo is UTC
    assert stored.valid_until > datetime.now(UTC)


def test_duplicate_version_number_is_rejected(db_session):
    _, broker, prop, mandate = _manager_broker_property(db_session)
    listing = Listing(property_id=prop.id, broker_id=broker.id, mandate_id=mandate.id)
    db_session.add(listing)
    db_session.flush()
    db_session.add_all([_version(listing.id, 1), _version(listing.id, 1)])

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_foreign_keys_are_enforced(db_session):
    _manager_broker_property(db_session)
    db_session.add(Listing(property_id="PROP-DOES-NOT-EXIST", broker_id="nobody", mandate_id="none"))

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_database_rejects_unknown_status_value(db_session):
    _, broker, prop, mandate = _manager_broker_property(db_session)
    db_session.commit()

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO listings (id, property_id, broker_id, mandate_id, status, created_at) "
                "VALUES ('x', :p, :b, :m, 'VERIFED', '2026-10-28 00:00:00')"
            ),
            {"p": prop.id, "b": broker.id, "m": mandate.id},
        )
