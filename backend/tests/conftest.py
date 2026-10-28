import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

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
def client(db_session) -> TestClient:
    """The real app, but every request uses this test's database."""
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db_session
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
