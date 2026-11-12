"""Feature 2 end to end: the booking assistant, open slots, holds, authorisation, the calendar,
and the hand-off to Feature 4 (the visit's captured fee is the token a closing deducts)."""

import threading
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.visit_agent import AssistantUnavailable
from app.api import visits as visits_api
from app.services.rate_limit import RateLimiter
from app.db.base import make_engine
from app.db.models import LedgerEntry, Listing, Visit, VisitStatus
from app.domain.visit_rules import Collected, Qualification, SlotRefused
from app.services import visit_service
from tests.f4_helpers import TENANT, demo_mode, keys, live_listing  # noqa: F401  (fixtures)

GUEST = {"tenant_name": "Asha Rao", "tenant_email": "asha@example.com"}


@pytest.fixture(autouse=True)
def fresh_rate_limits(monkeypatch):
    """The limits live in the process; each test starts with its own."""
    monkeypatch.setattr(visits_api, "hold_limit", RateLimiter(limit=10, per_seconds=60))
    monkeypatch.setattr(visits_api, "assistant_limit", RateLimiter(limit=20, per_seconds=60))


def test_one_visitor_cannot_hold_a_whole_calendar(client, keys):
    listing_id, _ = live_listing(client, keys)
    open_ = slots(client, listing_id)
    codes = [hold(client, listing_id, s).status_code for s in open_[:11]]
    assert codes == [201] * 10 + [429]


def slots(client, listing_id) -> list[dict]:
    resp = client.get(f"/public/listings/{listing_id}/slots")
    assert resp.status_code == 200, resp.text
    return resp.json()["slots"]


def hold(client, listing_id, slot, **extra):
    body = {**GUEST, "starts_at": slot["starts_at"], "ends_at": slot["ends_at"], **extra}
    return client.post(f"/public/listings/{listing_id}/visits", json=body)


def held(client, listing_id, index=0) -> dict:
    resp = hold(client, listing_id, slots(client, listing_id)[index])
    assert resp.status_code == 201, resp.text
    return resp.json()


def tenant(visit) -> dict:
    return {"X-Hold-Token": visit["hold_token"]}


# --- assistant ---


def test_only_a_live_verified_listing_has_an_assistant(client, keys):
    assert client.post("/public/listings/nope/assistant", json={"message": "hi"}).status_code == 404
    draft = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=keys["broker_rajesh"]).json()
    assert client.post(f"/public/listings/{draft['id']}/assistant", json={"message": "hi"}).status_code == 404


def test_the_assistant_is_given_the_signed_facts_and_ready_needs_our_rules(client, keys, fake_assistant):
    listing_id, _ = live_listing(client, keys)
    soon = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    fake_assistant.result = Qualification(
        answer="You're all set.", ready_to_book=True,
        collected=Collected(budget=Decimal("36000"), move_in_date=date.today() + timedelta(days=20), preferred_slot=soon),
    )
    body = {"message": "We are a family", "collected": {"profile": "family"}, "history": [{"role": "assistant", "content": "Hi"}]}
    resp = client.post(f"/public/listings/{listing_id}/assistant", json=body)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["available"] and out["reply"]["ready_to_book"]
    assert out["reply"]["collected"]["profile"] == "family"  # kept from the earlier turn
    call = fake_assistant.calls[0]
    assert call["view"]["rent"] == Decimal("35000.00") and call["message"] == "We are a family"


def test_a_low_budget_is_never_ready_whatever_the_model_says(client, keys, fake_assistant):
    listing_id, _ = live_listing(client, keys)
    fake_assistant.result = Qualification(answer="Sure, ₹25,000 works!", ready_to_book=True, collected=Collected(
        budget=Decimal("25000"), move_in_date=date.today() + timedelta(days=9), profile="couple",
        preferred_slot=datetime.now(UTC) + timedelta(days=1)))
    reply = client.post(f"/public/listings/{listing_id}/assistant", json={"message": "Can you do 25k?"}).json()["reply"]
    assert not reply["ready_to_book"]
    assert any("below the verified rent" in p for p in reply["problems"])


def test_offline_assistant_says_so_and_never_invents_an_answer(client, keys, fake_assistant):
    listing_id, _ = live_listing(client, keys)
    fake_assistant.error = AssistantUnavailable("GROQ_API_KEY is not set")
    out = client.post(f"/public/listings/{listing_id}/assistant", json={"message": "Is there a gym?"}).json()
    assert not out["available"] and out["unavailable_reason"] == "GROQ_API_KEY is not set"
    assert "offline" in out["reply"]["answer"] and not out["reply"]["ready_to_book"]


def test_an_empty_message_is_a_greeting_without_calling_the_model(client, keys, fake_assistant):
    listing_id, _ = live_listing(client, keys)
    out = client.post(f"/public/listings/{listing_id}/assistant", json={"message": " "}).json()
    assert "2BHK in Indiranagar" in out["reply"]["answer"] and fake_assistant.calls == []


# --- slots and holds ---


def test_slots_are_inside_the_window_and_priced(client, keys):
    listing_id, _ = live_listing(client, keys)
    open_ = slots(client, listing_id)
    now = datetime.now(UTC)
    assert open_ and all(now < datetime.fromisoformat(s["starts_at"]) for s in open_)
    assert all(datetime.fromisoformat(s["ends_at"]) <= now + timedelta(days=3, minutes=1) for s in open_)
    assert {s["fee"] for s in open_} <= {"12.00", "15.00", "19.50"} and open_[0]["currency"] == "USD"


def test_a_hold_takes_the_slot_off_the_calendar(client, keys):
    listing_id, _ = live_listing(client, keys)
    first = slots(client, listing_id)[0]
    visit = held(client, listing_id)
    assert visit["status"] == "SOFT_HOLD" and visit["hold_token"]
    expires = datetime.fromisoformat(visit["expires_at"]) - datetime.now(UTC)
    assert timedelta(minutes=14) < expires <= timedelta(minutes=15)
    assert first["starts_at"] not in {s["starts_at"] for s in slots(client, listing_id)}
    again = hold(client, listing_id, first)
    assert again.status_code == 409 and "already held" in again.json()["detail"]


def test_holds_outside_the_window_are_refused(client, keys):
    listing_id, _ = live_listing(client, keys)
    later = datetime.now(UTC) + timedelta(days=4)
    resp = hold(client, listing_id, {"starts_at": later.isoformat(), "ends_at": (later + timedelta(hours=1)).isoformat()})
    assert resp.status_code == 409 and "3 days" in resp.json()["detail"]


def test_a_broker_needs_travel_time_between_two_flats(client, keys, db_session):
    listing_id, _ = live_listing(client, keys)
    first = slots(client, listing_id)[0]
    held(client, listing_id)
    other = Listing(**{c: getattr(db_session.get(Listing, listing_id), c) for c in ("property_id", "broker_id", "mandate_id")})
    db_session.add(other)
    db_session.commit()
    start = datetime.fromisoformat(first["ends_at"])
    try:
        visit_service.hold(db_session, other, **GUEST, starts_at=start, ends_at=start + timedelta(hours=1),
                           collected=None, now=datetime.now(UTC))
        raise AssertionError("expected a transit clash")
    except SlotRefused as e:
        assert "30 minutes" in str(e)


def test_release_frees_the_slot(client, keys):
    listing_id, _ = live_listing(client, keys)
    first = slots(client, listing_id)[0]
    visit = held(client, listing_id)
    assert client.post(f"/public/visits/{visit['id']}/release", headers={"X-Hold-Token": "wrong"}).status_code == 404
    resp = client.post(f"/public/visits/{visit['id']}/release", headers=tenant(visit))
    assert resp.json()["status"] == "RELEASED"
    assert first["starts_at"] in {s["starts_at"] for s in slots(client, listing_id)}
    assert client.post(f"/public/visits/{visit['id']}/paypal-order", headers=tenant(visit)).status_code == 409


# --- calendar ---


def test_brokers_see_their_own_visits_and_managers_see_all(client, keys):
    listing_id, _ = live_listing(client, keys)
    visit = held(client, listing_id, 0)
    client.post(f"/public/listings/{listing_id}/assistant", json={"message": ""})
    assert client.get("/visits").status_code == 401
    mine = client.get("/visits", headers=keys["broker_rajesh"]).json()
    assert [v["id"] for v in mine["visits"]] == [visit["id"]]
    assert mine["visits"][0]["listing_title"] == "2BHK in Indiranagar" and mine["visits"][0]["tenant_name"] == "Asha Rao"
    assert mine["transit_buffer_minutes"] == 30 and mine["utc_offset_minutes"] == 330
    assert client.get("/visits", headers=keys["broker_quickcash"]).json()["visits"] == []
    assert len(client.get("/visits", headers=keys["manager"]).json()["visits"]) == 1


# --- concurrency ---


def test_two_tenants_racing_for_one_slot_get_one_hold(client, keys, db_session, tmp_path):
    listing_id, _ = live_listing(client, keys)
    slot = slots(client, listing_id)[0]
    start, end = datetime.fromisoformat(slot["starts_at"]), datetime.fromisoformat(slot["ends_at"])
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")  # the same file the test session uses
    barrier, results = threading.Barrier(4), []

    def book(n):
        with Session(engine, expire_on_commit=False) as s:
            listing = s.get(Listing, listing_id)
            barrier.wait()
            try:
                visit_service.hold(s, listing, tenant_name=f"T{n}", tenant_email=f"t{n}@x.io",
                                   starts_at=start, ends_at=end, collected=None, now=datetime.now(UTC))
                results.append("won")
            except SlotRefused:
                results.append("refused")

    threads = [threading.Thread(target=book, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    engine.dispose()
    assert sorted(results) == ["refused", "refused", "refused", "won"]
    assert len(db_session.scalars(select(Visit)).all()) == 1
