import io
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from PIL import Image

from app.adapters import images
from app.adapters.images import ImageRejected, inspect_upload
from tests.conftest import photo_bytes


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_allowed_formats_are_read_and_hashed(fmt):
    info = inspect_upload(photo_bytes(1, fmt=fmt))

    assert info.format == fmt
    assert len(info.phash) == 16


def test_other_formats_are_rejected():
    buf = io.BytesIO()
    Image.new("RGB", (64, 64)).save(buf, format="GIF")

    with pytest.raises(ImageRejected, match="format GIF not allowed"):
        inspect_upload(buf.getvalue())


def test_garbage_is_rejected():
    with pytest.raises(ImageRejected, match="not a readable image"):
        inspect_upload(b"definitely not an image")


def test_files_over_8_mb_are_rejected():
    with pytest.raises(ImageRejected, match="larger than 8 MB"):
        inspect_upload(b"\0" * (images.MAX_BYTES + 1))


def test_decompression_bombs_are_rejected():
    # A small file that would expand to 49 million pixels in memory.
    buf = io.BytesIO()
    Image.new("1", (7000, 7000)).save(buf, format="PNG")
    assert len(buf.getvalue()) < images.MAX_BYTES

    with pytest.raises(ImageRejected, match="too many pixels"):
        inspect_upload(buf.getvalue())


def test_exif_gps_and_time_are_read():
    info = inspect_upload(photo_bytes(1, gps=(12.971891, 77.641151), taken="2026:11:04 15:30:00"))

    lat, lng = info.gps
    assert abs(lat - Decimal("12.971891")) < Decimal("0.00001")
    assert abs(lng - Decimal("77.641151")) < Decimal("0.00001")
    assert info.taken_at == datetime(2026, 11, 4, 10, 0, tzinfo=UTC)  # 15:30 IST


def test_southern_and_western_coordinates_are_negative():
    lat, lng = inspect_upload(photo_bytes(1, gps=(-33.8688, -70.6693))).gps

    assert lat < 0 and lng < 0


def test_photo_without_exif_has_no_gps_or_time():
    info = inspect_upload(photo_bytes(1))

    assert (info.gps, info.taken_at) == (None, None)


def test_recompressed_copy_is_a_duplicate_but_another_photo_is_not():
    original = photo_bytes(1)
    with Image.open(io.BytesIO(original)) as img:
        buf = io.BytesIO()
        img.resize((200, 200)).save(buf, format="JPEG", quality=40)

    a, b = inspect_upload(original).phash, inspect_upload(buf.getvalue()).phash
    other = inspect_upload(photo_bytes(2)).phash

    assert images.hamming(a, b) <= images.DUPLICATE_MAX_BITS
    assert images.hamming(a, other) > images.DUPLICATE_MAX_BITS


def test_haversine_distances():
    pin = (Decimal("12.9718910"), Decimal("77.6411510"))

    assert images.haversine_m(*pin, *pin) == 0
    # 0.001 degrees of latitude is about 111 m everywhere.
    assert 110 < images.haversine_m(*pin, pin[0] + Decimal("0.001"), pin[1]) < 112
    # Indiranagar to Chennai OMR is roughly 290 km in a straight line.
    assert 280_000 < images.haversine_m(*pin, Decimal("12.9431870"), Decimal("80.2374820")) < 300_000


def test_save_names_files_by_content(tmp_path):
    data = photo_bytes(1)

    first = images.save(data, "JPEG", tmp_path)
    second = images.save(data, "JPEG", tmp_path)

    assert first == second
    assert first.endswith(".jpg")
    assert len(list(tmp_path.iterdir())) == 1


def test_vision_copy_is_a_small_jpeg_data_url(tmp_path):
    path = tmp_path / "big.png"
    Image.new("RGB", (3000, 2000), "white").save(path)

    url = images.vision_data_url(str(path))

    assert url.startswith("data:image/jpeg;base64,")
    assert len(url) < 200_000
