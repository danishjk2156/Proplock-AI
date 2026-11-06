"""Keeping LIVE listings honest after the audit: confirm, expire, mark rented, and show publicly.

A bait listing is often a real flat that was rented weeks ago. So a LIVE listing must be
re-confirmed by its broker every 7 days, or it turns EXPIRED (and costs trust points).
A listing whose mandate is revoked or ends also leaves LIVE.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Audit,
    AuditStatus,
    BaselinePhoto,
    ClaimSource,
    CommuteClaim,
    Listing,
    ListingPhoto,
    ListingStatus,
    ListingVersion,
    Mandate,
    PhotoKind,
    Property,
    User,
)
from app.schemas.audit import AuditReport
from app.services import trust
from app.services.audit_service import ListingConflict, NoMandate, _is_usable

CONFIRM_EVERY = timedelta(days=7)


def expires_at(listing: Listing) -> datetime | None:
    return listing.last_confirmed_at + CONFIRM_EVERY if listing.last_confirmed_at else None


def expire_if_stale(session: Session, listing: Listing, now: datetime) -> bool:
    """Returns True if the listing was LIVE and has just been taken down."""
    if listing.status is not ListingStatus.LIVE:
        return False
    stale = listing.last_confirmed_at is None or now - listing.last_confirmed_at > CONFIRM_EVERY
    mandate_gone = not _is_usable(session.get(Mandate, listing.mandate_id))
    if not (stale or mandate_gone):
        return False
    listing.status = ListingStatus.EXPIRED
    if stale:  # the broker's fault; a revoked mandate isn't
        trust.record(session, listing.broker_id, trust.EXPIRED_STALE)
    return True


def expire_stale(session: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    live = session.scalars(select(Listing).where(Listing.status == ListingStatus.LIVE)).all()
    expired = sum(expire_if_stale(session, listing, now) for listing in live)
    session.commit()
    return expired


def confirm_availability(session: Session, listing: Listing, now: datetime | None = None) -> Listing:
    """'Still available': keeps a LIVE listing up for 7 more days, or brings back an EXPIRED one."""
    now = now or datetime.now(UTC)
    if listing.status not in (ListingStatus.LIVE, ListingStatus.EXPIRED):
        raise ListingConflict(f"Only LIVE or EXPIRED listings can be confirmed; this one is {listing.status}")
    if not _is_usable(session.get(Mandate, listing.mandate_id)):
        raise NoMandate("Your mandate for this flat was revoked or has expired")
    listing.status = ListingStatus.LIVE
    listing.last_confirmed_at = now
    session.commit()
    return listing


def mark_rented(session: Session, listing: Listing) -> Listing:
    if listing.status not in (ListingStatus.LIVE, ListingStatus.EXPIRED):
        raise ListingConflict(f"Only LIVE or EXPIRED listings can be marked rented; this one is {listing.status}")
    listing.status = ListingStatus.RENTED
    session.commit()
    return listing


def latest_audit(session: Session, listing: Listing) -> tuple[ListingVersion, Audit] | None:
    """This listing's newest version and its own audit (never another listing's badge: fixes D9)."""
    version = session.scalar(
        select(ListingVersion).where(ListingVersion.listing_id == listing.id).order_by(ListingVersion.version_no.desc())
    )
    if version is None:
        return None
    audit = session.scalar(select(Audit).where(Audit.version_id == version.id))
    return (version, audit) if audit else None


def _broker_card(broker: User) -> dict:
    return {"name": broker.name, "agency": broker.agency, "licence_number": broker.licence_number,
            "trust_score": broker.trust_score}


def public_view(session: Session, listing: Listing) -> dict | None:
    """What a tenant sees. None unless the listing is LIVE with a VERIFIED audit of its latest version."""
    found = latest_audit(session, listing)
    if listing.status is not ListingStatus.LIVE or found is None or found[1].status is not AuditStatus.VERIFIED:
        return None
    version, audit = found
    report = AuditReport.model_validate_json(audit.report_json)
    by_check = {c.check.value: c for c in report.checks}
    confirmed = {e.field: e.ok for e in by_check["commute"].evidence} if "commute" in by_check else {}
    advisories = [
        e.note.removeprefix("ADVISORY: ")
        for c in report.checks
        for e in c.evidence
        if e.note.startswith("ADVISORY: ")
    ]
    baselines = session.scalars(
        select(BaselinePhoto).where(BaselinePhoto.property_id == listing.property_id).order_by(BaselinePhoto.captured_at)
    ).all()
    terms = report.terms
    return {
        "listing_id": listing.id,
        "property_id": listing.property_id,
        "city": report.owner.city if report.owner else "",
        "title": terms.title,
        "description": terms.description,
        "rent": terms.rent,
        "deposit": terms.deposit,
        "brokerage_fee": terms.brokerage_fee,
        "move_in_total": terms.rent + terms.deposit + terms.brokerage_fee,
        "furnishing": terms.furnishing,
        "commute": [
            {
                "destination": c.destination,
                "minutes": c.minutes,
                "mode": c.mode,
                "checked_at_rush_hour": confirmed.get(f"commute: {c.destination} ({c.mode.value})") is True,
            }
            for c in report.commute_claims
        ],
        "advisories": advisories,
        # Layer 0 of the fake-photo defence: tenants see the owner's photos, not the broker's.
        "photos": [{"url": f"/public/photos/{p.id}", "room_type": p.room_type} for p in baselines],
        "broker": _broker_card(session.get(User, listing.broker_id)),
        "audit_id": audit.id,
        "verify_url": f"/public/audits/{audit.id}/verify",
        "paypal_product_id": audit.paypal_product_id,
        "demo_product": bool(audit.paypal_product_id and audit.paypal_product_id.startswith("FAKE-")),
        "verified_at": audit.created_at,
        "last_confirmed_at": listing.last_confirmed_at,
        "expires_at": expires_at(listing),
        "version_no": version.version_no,
    }


def broker_mandates(session: Session, broker: User) -> list[dict]:
    now = datetime.now(UTC)
    mandates = session.scalars(
        select(Mandate).where(Mandate.broker_id == broker.id).order_by(Mandate.created_at)
    ).all()
    out = []
    for mandate in mandates:
        if not (mandate.status.value == "ACTIVE" and mandate.valid_until > now):
            continue
        prop = session.get(Property, mandate.property_id)
        listing = session.scalar(
            select(Listing).where(Listing.mandate_id == mandate.id).order_by(Listing.created_at.desc())
        )
        photo = session.scalar(
            select(BaselinePhoto).where(BaselinePhoto.property_id == prop.id).order_by(BaselinePhoto.captured_at)
        )
        out.append({
            "photo_url": f"/public/photos/{photo.id}" if photo else None,
            "mandate_id": mandate.id,
            "property_id": prop.id,
            "address": prop.address,
            "city": prop.city,
            "state": prop.state,
            "furnishing": prop.furnishing,
            "latitude": prop.latitude,
            "longitude": prop.longitude,
            "min_rent": mandate.min_rent,
            "max_rent": mandate.max_rent,
            "max_deposit": mandate.max_deposit,
            "max_brokerage_months": mandate.max_brokerage_months,
            "valid_until": mandate.valid_until,
            "listing_id": listing.id if listing else None,
        })
    return out


def broker_listing_view(session: Session, listing: Listing) -> dict:
    prop = session.get(Property, listing.property_id)
    found = latest_audit(session, listing)
    version, audit = found if found else (None, None)
    problems = questions = 0
    if audit:
        report = AuditReport.model_validate_json(audit.report_json)
        rows = [e for c in report.checks for e in c.evidence]
        problems = sum(e.ok is False for e in rows)
        questions = sum(e.ok is None for e in rows)
    photos = session.scalars(
        select(ListingPhoto).where(ListingPhoto.listing_id == listing.id, ListingPhoto.removed_at.is_(None))
    ).all()
    ad = None
    if version is None:
        version = session.scalar(
            select(ListingVersion)
            .where(ListingVersion.listing_id == listing.id)
            .order_by(ListingVersion.version_no.desc())
        )
    if version is not None:
        claims = session.scalars(
            select(CommuteClaim).where(CommuteClaim.version_id == version.id, CommuteClaim.source == ClaimSource.FORM)
        ).all()
        ad = {
            "title": version.title,
            "description": version.description,
            "rent": version.rent,
            "deposit": version.deposit,
            "brokerage_fee": version.brokerage_fee,
            "furnishing": version.furnishing,
            "commute_claims": [{"destination": c.destination, "minutes": c.minutes, "mode": c.mode} for c in claims],
        }
    return {
        "id": listing.id,
        "property_id": listing.property_id,
        "address": f"{prop.address}, {prop.city}",
        "status": listing.status,
        "version_no": version.version_no if version else None,
        "audit_id": audit.id if audit else None,
        "audit_status": audit.status.value if audit else None,
        "problems": problems,
        "open_questions": questions,
        "photos": len(photos),
        "has_checkin": any(p.kind is PhotoKind.CHECKIN for p in photos),
        "last_confirmed_at": listing.last_confirmed_at,
        "expires_at": expires_at(listing),
        "created_at": listing.created_at,
        "ad": ad,
    }


def broker_listings(session: Session, broker: User) -> list[dict]:
    listings = session.scalars(
        select(Listing).where(Listing.broker_id == broker.id).order_by(Listing.created_at.desc())
    ).all()
    return [broker_listing_view(session, listing) for listing in listings]
