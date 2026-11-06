"""Photo uploads (broker listing photos, manager baseline photos) and the facts Check A judges.

Files are validated and stored by content hash under the upload folder. Distances and reuse
across flats are measured here, at submit time, so a photo stolen later is still caught.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import images
from app.db.models import BaselinePhoto, Listing, ListingPhoto, PhotoKind, Property
from app.domain.photo_rules import PhotoInput

MAX_PHOTOS_PER_LISTING = 20


class PhotoRejected(ValueError):
    """The upload breaks a rule (file type, size, count, coordinates). Shown to the caller as 422."""


@dataclass(frozen=True)
class Upload:
    filename: str
    data: bytes


def _checked(uploads: list[Upload]) -> list[tuple[Upload, images.ImageInfo]]:
    if not uploads:
        raise PhotoRejected("send at least one image")
    checked = []
    for upload in uploads:
        try:
            checked.append((upload, images.inspect_upload(upload.data)))
        except images.ImageRejected as e:
            raise PhotoRejected(f"{upload.filename}: {e}") from e
    return checked


def add_listing_photos(
    session: Session,
    listing: Listing,
    uploads: list[Upload],
    kind: PhotoKind,
    device_gps: tuple[Decimal, Decimal] | None,
    upload_dir: Path,
) -> list[ListingPhoto]:
    """device_gps is the browser's location sent with the upload; EXIF GPS is the backup."""
    active = session.scalars(
        select(ListingPhoto).where(ListingPhoto.listing_id == listing.id, ListingPhoto.removed_at.is_(None))
    ).all()
    if len(active) + len(uploads) > MAX_PHOTOS_PER_LISTING:
        raise PhotoRejected(f"a listing can have at most {MAX_PHOTOS_PER_LISTING} photos ({len(active)} already)")

    now = datetime.now(UTC)
    photos = []
    for n, (upload, info) in enumerate(_checked(uploads), start=len(active)):
        gps, source = (device_gps, "device") if device_gps else (info.gps, "exif" if info.gps else None)
        photo = ListingPhoto(
            listing_id=listing.id,
            kind=kind,
            storage_path=images.save(upload.data, info.format, upload_dir),
            phash=info.phash,
            latitude=gps[0] if gps else None,
            longitude=gps[1] if gps else None,
            gps_source=source,
            captured_at=info.taken_at or now,
            sequence=n,
        )
        photos.append(photo)
    session.add_all(photos)
    session.commit()
    return photos


def remove_listing_photo(session: Session, photo: ListingPhoto) -> None:
    """Hide from later audits; the row and file stay as evidence for audits that used them."""
    photo.removed_at = datetime.now(UTC)
    session.commit()


def add_baseline_photos(
    session: Session, prop: Property, uploads: list[Upload], room_type: str, upload_dir: Path
) -> list[BaselinePhoto]:
    photos = [
        BaselinePhoto(
            property_id=prop.id,
            url=images.save(upload.data, info.format, upload_dir),
            phash=info.phash,
            room_type=room_type,
        )
        for upload, info in _checked(uploads)
    ]
    session.add_all(photos)
    session.commit()
    return photos


def _hashes_of_other_flats(session: Session, property_id: str) -> list[tuple[str, str]]:
    listing_rows = session.execute(
        select(ListingPhoto.phash, Listing.property_id)
        .join(Listing, ListingPhoto.listing_id == Listing.id)
        .where(Listing.property_id != property_id)
    ).all()
    baseline_rows = session.execute(
        select(BaselinePhoto.phash, BaselinePhoto.property_id).where(BaselinePhoto.property_id != property_id)
    ).all()
    return [(h, p) for h, p in [*listing_rows, *baseline_rows]]


def photo_inputs(session: Session, listing: Listing, prop: Property) -> list[PhotoInput]:
    """The listing's current photos, measured against the flat and against every other flat."""
    photos = session.scalars(
        select(ListingPhoto)
        .where(ListingPhoto.listing_id == listing.id, ListingPhoto.removed_at.is_(None))
        .order_by(ListingPhoto.sequence)
    ).all()
    # Linear scan: fine for a demo-sized database; a BK-tree or pgvector index would replace it at scale.
    others = _hashes_of_other_flats(session, prop.id)
    inputs = []
    for photo in photos:
        distance = None
        if photo.latitude is not None and photo.longitude is not None:
            distance = images.haversine_m(photo.latitude, photo.longitude, prop.latitude, prop.longitude)
        duplicate = next(
            (f"a photo of {pid}" for h, pid in others if images.hamming(photo.phash, h) <= images.DUPLICATE_MAX_BITS),
            None,
        )
        inputs.append(
            PhotoInput(photo.id, photo.kind, photo.storage_path, photo.phash, distance, photo.captured_at, duplicate)
        )
    return inputs


def baseline_paths(session: Session, prop: Property) -> list[str]:
    return list(
        session.scalars(
            select(BaselinePhoto.url).where(BaselinePhoto.property_id == prop.id).order_by(BaselinePhoto.captured_at)
        )
    )
