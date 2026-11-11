"""ORM tables for Feature 1 (listings and audits) and Feature 4 (closings, ledger, invoices).

Money is Numeric(12, 2) -> Python Decimal (never float). Enums are stored as
VARCHAR with a CHECK constraint so the same schema works on SQLite and Postgres.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, Numeric, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


def enum_column(enum_cls: type[StrEnum], **kwargs):
    return mapped_column(
        Enum(
            enum_cls,
            native_enum=False,
            create_constraint=True,  # the database itself rejects unknown values
            length=20,
            values_callable=lambda e: [m.value for m in e],
        ),
        **kwargs,
    )


class Role(StrEnum):
    MANAGER = "MANAGER"
    BROKER = "BROKER"


class Furnishing(StrEnum):
    UNFURNISHED = "Unfurnished"
    SEMI = "Semi-Furnished"
    FULLY = "Fully Furnished"


class MandateStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class ListingStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    NEEDS_FIX = "NEEDS_FIX"
    IN_REVIEW = "IN_REVIEW"
    VERIFIED = "VERIFIED"
    LIVE = "LIVE"
    EXPIRED = "EXPIRED"
    RENTED = "RENTED"


class AuditStatus(StrEnum):
    NEEDS_FIX = "NEEDS_FIX"
    IN_REVIEW = "IN_REVIEW"
    PENDING_MINT = "PENDING_MINT"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class PhotoKind(StrEnum):
    CHECKIN = "CHECKIN"
    EXTRA = "EXTRA"


class CommuteMode(StrEnum):
    WALKING = "walking"
    TRANSIT = "transit"
    DRIVING = "driving"


class ClaimSource(StrEnum):
    FORM = "FORM"
    EXTRACTED = "EXTRACTED"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    DONE = "DONE"
    FAILED = "FAILED"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    role: Mapped[Role] = enum_column(Role)
    # Only a SHA-256 of the API key is stored, so a leaked database does not leak working keys.
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    agency: Mapped[str | None] = mapped_column(String(150))
    licence_number: Mapped[str | None] = mapped_column(String(100))
    trust_score: Mapped[int] = mapped_column(default=100)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # e.g. PROP-BLR-001
    owner_name: Mapped[str] = mapped_column(String(150))
    address: Mapped[str] = mapped_column(Text)
    city: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(100))
    postal_code: Mapped[str] = mapped_column(String(20))
    latitude: Mapped[Decimal] = mapped_column(Numeric(10, 7))
    longitude: Mapped[Decimal] = mapped_column(Numeric(10, 7))
    furnishing: Mapped[Furnishing] = enum_column(Furnishing)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    baseline_photos: Mapped[list["BaselinePhoto"]] = relationship(back_populates="property")


class BaselinePhoto(Base):
    __tablename__ = "baseline_photos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(Text)
    phash: Mapped[str] = mapped_column(String(16), index=True)
    room_type: Mapped[str] = mapped_column(String(50), default="general")
    captured_at: Mapped[datetime] = mapped_column(default=utcnow)

    property: Mapped[Property] = relationship(back_populates="baseline_photos")


class Mandate(Base):
    """The owner's permission for one broker to list one flat, with the limits they agreed."""

    __tablename__ = "mandates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"))
    broker_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    min_rent: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    max_rent: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    max_deposit: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    max_brokerage_months: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("1.00"))
    valid_until: Mapped[datetime]
    status: Mapped[MandateStatus] = enum_column(MandateStatus, default=MandateStatus.ACTIVE)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Listing(Base):
    """One broker's ad for one flat. Its content lives in versions; each edit is a new version."""

    __tablename__ = "listings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"))
    broker_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    mandate_id: Mapped[str] = mapped_column(ForeignKey("mandates.id"))
    status: Mapped[ListingStatus] = enum_column(ListingStatus, default=ListingStatus.DRAFT)
    last_confirmed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    versions: Mapped[list["ListingVersion"]] = relationship(
        back_populates="listing", order_by="ListingVersion.version_no"
    )


class ListingVersion(Base):
    __tablename__ = "listing_versions"
    __table_args__ = (UniqueConstraint("listing_id", "version_no"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    version_no: Mapped[int]
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    rent: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    deposit: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    brokerage_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    furnishing: Mapped[Furnishing] = enum_column(Furnishing)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    listing: Mapped[Listing] = relationship(back_populates="versions")
    photos: Mapped[list["ListingPhoto"]] = relationship(back_populates="version")
    commute_claims: Mapped[list["CommuteClaim"]] = relationship(back_populates="version")


class ListingPhoto(Base):
    """A broker's photo. It belongs to the listing; every submit audits the listing's current
    (not removed) photos. Removing a photo hides it from later audits but keeps it as evidence."""

    __tablename__ = "listing_photos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    # The first version audited with this photo; empty until the listing is submitted.
    version_id: Mapped[str | None] = mapped_column(ForeignKey("listing_versions.id", ondelete="CASCADE"))
    kind: Mapped[PhotoKind] = enum_column(PhotoKind)
    storage_path: Mapped[str] = mapped_column(Text)
    phash: Mapped[str] = mapped_column(String(16), index=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    gps_source: Mapped[str | None] = mapped_column(String(10))  # "device" (sent with the upload) or "exif"
    captured_at: Mapped[datetime | None]
    sequence: Mapped[int] = mapped_column(default=0)
    removed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    version: Mapped[ListingVersion | None] = relationship(back_populates="photos")


class CommuteClaim(Base):
    __tablename__ = "commute_claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("listing_versions.id", ondelete="CASCADE"))
    destination: Mapped[str] = mapped_column(String(255))
    minutes: Mapped[int]
    mode: Mapped[CommuteMode] = enum_column(CommuteMode)
    source: Mapped[ClaimSource] = enum_column(ClaimSource, default=ClaimSource.FORM)

    version: Mapped[ListingVersion] = relationship(back_populates="commute_claims")


class Audit(Base):
    """The result of auditing one listing version. report_json is the exact report shown to users."""

    __tablename__ = "audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("listing_versions.id"), unique=True)
    status: Mapped[AuditStatus] = enum_column(AuditStatus)
    report_json: Mapped[str] = mapped_column(Text)
    ruleset_version: Mapped[str] = mapped_column(String(20))
    signature: Mapped[str | None] = mapped_column(String(64))
    paypal_product_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class OutboxJob(Base):
    """Work to do after a record is safely saved (create the PayPal product, draft or send an
    invoice, submit dispute evidence), retried until done. Mint jobs point at an audit; Feature 4
    jobs point at their invoice or dispute through ref_id."""

    __tablename__ = "outbox_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    audit_id: Mapped[str | None] = mapped_column(ForeignKey("audits.id"))
    ref_id: Mapped[str | None] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[JobStatus] = enum_column(JobStatus, default=JobStatus.PENDING)
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_run_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TrustEvent(Base):
    __tablename__ = "trust_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    broker_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(40))
    points: Mapped[int]
    audit_id: Mapped[str | None] = mapped_column(ForeignKey("audits.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


# --- Feature 4: closing, ledger, invoices, webhooks, disputes ---


class ClosingStatus(StrEnum):
    OPEN = "OPEN"
    INVOICED = "INVOICED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"
    DISPUTED = "DISPUTED"


class LedgerDirection(StrEnum):
    IN = "in"  # money received from the tenant
    OUT = "out"  # money returned to the tenant
    INFO = "info"  # an event with an amount that moves no money (a hold, a draft, a reminder)


class LedgerSource(StrEnum):
    FEATURE3 = "feature3"
    WEBHOOK = "webhook"
    API = "api"
    MANUAL = "manual"


class InvoiceStatus(StrEnum):
    PENDING = "PENDING"  # saved here, not yet created in PayPal
    DRAFT = "DRAFT"
    SENT = "SENT"
    PAID = "PAID"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class DisputeStatus(StrEnum):
    OPEN = "OPEN"
    APPROVED = "APPROVED"  # a manager approved the response; the outbox submits it
    SUBMITTED = "SUBMITTED"
    RESOLVED = "RESOLVED"


class Closing(Base):
    """One locked flat: ties the tenant's money to the verified listing version it was agreed on."""

    __tablename__ = "closings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("listing_versions.id"))
    audit_id: Mapped[str] = mapped_column(ForeignKey("audits.id"))
    broker_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    tenant_name: Mapped[str] = mapped_column(String(150))
    tenant_email: Mapped[str] = mapped_column(String(255))
    # The Feature 3 capture whose token is deducted; unique so one token pays one closing.
    capture_ref: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[ClosingStatus] = enum_column(ClosingStatus, default=ClosingStatus.OPEN)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class LedgerEntry(Base):
    """Append-only: no code path updates or deletes a row (enforced below); corrections are new
    entries. Balances are always summed from these rows, never stored."""

    __tablename__ = "ledger_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id"), index=True)
    closing_id: Mapped[str | None] = mapped_column(ForeignKey("closings.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    direction: Mapped[LedgerDirection] = enum_column(LedgerDirection)
    paypal_ref: Mapped[str | None] = mapped_column(String(100), index=True)
    # The same event recorded twice (a retried job, a replayed webhook) hits this and is a no-op.
    idempotency_key: Mapped[str] = mapped_column(String(150), unique=True)
    occurred_at: Mapped[datetime] = mapped_column(default=utcnow)
    source: Mapped[LedgerSource] = enum_column(LedgerSource)
    note: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Invoice(Base):
    """The invoice we ask PayPal to send. Its body is HMAC-signed like an audit."""

    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    closing_id: Mapped[str] = mapped_column(ForeignKey("closings.id"), unique=True)
    paypal_invoice_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    number: Mapped[str] = mapped_column(String(50), unique=True)
    items_json: Mapped[str] = mapped_column(Text)
    deposit: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    brokerage: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    token_deduction: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[InvoiceStatus] = enum_column(InvoiceStatus, default=InvoiceStatus.PENDING)
    due_date: Mapped[datetime]
    payer_view_url: Mapped[str | None] = mapped_column(Text)
    override_note: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None]
    paid_at: Mapped[datetime | None]
    reminders_sent: Mapped[int] = mapped_column(default=0)
    signature: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class WebhookEvent(Base):
    """Every webhook we received, verified or not, so a replay is recognised and nothing is lost."""

    __tablename__ = "webhook_events"

    paypal_event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80))
    verified: Mapped[bool] = mapped_column(default=False)
    raw_json: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(default=utcnow)
    processed_at: Mapped[datetime | None]
    error: Mapped[str | None] = mapped_column(Text)


class Dispute(Base):
    """A PayPal dispute and our response. Nothing is sent to PayPal without approved_by."""

    __tablename__ = "disputes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paypal_dispute_id: Mapped[str] = mapped_column(String(100), unique=True)
    closing_id: Mapped[str | None] = mapped_column(ForeignKey("closings.id"), index=True)
    reason: Mapped[str] = mapped_column(String(80))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[DisputeStatus] = enum_column(DisputeStatus, default=DisputeStatus.OPEN)
    evidence_pack_json: Mapped[str | None] = mapped_column(Text)
    draft_response: Mapped[str | None] = mapped_column(Text)
    draft_source: Mapped[str | None] = mapped_column(String(80))
    final_response: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class LedgerIsAppendOnly(Exception):
    pass


@event.listens_for(LedgerEntry, "before_update")
def _no_ledger_updates(_mapper, _connection, target):
    raise LedgerIsAppendOnly(f"ledger entry {target.id} cannot be changed; record a correcting entry")


@event.listens_for(LedgerEntry, "before_delete")
def _no_ledger_deletes(_mapper, _connection, target):
    raise LedgerIsAppendOnly(f"ledger entry {target.id} cannot be deleted; record a correcting entry")
