"""Feature 4 ledger: the Feature 3 contract, append-only rows, idempotent writes, derived balances."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.db.models import LedgerDirection, LedgerEntry, LedgerIsAppendOnly, LedgerSource, Listing
from app.db.seed import seed
from app.domain.ledger import EntryKind, LedgerEvent, Movement, balance
from app.services import ledger_service

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)


@pytest.fixture
def listing_id(db_session):
    seed(db_session)
    from app.db.models import Mandate

    mandate = db_session.query(Mandate).first()
    listing = Listing(property_id=mandate.property_id, broker_id=mandate.broker_id, mandate_id=mandate.id)
    db_session.add(listing)
    db_session.commit()
    return listing.id


def _event(listing_id, kind=EntryKind.HOLD_CAPTURED, ref="CAP-1", amount="15.00"):
    return LedgerEvent(kind=kind, listing_id=listing_id, amount=Decimal(amount), currency="USD", paypal_ref=ref, occurred_at=NOW)


# --- contract ---

def test_feature3_contract_accepts_only_hold_events():
    assert _event("L1").idempotency_key == "f3:HOLD_CAPTURED:CAP-1"
    with pytest.raises(ValidationError, match="Feature 3 can only send"):
        _event("L1", kind=EntryKind.INVOICE_PAID)


@pytest.mark.parametrize("field,value", [("amount", "-1"), ("currency", "usd"), ("occurred_at", datetime(2026, 1, 1))])
def test_feature3_contract_rejects_bad_values(field, value):
    data = dict(kind="HOLD_CAPTURED", listing_id="L1", amount="15", currency="USD", paypal_ref="CAP-1", occurred_at=NOW)
    data[field] = value
    with pytest.raises(ValidationError):
        LedgerEvent(**data)


# --- balance (pure) ---

def test_balance_is_in_minus_out_and_ignores_info():
    entries = [
        Movement(Decimal("15"), "USD", LedgerDirection.IN),
        Movement(Decimal("1035"), "USD", LedgerDirection.IN),
        Movement(Decimal("100"), "USD", LedgerDirection.OUT),
        Movement(Decimal("999"), "USD", LedgerDirection.INFO),
        Movement(Decimal("500"), "INR", LedgerDirection.IN),
    ]
    assert balance(entries) == {"USD": Decimal("950.00"), "INR": Decimal("500.00")}
    assert balance([]) == {}


# --- service ---

def test_recording_the_same_event_twice_keeps_one_row(db_session, listing_id):
    first = ledger_service.ingest_feature3(db_session, _event(listing_id))
    again = ledger_service.ingest_feature3(db_session, _event(listing_id))

    assert again.id == first.id
    assert db_session.query(LedgerEntry).count() == 1


def test_a_key_reused_for_a_different_event_is_refused(db_session, listing_id):
    ledger_service.ingest_feature3(db_session, _event(listing_id))

    with pytest.raises(ledger_service.LedgerConflict):
        ledger_service.ingest_feature3(db_session, _event(listing_id, amount="20.00"))


def test_the_database_rejects_a_duplicate_key_even_past_the_service(db_session, listing_id):
    ledger_service.ingest_feature3(db_session, _event(listing_id))
    db_session.add(LedgerEntry(listing_id=listing_id, kind="HOLD_CAPTURED", amount=Decimal("15"), currency="USD",
                               direction=LedgerDirection.IN, idempotency_key="f3:HOLD_CAPTURED:CAP-1", source=LedgerSource.MANUAL))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_entries_cannot_be_updated_or_deleted(db_session, listing_id):
    entry = ledger_service.ingest_feature3(db_session, _event(listing_id))

    entry.amount = Decimal("1.00")
    with pytest.raises(LedgerIsAppendOnly):
        db_session.commit()
    db_session.rollback()

    db_session.delete(db_session.get(LedgerEntry, entry.id))
    with pytest.raises(LedgerIsAppendOnly):
        db_session.commit()
    db_session.rollback()
    assert db_session.get(LedgerEntry, entry.id).amount == Decimal("15.00")


def test_a_correction_is_a_new_reversing_entry(db_session, listing_id):
    ledger_service.ingest_feature3(db_session, _event(listing_id))
    ledger_service.record(db_session, listing_id=listing_id, kind=EntryKind.REVERSAL, amount=Decimal("15"), currency="USD",
                          idempotency_key="rev:CAP-1", source=LedgerSource.MANUAL, direction=LedgerDirection.OUT,
                          note="captured twice by mistake")
    db_session.commit()

    rows = db_session.query(LedgerEntry).all()
    assert len(rows) == 2
    assert balance(ledger_service.movements(rows)) == {"USD": Decimal("0.00")}


def test_unknown_listing_is_refused(db_session, listing_id):
    with pytest.raises(ledger_service.UnknownListing):
        ledger_service.ingest_feature3(db_session, _event("no-such-listing"))
