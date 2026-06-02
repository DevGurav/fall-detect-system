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
from app.routers import health, inference
from app.services.detector import CloudDetector


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.detector = CloudDetector(settings)  # built once, reused
    yield
    # (nothing to tear down yet — model handles are GC'd)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Fall Guardian — Cloud Gateway",
        version=__version__,
        lifespan=_lifespan,
    )
    app.include_router(health.router)
    app.include_router(inference.router)
    return app


app = create_app()
