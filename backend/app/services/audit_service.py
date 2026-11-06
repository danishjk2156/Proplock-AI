"""Open a listing, pre-check drafts, submit versions for audit, and settle manager reviews.

Submitting runs the checks first and then writes the new version, its audit (with the
full report_json) and the listing's new status in ONE commit (fixes M7): either all of
it is saved or none of it is, so there is never an audit without a report or a status
that no audit explains.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.llm import LLM, UnconfiguredVision, Vision
from app.adapters.maps import Maps, Place
from app.config import get_settings
from app.agents.graph import PRECHECK_CHECKS, AuditRun, run_checks
from app.db.models import (
    Audit,
    AuditStatus,
    CommuteClaim,
    ListingPhoto,
    Listing,
    ListingStatus,
    ListingVersion,
    Mandate,
    MandateStatus,
    new_id,
    Property,
    User,
)
from app.domain.check_result import Check, CheckResult, Verdict
from app.domain.decision import Decision
from app.domain.ground_truth import ListingTerms, OwnerTerms
from app.domain.signing import sign
from app.schemas.audit import AuditReport, OwnerSnapshot, PhotoRecord, SubmittedTerms
from app.schemas.listing import ListingSubmit
from app.services import outbox, photo_service, trust

# Bump whenever any check's rules change, so every stored report says which rules judged it.
RULESET_VERSION = "5"  # 2: claims from ad text; 3: Check B commute; 4: Check C deposits; 5: Check A photos

SUBMITTABLE = {ListingStatus.DRAFT, ListingStatus.NEEDS_FIX}
STATUS_FOR = {
    Decision.NEEDS_FIX: (AuditStatus.NEEDS_FIX, ListingStatus.NEEDS_FIX),
    Decision.IN_REVIEW: (AuditStatus.IN_REVIEW, ListingStatus.IN_REVIEW),
    # Saved and signed first; the outbox then creates the PayPal product and makes it VERIFIED / LIVE.
    Decision.VERIFIED: (AuditStatus.PENDING_MINT, ListingStatus.VERIFIED),
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


def _usable_mandate(session: Session, listing: Listing) -> Mandate:
    mandate = session.get(Mandate, listing.mandate_id)
    if not _is_usable(mandate):
        raise NoMandate("Your mandate for this flat was revoked or has expired")
    return mandate


def _judged_terms(mandate: Mandate, prop: Property, content: ListingSubmit) -> tuple[ListingTerms, OwnerTerms]:
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
    return terms, owner


def _place(prop: Property) -> Place:
    return Place(prop.id, prop.latitude, prop.longitude, prop.city, prop.state)


def precheck_listing(session: Session, listing: Listing, content: ListingSubmit, llm: LLM, maps: Maps) -> AuditRun:
    """Claims + Checks B, C, D on a draft. Nothing is saved, signed or minted; photos are skipped."""
    mandate = _usable_mandate(session, listing)
    prop = session.get(Property, listing.property_id)
    terms, owner = _judged_terms(mandate, prop, content)
    return run_checks(
        title=content.title,
        description=content.description,
        listing=terms,
        owner=owner,
        place=_place(prop),
        photos=[],
        baselines=[],
        form_commute=content.commute_claims,
        llm=llm,
        maps=maps,
        vision=UnconfiguredVision("photos are checked on submit, not in the pre-check"),
        checks=PRECHECK_CHECKS,
    )


def _photo_reused(results: list[CheckResult]) -> bool:
    return any(
        r.check is Check.PHOTOS and any(e.field.endswith(" reuse") and e.ok is False for e in r.evidence)
        for r in results
    )


def submit_listing(
    session: Session, listing: Listing, content: ListingSubmit, llm: LLM, maps: Maps, vision: Vision
) -> tuple[Audit, AuditReport]:
    mandate = _usable_mandate(session, listing)
    if listing.status not in SUBMITTABLE:
        raise ListingConflict(f"A listing in status {listing.status} cannot be submitted")
    prop = session.get(Property, listing.property_id)
    terms, owner = _judged_terms(mandate, prop, content)

    # Checks run before any write, so the transaction stays short once they call real APIs.
    photos = photo_service.photo_inputs(session, listing, prop)
    run = run_checks(
        title=content.title,
        description=content.description,
        listing=terms,
        owner=owner,
        place=_place(prop),
        photos=photos,
        baselines=photo_service.baseline_paths(session, prop),
        form_commute=content.commute_claims,
        llm=llm,
        maps=maps,
        vision=vision,
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
        for photo in session.scalars(select(ListingPhoto).where(ListingPhoto.id.in_([p.photo_id for p in photos]))):
            photo.version_id = photo.version_id or version.id  # first version audited with it

        report = AuditReport(
            listing_id=listing.id,
            property_id=listing.property_id,
            version_no=version.version_no,
            decision=run.decision,
            ruleset_version=RULESET_VERSION,
            checks=run.results,
            commute_claims=run.commute,
            text_furnishing=run.text_furnishing,
            photos=[PhotoRecord(id=p.photo_id, kind=p.kind, phash=p.phash) for p in photos],
            terms=SubmittedTerms(**content.model_dump(exclude={"commute_claims"})),
            owner=OwnerSnapshot(
                mandate_id=owner.mandate_id,
                min_rent=owner.min_rent,
                max_rent=owner.max_rent,
                max_deposit=owner.max_deposit,
                max_brokerage_months=owner.max_brokerage_months,
                furnishing=owner.furnishing,
                latitude=prop.latitude,
                longitude=prop.longitude,
                city=prop.city,
                state=prop.state,
            ),
        )
        audit_status, listing.status = STATUS_FOR[run.decision]
        audit_id = new_id()
        report_json = report.model_dump_json()
        audit = Audit(
            id=audit_id,
            version_id=version.id,
            status=audit_status,
            report_json=report_json,
            ruleset_version=RULESET_VERSION,
            # Every audit is sealed, failed ones too: they are evidence in disputes.
            signature=sign(audit_id, report_json, get_settings().audit_secret),
        )
        session.add(audit)
        session.flush()  # the audit row must exist before trust events point at it
        if audit_status is AuditStatus.PENDING_MINT:
            outbox.enqueue_mint(session, audit)  # same commit as the audit: never one without the other
            if version.version_no == 1:
                trust.record(session, listing.broker_id, trust.VERIFIED_FIRST_TRY, audit.id)
        if run.decision is Decision.NEEDS_FIX:
            trust.record(session, listing.broker_id, trust.NEEDS_FIX, audit.id)
        if _photo_reused(run.results):
            trust.record(session, listing.broker_id, trust.PHOTO_REUSED, audit.id)
        session.commit()
    except Exception:
        session.rollback()  # undo the flushed version and the status change
        raise
    return audit, report


def review_audit(session: Session, audit: Audit, manager: User, approve: bool, note: str) -> Listing:
    """A manager settles an IN_REVIEW audit. The signed report is never changed: the decision
    is recorded next to it (reviewed_by, review_note). Approving takes the normal minting path."""
    if audit.status is not AuditStatus.IN_REVIEW:
        raise ListingConflict(f"Only audits in review can be decided; this one is {audit.status}")
    version = session.get(ListingVersion, audit.version_id)
    listing = session.get(Listing, version.listing_id)
    audit.reviewed_by = manager.id
    audit.review_note = note
    if approve:
        _usable_mandate(session, listing)
        audit.status = AuditStatus.PENDING_MINT
        listing.status = ListingStatus.VERIFIED
        outbox.enqueue_mint(session, audit)
        if version.version_no == 1:
            trust.record(session, listing.broker_id, trust.VERIFIED_FIRST_TRY, audit.id)
    else:
        audit.status = AuditStatus.REJECTED
        listing.status = ListingStatus.NEEDS_FIX  # the broker can fix the ad and submit again
        trust.record(session, listing.broker_id, trust.NEEDS_FIX, audit.id)
    session.commit()
    return listing


def open_questions(report: AuditReport) -> list[str]:
    """What the checks couldn't decide, in plain words, for the manager's review queue."""
    questions = []
    for check in report.checks:
        if check.verdict is not Verdict.UNVERIFIABLE:
            continue
        rows = [e for e in check.evidence if e.ok is None]
        if not rows:
            questions.append(f"{check.check}: {check.summary}")
        questions += [f"{check.check}: {e.field}: {e.note}" for e in rows]
    return questions
