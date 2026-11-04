import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.llm import FakeLLM, get_llm
from app.adapters.maps import FakeMaps, get_maps
from app.auth import generate_key, hash_key
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
def client(db_session, fake_llm, fake_maps) -> TestClient:
    """The real app, but every request uses this test's database and never calls Groq or Google."""
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_llm] = lambda: fake_llm
    app.dependency_overrides[get_maps] = lambda: fake_maps
    return TestClient(app)


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
