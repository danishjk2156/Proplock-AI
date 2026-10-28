import pytest
from sqlalchemy.orm import Session

from app.db import models  # noqa: F401  (registers tables)
from app.db.base import Base, make_engine


@pytest.fixture
def db_session(tmp_path) -> Session:
    """A fresh SQLite database file per test, with all tables created."""
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()
