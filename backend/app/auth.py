"""API-key authentication and role checks.

Each user has one key, sent as the `X-API-Key` header. The database stores only its
SHA-256, so we hash the incoming key and look the hash up.
"""

import hashlib
import secrets
from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_session
from app.db.models import Role, User

# Declaring the header this way also adds an "Authorize" button to /docs.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_key(role: Role) -> str:
    return f"pl_{role.value.lower()}_{secrets.token_urlsafe(24)}"


def current_user(
    api_key: str | None = Depends(api_key_header),
    session: Session = Depends(get_session),
) -> User:
    if not api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-API-Key header")
    user = session.scalar(select(User).where(User.api_key_hash == hash_key(api_key)))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
    return user


def require_role(*roles: Role) -> Callable[..., User]:
    """401 = we don't know who you are. 403 = we know, but you're not allowed."""

    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role: {', '.join(r.value for r in roles)}")
        return user

    return dependency
