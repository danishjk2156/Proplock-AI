import io
import random

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.llm import FakeLLM, FakeVision, get_llm, get_vision
from app.adapters.maps import FakeMaps, get_maps
from app.adapters.paypal import FakePayPal, get_paypal
from app.auth import generate_key, hash_key
from app.config import get_upload_dir
from app.db import models  # noqa: F401  (registers tables)
from app.db.base import Base, get_session, make_engine
from app.db.models import Role, User
from app.main import create_app


@pytest.fixture
def db_session(tmp_path) -> Session:
    """A fresh SQLite database file per test, with all tables created."""
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


@pytest.fixture
def fake_llm() -> FakeLLM:
    """Finds no claims in the ad text unless a test sets fake_llm.claims or fake_llm.error."""
    return FakeLLM()


@pytest.fixture
def fake_maps() -> FakeMaps:
    """Knows no routes (every claim is UNVERIFIABLE) unless a test fills fake_maps.routes."""
    return FakeMaps()


@pytest.fixture
def fake_vision() -> FakeVision:
    """No inspection set: extra photos are UNVERIFIABLE unless a test sets fake_vision.inspection."""
    return FakeVision()


@pytest.fixture
def fake_paypal() -> FakePayPal:
    """An in-memory catalog that accepts every product unless a test sets fail_times."""
    return FakePayPal()


@pytest.fixture
def client(db_session, fake_llm, fake_maps, fake_vision, fake_paypal, tmp_path) -> TestClient:
    """The real app, but with this test's database and upload folder, and no Groq, Google or PayPal calls."""
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_llm] = lambda: fake_llm
    app.dependency_overrides[get_maps] = lambda: fake_maps
    app.dependency_overrides[get_vision] = lambda: fake_vision
    app.dependency_overrides[get_paypal] = lambda: fake_paypal
    app.dependency_overrides[get_upload_dir] = lambda: tmp_path / "uploads"
    return TestClient(app)


# Seeded flats' verified pins.
BLR_PIN = ("12.9718910", "77.6411510")
CHN_PIN = ("12.9431870", "80.2374820")


def _dms(value: float):
    value = abs(value)
    d = int(value)
    m = int((value - d) * 60)
    return (d, m, round(((value - d) * 60 - m) * 60, 4))


def photo_bytes(seed: int, fmt: str = "JPEG", gps: tuple[float, float] | None = None, taken: str | None = None) -> bytes:
    """A noise image: each seed gives a different perceptual hash. Optional EXIF GPS and time."""
    rng = random.Random(seed)
    img = Image.frombytes("L", (32, 32), bytes(rng.randrange(256) for _ in range(32 * 32)))
    img = img.resize((320, 320)).convert("RGB")
    exif = Image.Exif()
    if gps:
        gps_ifd = exif.get_ifd(0x8825)
        gps_ifd.update({1: "N" if gps[0] >= 0 else "S", 2: _dms(gps[0]), 3: "E" if gps[1] >= 0 else "W", 4: _dms(gps[1])})
    if taken:
        exif.get_ifd(0x8769)[36867] = taken
    buf = io.BytesIO()
    img.save(buf, format=fmt, exif=exif.tobytes()) if (gps or taken) else img.save(buf, format=fmt)
    return buf.getvalue()


def upload_photo(client, headers, listing_id, seed=1, kind="CHECKIN", pin=BLR_PIN, **photo_kwargs):
    data = {"kind": kind}
    if pin:
        data.update(latitude=pin[0], longitude=pin[1])
    files = [("files", (f"p{seed}.jpg", photo_bytes(seed, **photo_kwargs), "image/jpeg"))]
    return client.post(f"/broker/listings/{listing_id}/photos", data=data, files=files, headers=headers)


@pytest.fixture
def make_user(db_session):
    """Create a user and return (user, plain API key)."""

    def _make(role: Role, name: str = "Test User") -> tuple[User, str]:
        key = generate_key(role)
        user = User(name=name, email=f"{key[-8:]}@test.local", role=role, api_key_hash=hash_key(key))
        db_session.add(user)
        db_session.commit()
        return user, key

    return _make
