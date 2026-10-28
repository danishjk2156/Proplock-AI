from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.auth import current_user
from app.db.models import Role, User

router = APIRouter(tags=["auth"])


class MeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # build from the ORM object

    id: str
    name: str
    role: Role
    agency: str | None
    trust_score: int


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(current_user)) -> User:
    """Who am I? Useful to check which API key Swagger is using."""
    return user
