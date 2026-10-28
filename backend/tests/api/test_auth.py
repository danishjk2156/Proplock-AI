from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import hash_key, require_role
from app.db.base import get_session
from app.db.models import Role, User


def test_missing_key_is_401(client):
    assert client.get("/me").status_code == 401


def test_wrong_key_is_401(client):
    assert client.get("/me", headers={"X-API-Key": "pl_broker_not-a-real-key"}).status_code == 401


def test_valid_key_identifies_the_user(client, make_user):
    user, key = make_user(Role.BROKER, name="Rajesh")

    response = client.get("/me", headers={"X-API-Key": key})

    assert response.status_code == 200
    assert response.json()["id"] == user.id
    assert response.json()["role"] == "BROKER"


def test_only_the_hash_of_the_key_is_stored(make_user):
    user, key = make_user(Role.MANAGER)

    assert key not in user.api_key_hash
    assert user.api_key_hash == hash_key(key)


def test_wrong_role_is_403(db_session, make_user):
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: db_session

    @app.get("/manager-only")
    def manager_only(user: User = Depends(require_role(Role.MANAGER))):
        return {"ok": True}

    _, broker_key = make_user(Role.BROKER)
    _, manager_key = make_user(Role.MANAGER)
    client = TestClient(app)

    assert client.get("/manager-only", headers={"X-API-Key": broker_key}).status_code == 403
    assert client.get("/manager-only", headers={"X-API-Key": manager_key}).status_code == 200
