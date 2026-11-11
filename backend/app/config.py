"""Application settings, read from environment variables or backend/.env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    # Browser origins allowed to call the API (comma-separated in .env). The dev frontend
    # proxies through Vite and needs none; a deployed frontend needs its own origin here.
    cors_origins: str = "http://localhost:5173"

    # An empty model name means "not configured": the adapter reports UNVERIFIABLE instead of guessing.
    groq_api_key: str | None = None
    groq_text_model: str = ""
    groq_vision_model: str = ""

    google_maps_api_key: str | None = None
    # "auto": Google if a key is set, else the demo table in DEMO_MODE, else no routes.
    # "osm": OpenStreetMap (free, no key; no live traffic, no transit).
    maps_provider: Literal["auto", "osm"] = "auto"

    paypal_client_id: str | None = None
    paypal_client_secret: str | None = None
    paypal_api_base: str = "https://api-m.sandbox.paypal.com"
    # Feature 4: the webhook id PayPal shows for our listener; without it no webhook is trusted.
    paypal_webhook_id: str | None = None
    # USD in the sandbox: PayPal has no domestic INR between Indian accounts (₹ is shown as framing).
    invoice_currency: str = "USD"
    invoice_due_days: int = 7
    # Reminder days relative to the due date; at most three are ever sent.
    invoice_reminder_offsets: str = "-1,1,3"

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
