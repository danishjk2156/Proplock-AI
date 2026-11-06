"""The outbox: work to do after the audit is safely saved, retried until it succeeds (fixes M5, M7).

The dual-write problem: saving the audit and creating the PayPal product are two systems, and a
crash between them leaves one without the other. So the audit and a "mint" job are saved in the
same commit, and PayPal is called afterwards. If the call fails, the job is retried later with
the same product id, so a retry can never create a second product.

Run the worker (it also expires stale listings):  .venv/Scripts/python -m app.services.outbox
"""

import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.adapters.paypal import PayPal, PayPalError, Product
from app.db.models import Audit, AuditStatus, JobStatus, Listing, ListingStatus, ListingVersion, OutboxJob

logger = logging.getLogger(__name__)

MINT = "mint_catalog_product"
MAX_ATTEMPTS = 8
LEASE = timedelta(minutes=5)  # how long a worker may hold a job before another may take it


def enqueue_mint(session: Session, audit: Audit) -> OutboxJob:
    """Add (not commit) the job, so it is saved in the caller's transaction with the audit."""
    job = OutboxJob(audit_id=audit.id, kind=MINT)
    session.add(job)
    return job


def backoff(attempts: int) -> timedelta:
    """1, 2, 4, 8 ... minutes, at most an hour."""
    return min(timedelta(minutes=2 ** (attempts - 1)), timedelta(hours=1))


def product_for(audit: Audit, version: ListingVersion) -> Product:
    return Product(
        id=f"PL-{version.id}",
        name=f"PropLock verified: {version.title}"[:127],
        description=(
            f"PropLock verified rental listing {version.listing_id}, version {version.version_no}. "
            f"Audit {audit.id}, signature {audit.signature[:16]}."
        ),
    )


def _claim(session: Session, job_id: str, now: datetime) -> bool:
    """Atomically take the job: only one worker's UPDATE can match, the other sees 0 rows."""
    result = session.execute(
        update(OutboxJob)
        .where(OutboxJob.id == job_id, OutboxJob.status == JobStatus.PENDING, OutboxJob.next_run_at <= now)
        .values(next_run_at=now + LEASE)
    )
    session.commit()
    return result.rowcount == 1


def run_job(session: Session, job_id: str, paypal: PayPal, now: datetime | None = None) -> JobStatus | None:
    """Try one job. Returns its new status, or None if it wasn't due or another worker has it."""
    now = now or datetime.now(UTC)
    if not _claim(session, job_id, now):
        return None
    job = session.get(OutboxJob, job_id)
    session.refresh(job)
    audit = session.get(Audit, job.audit_id)
    version = session.get(ListingVersion, audit.version_id)
    job.attempts += 1
    try:
        product_id = paypal.create_product(product_for(audit, version))
    except PayPalError as e:
        job.last_error = str(e)
        if job.attempts >= MAX_ATTEMPTS:
            job.status = JobStatus.FAILED  # the listing stays VERIFIED but not LIVE; a person must look
        else:
            job.next_run_at = now + backoff(job.attempts)
        logger.error("Outbox job %s attempt %d failed: %s", job.id, job.attempts, e)
        session.commit()
        return job.status

    audit.paypal_product_id = product_id
    audit.status = AuditStatus.VERIFIED
    listing = session.get(Listing, version.listing_id)
    listing.status = ListingStatus.LIVE
    listing.last_confirmed_at = now
    job.status = JobStatus.DONE
    job.last_error = None
    session.commit()
    return job.status


def try_now(session: Session, audit_id: str, paypal: PayPal) -> None:
    """Run the audit's mint job right away, if it has one. Failures leave it queued for the worker."""
    job = session.scalar(select(OutboxJob).where(OutboxJob.audit_id == audit_id, OutboxJob.status == JobStatus.PENDING))
    if job is None:
        return
    try:
        run_job(session, job.id, paypal)
    except Exception:
        session.rollback()
        logger.exception("Immediate mint failed for audit %s; the outbox worker will retry", audit_id)


def run_due(session: Session, paypal: PayPal, now: datetime | None = None, limit: int = 20) -> int:
    """Run every job that is due. Returns how many succeeded."""
    now = now or datetime.now(UTC)
    due = session.scalars(
        select(OutboxJob.id)
        .where(OutboxJob.status == JobStatus.PENDING, OutboxJob.next_run_at <= now)
        .order_by(OutboxJob.next_run_at)
        .limit(limit)
    ).all()
    return sum(run_job(session, job_id, paypal, now) is JobStatus.DONE for job_id in due)


def main(poll_seconds: int = 30) -> None:
    from app.adapters.paypal import get_paypal
    from app.db.base import SessionLocal
    from app.services.listing_service import expire_stale

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    paypal = get_paypal()
    logger.info("Outbox worker started with %s; polling every %ss", paypal.name, poll_seconds)
    while True:
        with SessionLocal() as session:
            done = run_due(session, paypal)
            expired = expire_stale(session)  # LIVE listings not confirmed for 7 days
        if done or expired:
            logger.info("Minted %d product(s), expired %d stale listing(s)", done, expired)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
