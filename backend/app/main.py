"""FastAPI entry point. Run with: uvicorn app.main:app --reload"""

from fastapi import FastAPI

from app.config import get_settings


def create_app() -> FastAPI:
    app = FastAPI(title="PropLock AI - Feature 1: Listing Audit", version="0.1.0")

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "env": get_settings().app_env}

    return app


app = create_app()
