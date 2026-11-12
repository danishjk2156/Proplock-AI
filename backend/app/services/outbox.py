"""The outbox: work to do after a record is safely saved, retried until it succeeds (fixes M5, M7).

The dual-write problem: saving the audit and creating the PayPal product are two systems, and a
crash between them leaves one without the other. So the audit and a "mint" job are saved in the
same commit, and PayPal is called afterwards. If the call fails, the job is retried later with
the same product id, so a retry can never create a second product.

Feature 4 uses the same pattern for invoices and dispute evidence (app/services/invoice_jobs.py):
each job has a fixed PayPal-Request-Id, so running it twice never sends two invoices.

Run the worker (it also expires stale listings):  .venv/Scripts/python -m app.services.outbox
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
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


def enqueue(session: Session, kind: str, ref_id: str, run_at: datetime | None = None) -> OutboxJob:
    """Add (not commit) a Feature 4 job for an invoice or dispute, in the caller's transaction."""
    job = OutboxJob(ref_id=ref_id, kind=kind, next_run_at=run_at or datetime.now(UTC))
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


def _mint(session: Session, job: OutboxJob, adapters: "Adapters", now: datetime) -> None:
    audit = session.get(Audit, job.audit_id)
    version = session.get(ListingVersion, audit.version_id)
    product_id = adapters.paypal.create_product(product_for(audit, version))
    audit.paypal_product_id = product_id
    audit.status = AuditStatus.VERIFIED
    listing = session.get(Listing, version.listing_id)
    listing.status = ListingStatus.LIVE
    listing.last_confirmed_at = now


@dataclass
class Adapters:
    """The outside services jobs may call. A job whose service is missing here is left queued."""

    paypal: PayPal | None = None
    invoicing: Any = None  # app.adapters.paypal_invoicing.Invoicing
    orders: Any = None  # app.adapters.paypal_orders.Orders (Feature 3 visit holds)


def _handlers() -> dict[str, tuple[str, Callable]]:
    """kind -> (adapter it needs, handler). Feature 4 handlers live in invoice_jobs, Feature 3's
    (capture or void a visit hold) in escrow_jobs."""
    from app.services import escrow_jobs, invoice_jobs

    return {MINT: ("paypal", _mint), **invoice_jobs.HANDLERS, **escrow_jobs.HANDLERS}


def run_job(
    session: Session, job_id: str, paypal: PayPal | None, now: datetime | None = None, invoicing=None, orders=None
) -> JobStatus | None:
    """Try one job. Returns its new status, or None if it wasn't due, another worker has it,
    or the service it needs wasn't passed in."""
    now = now or datetime.now(UTC)
    adapters = Adapters(paypal=paypal, invoicing=invoicing, orders=orders)
    kind = session.scalar(select(OutboxJob.kind).where(OutboxJob.id == job_id))
    needs, handler = _handlers()[kind]
    if getattr(adapters, needs) is None or not _claim(session, job_id, now):
        return None
    job = session.get(OutboxJob, job_id)
    session.refresh(job)
    job.attempts += 1
    try:
        handler(session, job, adapters, now)
    except PayPalError as e:
        session.rollback()  # drop any half-made changes; then record the failure on a clean job
        job = session.get(OutboxJob, job_id)
        session.refresh(job)
        job.attempts += 1
        job.last_error = str(e)
        if job.attempts >= MAX_ATTEMPTS:
            job.status = JobStatus.FAILED  # e.g. the listing stays VERIFIED but not LIVE; a person must look
        else:
            job.next_run_at = now + backoff(job.attempts)
        logger.error("Outbox job %s (%s) attempt %d failed: %s", job.id, job.kind, job.attempts, e)
        session.commit()
        return job.status

    if job.status is JobStatus.PENDING:  # a handler may have refused the job (FAILED) itself
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


def try_ref_now(session: Session, ref_id: str, invoicing, now: datetime | None = None, rounds: int = 3, orders=None) -> None:
    """Run the due jobs for one invoice, dispute or visit now, following a short chain (create -> send).
    Failures leave them queued for the worker."""
    now = now or datetime.now(UTC)
    for _ in range(rounds):
        due = session.scalars(
            select(OutboxJob.id).where(
                OutboxJob.ref_id == ref_id, OutboxJob.status == JobStatus.PENDING, OutboxJob.next_run_at <= now
            )
        ).all()
        if not due:
            return
        for job_id in due:
            try:
                if run_job(session, job_id, None, now, invoicing=invoicing, orders=orders) is not JobStatus.DONE:
                    return
            except Exception:
                session.rollback()
                logger.exception("Immediate job %s failed; the outbox worker will retry", job_id)
                return


def run_due(session: Session, paypal: PayPal | None, now: datetime | None = None, limit: int = 20, invoicing=None,
            orders=None) -> int:
    """Run every job that is due. Returns how many succeeded."""
    now = now or datetime.now(UTC)
    due = session.scalars(
        select(OutboxJob.id)
        .where(OutboxJob.status == JobStatus.PENDING, OutboxJob.next_run_at <= now)
        .order_by(OutboxJob.next_run_at)
        .limit(limit)
    ).all()
    return sum(run_job(session, job_id, paypal, now, invoicing=invoicing, orders=orders) is JobStatus.DONE for job_id in due)


def main(poll_seconds: int = 30) -> None:
    from app.adapters.paypal import get_paypal
    from app.adapters.paypal_invoicing import get_invoicing
    from app.adapters.paypal_orders import get_orders
    from app.db.base import SessionLocal
    from app.services.escrow_service import settle_due
    from app.services.listing_service import expire_stale
    from app.services.visit_service import sweep_expired

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    paypal, invoicing, orders = get_paypal(), get_invoicing(), get_orders()
    logger.info("Outbox worker started with %s + %s + %s; polling every %ss", paypal.name, invoicing.name, orders.name,
                poll_seconds)
    while True:
        with SessionLocal() as session:
            settled = settle_due(session)  # booked visits past their deadline: the referee's default
            done = run_due(session, paypal, invoicing=invoicing, orders=orders)
            expired = expire_stale(session)  # LIVE listings not confirmed for 7 days
            holds = sweep_expired(session)  # visit soft holds nobody authorised in time
        if done or expired or holds or settled:
            logger.info("Ran %d job(s); expired %d stale listing(s) and %d visit hold(s); settled %d visit(s)",
                        done, expired, holds, settled)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
