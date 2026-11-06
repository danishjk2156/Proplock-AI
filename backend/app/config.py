"""Application settings, read from environment variables or backend/.env."""

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEV_AUDIT_SECRET = "dev-only-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite:///./proplock.db"
    audit_secret: str = DEV_AUDIT_SECRET
    demo_mode: bool = False
    upload_dir: str = "uploads"  # photos are stored here, named by content hash

    # An empty model name means "not configured": the adapter reports UNVERIFIABLE instead of guessing.
    groq_api_key: str | None = None
    groq_text_model: str = ""
    groq_vision_model: str = ""

    google_maps_api_key: str | None = None

    paypal_client_id: str | None = None
    paypal_client_secret: str | None = None
    paypal_api_base: str = "https://api-m.sandbox.paypal.com"

    @model_validator(mode="after")
    def _real_secret_outside_development(self) -> "Settings":
        # Audit signatures are only as secret as this key; the default is public in the repo.
        if self.app_env == "production" and self.audit_secret == DEV_AUDIT_SECRET:
            raise ValueError("Set AUDIT_SECRET to a long random value before running in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_upload_dir() -> Path:
    """FastAPI dependency, so tests can store photos in a temporary folder."""
    return Path(get_settings().upload_dir)
