"""The outbox: the audit is saved first, the PayPal product is created once, failures are retried."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.adapters.paypal import FakePayPal, UnconfiguredPayPal
from app.db.models import Audit, JobStatus, Listing, ListingVersion, OutboxJob, Role
from app.db.seed import seed
from app.services import outbox
from tests.conftest import upload_photo

AD = {
    "title": "2BHK in Indiranagar",
    "description": "Unfurnished 2BHK on 12th Main, east facing, covered parking.",
    "rent": "35000",
    "deposit": "70000",
    "furnishing": "Unfurnished",
}


@pytest.fixture
def verified(client, db_session, fake_paypal):
    """A VERIFIED submit whose immediate mint failed, so its job is still waiting."""
    fake_paypal.fail_times = 1
    headers = {"X-API-Key": seed(db_session)["broker_rajesh"]}
    listing_id = client.post("/broker/listings", json={"property_id": "PROP-BLR-001"}, headers=headers).json()["id"]
    upload_photo(client, headers, listing_id)
    body = client.post(f"/broker/listings/{listing_id}/submit", json=AD, headers=headers).json()
    audit = db_session.get(Audit, body["audit_id"])
    job = db_session.scalar(select(OutboxJob).where(OutboxJob.audit_id == audit.id))
    return body, audit, job


def test_failed_first_mint_leaves_a_verified_but_not_live_listing(verified, db_session):
    body, audit, job = verified

    assert body["listing_status"] == "VERIFIED"  # not LIVE: no PayPal product yet
    assert audit.status.value == "PENDING_MINT"
    assert audit.signature is not None  # saved and sealed before PayPal was ever called
    assert (job.status, job.attempts) == (JobStatus.PENDING, 1)
    assert "debug_id=fake-debug-id" in job.last_error
    assert audit.paypal_product_id is None  # never a made-up id


def test_retry_is_not_run_before_its_backoff(verified, db_session):
    _, _, job = verified
    paypal = FakePayPal()

    assert outbox.run_due(db_session, paypal, now=datetime.now(UTC)) == 0
    assert paypal.calls == []


def test_retry_after_backoff_mints_once_with_our_id(verified, db_session):
    _, audit, job = verified
    paypal = FakePayPal()
    later = datetime.now(UTC) + timedelta(minutes=2)

    assert outbox.run_due(db_session, paypal, now=later) == 1

    version = db_session.get(ListingVersion, audit.version_id)
    [product] = paypal.calls
    assert product.id == f"PL-{version.id}"
    assert len(product.description) <= 256
    assert audit.signature[:16] in product.description
    db_session.refresh(audit)
    assert (audit.status.value, audit.paypal_product_id) == ("VERIFIED", product.id)
    listing = db_session.get(Listing, version.listing_id)
    assert listing.status.value == "LIVE"
    assert listing.last_confirmed_at is not None
    db_session.refresh(job)
    assert (job.status, job.attempts, job.last_error) == (JobStatus.DONE, 2, None)


def test_running_the_worker_twice_creates_the_product_once(verified, db_session):
    _, _, job = verified
    paypal = FakePayPal()
    later = datetime.now(UTC) + timedelta(minutes=2)

    outbox.run_due(db_session, paypal, now=later)
    outbox.run_due(db_session, paypal, now=later)

    assert len(paypal.calls) == 1


def test_a_claimed_job_cannot_be_taken_by_a_second_worker(verified, db_session):
    _, _, job = verified
    later = datetime.now(UTC) + timedelta(minutes=2)

    assert outbox._claim(db_session, job.id, later) is True
    # The first worker now holds a 5-minute lease; a second worker at the same moment gets nothing.
    assert outbox.run_job(db_session, job.id, FakePayPal(), now=later) is None


def test_gives_up_after_max_attempts(verified, db_session):
    _, audit, job = verified
    paypal = FakePayPal(fail_times=100)
    when = datetime.now(UTC)
    for _ in range(outbox.MAX_ATTEMPTS):
        when += timedelta(hours=2)
        outbox.run_due(db_session, paypal, now=when)

    db_session.refresh(job)
    assert (job.status, job.attempts) == (JobStatus.FAILED, outbox.MAX_ATTEMPTS)
    assert audit.paypal_product_id is None


def test_unconfigured_paypal_keeps_the_job_waiting(verified, db_session):
    _, _, job = verified

    outbox.run_due(db_session, UnconfiguredPayPal("PAYPAL_CLIENT_ID is not set"), now=datetime.now(UTC) + timedelta(hours=1))

    db_session.refresh(job)
    assert job.status is JobStatus.PENDING
    assert "PAYPAL_CLIENT_ID is not set" in job.last_error


@pytest.mark.parametrize(("attempts", "minutes"), [(1, 1), (2, 2), (3, 4), (7, 60), (20, 60)])
def test_backoff_doubles_up_to_an_hour(attempts, minutes):
    assert outbox.backoff(attempts) == timedelta(minutes=minutes)


def test_long_titles_fit_paypal_limits():
    audit = Audit(id="a" * 36, signature="f" * 64)
    version = ListingVersion(id="v" * 36, listing_id="l" * 36, version_no=12, title="T" * 255)

    product = outbox.product_for(audit, version)

    assert len(product.name) <= 127
    assert len(product.description) <= 256


def test_manager_can_run_the_outbox_now(verified, client, db_session, make_user):
    _, key = make_user(Role.MANAGER)
    _, _, job = verified
    job.next_run_at = datetime.now(UTC) - timedelta(seconds=1)  # due now
    db_session.commit()

    response = client.post("/manager/outbox/run", headers={"X-API-Key": key})

    assert response.json() == {"minted": 1}
