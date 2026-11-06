"""FastAPI entry point. Run with: uvicorn app.main:app --reload"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import audits, broker, manager, me, public
from app.config import get_settings


def create_app() -> FastAPI:
    app = FastAPI(title="PropLock AI - Feature 1: Listing Audit", version="0.1.0")
    origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,  # never "*": keys travel in a header any page could send
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["X-API-Key", "Content-Type"],
    )

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "env": get_settings().app_env}

    app.include_router(me.router)
    app.include_router(manager.router)
    app.include_router(broker.router)
    app.include_router(audits.router)
    app.include_router(public.router)
    return app


app = create_app()
