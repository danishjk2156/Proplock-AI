"""Open a listing, then submit versions of it for audit.

Submitting runs the checks first and then writes the new version, its audit (with the
full report_json) and the listing's new status in ONE commit (fixes M7): either all of
it is saved or none of it is, so there is never an audit without a report or a status
that no audit explains.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.llm import LLM
from app.adapters.maps import Maps, Place
from app.agents.graph import run_checks
from app.db.models import (
    Audit,
    AuditStatus,
    CommuteClaim,
    Listing,
    ListingStatus,
    ListingVersion,
    Mandate,
    MandateStatus,
    Property,
    User,
)
from app.domain.decision import Decision
from app.domain.ground_truth import ListingTerms, OwnerTerms
from app.schemas.audit import AuditReport
from app.schemas.listing import ListingSubmit

# Bump whenever any check's rules change, so every stored report says which rules judged it.
RULESET_VERSION = "4"  # 2: claims from ad text; 3: Check B commute; 4: Check C deposit rules

SUBMITTABLE = {ListingStatus.DRAFT, ListingStatus.NEEDS_FIX}
STATUS_FOR = {
    Decision.NEEDS_FIX: (AuditStatus.NEEDS_FIX, ListingStatus.NEEDS_FIX),
    Decision.IN_REVIEW: (AuditStatus.IN_REVIEW, ListingStatus.IN_REVIEW),
    # Phase 7 changes this to PENDING_MINT: sign the audit, then mint the PayPal product.
    Decision.VERIFIED: (AuditStatus.VERIFIED, ListingStatus.VERIFIED),
}


class NoMandate(Exception):
    """The broker has no active, unexpired mandate for this flat."""


class ListingConflict(Exception):
    """The request is valid but clashes with the listing's current state."""


def _is_usable(mandate: Mandate) -> bool:
    return mandate.status is MandateStatus.ACTIVE and mandate.valid_until > datetime.now(UTC)


def open_listing(session: Session, broker: User, property_id: str) -> Listing:
    mandates = session.scalars(
        select(Mandate).where(Mandate.property_id == property_id, Mandate.broker_id == broker.id)
    ).all()
    mandate = next((m for m in mandates if _is_usable(m)), None)
    if mandate is None:
        raise NoMandate(f"You have no active mandate for {property_id}")

    existing = session.scalar(
        select(Listing).where(Listing.mandate_id == mandate.id, Listing.status.in_(SUBMITTABLE))
    )
    if existing:
        raise ListingConflict(f"You already have an open listing for this flat: {existing.id}")

    listing = Listing(property_id=property_id, broker_id=broker.id, mandate_id=mandate.id)
    session.add(listing)
    session.commit()
    return listing


def submit_listing(
    session: Session, listing: Listing, content: ListingSubmit, llm: LLM, maps: Maps
) -> tuple[Audit, AuditReport]:
    mandate = session.get(Mandate, listing.mandate_id)
    if not _is_usable(mandate):
        raise NoMandate("Your mandate for this flat was revoked or has expired")
    if listing.status not in SUBMITTABLE:
        raise ListingConflict(f"A listing in status {listing.status} cannot be submitted")
    prop = session.get(Property, listing.property_id)

    owner = OwnerTerms(
        mandate_id=mandate.id,
        min_rent=mandate.min_rent,
        max_rent=mandate.max_rent,
        max_deposit=mandate.max_deposit,
        max_brokerage_months=mandate.max_brokerage_months,
        furnishing=prop.furnishing,
    )
    terms = ListingTerms(
        rent=content.rent,
        deposit=content.deposit,
        brokerage_fee=content.brokerage_fee,
        furnishing=content.furnishing,
    )
    # Checks run before any write, so the transaction stays short once they call real APIs.
    run = run_checks(
        title=content.title,
        description=content.description,
        listing=terms,
        owner=owner,
        place=Place(prop.id, prop.latitude, prop.longitude, prop.city, prop.state),
        form_commute=content.commute_claims,
        llm=llm,
        maps=maps,
    )

    try:
        last_no = session.scalar(
            select(func.max(ListingVersion.version_no)).where(ListingVersion.listing_id == listing.id)
        )
        version = ListingVersion(
            listing_id=listing.id,
            version_no=(last_no or 0) + 1,
            **content.model_dump(exclude={"commute_claims"}),
        )
        session.add(version)
        session.flush()  # gives the version its id without committing yet
        session.add_all(CommuteClaim(version_id=version.id, **c.model_dump()) for c in run.commute)

        report = AuditReport(
            listing_id=listing.id,
            property_id=listing.property_id,
            version_no=version.version_no,
            decision=run.decision,
            ruleset_version=RULESET_VERSION,
            checks=run.results,
            commute_claims=run.commute,
            text_furnishing=run.text_furnishing,
        )
        audit_status, listing.status = STATUS_FOR[run.decision]
        audit = Audit(
            version_id=version.id,
            status=audit_status,
            report_json=report.model_dump_json(),
            ruleset_version=RULESET_VERSION,
        )
        session.add(audit)
        session.commit()
    except Exception:
        session.rollback()  # undo the flushed version and the status change
        raise
    return audit, report
