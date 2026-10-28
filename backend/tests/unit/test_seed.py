from sqlalchemy import func, select

from app.auth import hash_key
from app.db.models import Mandate, Property, Role, User
from app.db.seed import seed


def test_seed_creates_demo_data_with_working_keys(db_session):
    keys = seed(db_session)

    assert set(keys) == {"manager", "broker_rajesh", "broker_quickcash"}
    assert db_session.scalar(select(func.count()).select_from(Property)) == 2
    assert db_session.scalar(select(func.count()).select_from(Mandate)) == 3
    manager = db_session.scalar(select(User).where(User.api_key_hash == hash_key(keys["manager"])))
    assert manager.role is Role.MANAGER


def test_seed_twice_does_nothing_the_second_time(db_session):
    seed(db_session)

    assert seed(db_session) is None
    assert db_session.scalar(select(func.count()).select_from(User)) == 3
