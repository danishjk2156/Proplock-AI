"""Image helpers: validate uploads, perceptual hash, EXIF GPS/time, distance, and vision-ready copies."""

import base64
import hashlib
import io
import math
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import imagehash
from PIL import Image, ImageOps, UnidentifiedImageError

from app.domain.commute_rules import IST

# Refuse "decompression bombs": a tiny file that expands to billions of pixels in memory.
Image.MAX_IMAGE_PIXELS = 40_000_000
MAX_BYTES = 8 * 1024 * 1024
FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
# pHashes this close (out of 64 bits) are the same picture, even resized or recompressed.
DUPLICATE_MAX_BITS = 6

GPS_IFD, EXIF_IFD = 0x8825, 0x8769
DATETIME_ORIGINAL, DATETIME = 36867, 306


class ImageRejected(ValueError):
    pass


@dataclass(frozen=True)
class ImageInfo:
    format: str
    phash: str
    gps: tuple[Decimal, Decimal] | None
    taken_at: datetime | None


def _dms_to_degrees(dms, ref: str) -> Decimal:
    degrees, minutes, seconds = (float(x) for x in dms)
    value = degrees + minutes / 60 + seconds / 3600
    return Decimal(f"{-value if ref in ('S', 'W') else value:.7f}")


def _exif_gps(exif) -> tuple[Decimal, Decimal] | None:
    gps = exif.get_ifd(GPS_IFD)
    try:
        return _dms_to_degrees(gps[2], gps[1]), _dms_to_degrees(gps[4], gps[3])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def _exif_time(exif) -> datetime | None:
    raw = exif.get_ifd(EXIF_IFD).get(DATETIME_ORIGINAL) or exif.get(DATETIME)
    try:
        # EXIF time has no timezone; phones in India record local time, so read it as IST.
        return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S").replace(tzinfo=IST).astimezone(UTC)
    except ValueError:
        return None


def inspect_upload(data: bytes) -> ImageInfo:
    if len(data) > MAX_BYTES:
        raise ImageRejected(f"larger than {MAX_BYTES // (1024 * 1024)} MB")
    try:
        # Pillow only raises above 2x MAX_IMAGE_PIXELS (it just warns in between), so check ourselves.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:  # reads only the header
                if probe.width * probe.height > Image.MAX_IMAGE_PIXELS:
                    raise ImageRejected(f"{probe.width}x{probe.height} is too many pixels")
                probe.verify()  # structural check; the image must be reopened after this
        with Image.open(io.BytesIO(data)) as img:
            if img.format not in FORMATS:
                raise ImageRejected(f"format {img.format} not allowed (use JPEG, PNG or WEBP)")
            exif = img.getexif()
            img.load()
            return ImageInfo(
                format=img.format,
                phash=str(imagehash.phash(ImageOps.exif_transpose(img))),
                gps=_exif_gps(exif),
                taken_at=_exif_time(exif),
            )
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as e:
        raise ImageRejected(f"not a readable image ({type(e).__name__})") from e


def save(data: bytes, image_format: str, upload_dir: Path) -> str:
    """Store by content hash, so the same file uploaded twice is stored once."""
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / f"{hashlib.sha256(data).hexdigest()}.{FORMATS[image_format]}"
    if not path.exists():
        path.write_bytes(data)
    return str(path)


def hamming(a: str, b: str) -> int:
    return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)


def haversine_m(lat1: Decimal, lon1: Decimal, lat2: Decimal, lon2: Decimal) -> float:
    """Distance in metres along the Earth's surface."""
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp, dl = p2 - p1, math.radians(float(lon2) - float(lon1))
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def vision_data_url(path: str, max_side: int = 1024) -> str:
    """A small JPEG copy as a data: URL, well under Groq's request size limit."""
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
