"""FastAPI application factory.

`create_app()` wires settings → detector → routers and stashes the long-lived
detector on `app.state` so it's built once (model load is expensive) and shared
across requests. `uvicorn app.main:app` serves it.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.config import get_settings
from app.db import Database
from app.routers import health, inference, retraining
from app.services.detector import CloudDetector
from app.services.retraining_store import RetrainingStore


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.db = Database.from_settings(settings)  # None when no DSN (DB-less mode)
    app.state.detector = CloudDetector(settings)  # built once, reused
    app.state.retraining_store = RetrainingStore(settings, app.state.db)  # built once, reused
    yield
    if app.state.db is not None:
        await app.state.db.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Fall Guardian — Cloud Gateway",
        version=__version__,
        lifespan=_lifespan,
    )
    app.include_router(health.router)
    app.include_router(inference.router)
    app.include_router(retraining.router)
    return app


app = create_app()
