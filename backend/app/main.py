"""FastAPI application factory.

`create_app()` wires settings → db → detector + services → routers, stashing the
long-lived objects on `app.state` so they're built once (model load is expensive,
the DB engine holds a pool) and shared across requests. `uvicorn app.main:app`
serves it.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.broker import EventBroker
from app.config import get_settings
from app.db import Database
from app.observability import TraceIDMiddleware, configure_logging
from app.ratelimit import RateLimiter
from app.routers import auth, contacts, devices, emergency, events, health, inference, retraining, users
from app.services.audit_service import AuditService
from app.services.calibration_store import CalibrationStore
from app.services.detector import CloudDetector
from app.services.device_service import DeviceService
from app.services.event_store import EventStore
from app.services.fcm_service import FcmService
from app.services.pairing_service import PairingService
from app.services.refresh_token_service import RefreshTokenService
from app.services.retraining_store import RetrainingStore
from app.services.user_service import UserService


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.db = Database.from_settings(settings)  # None when no DSN (DB-less mode)
    redis = None
    if settings.redis_url:
        from redis.asyncio import from_url

        redis = from_url(settings.redis_url, decode_responses=True)
    app.state.redis = redis  # None when no FG_REDIS_URL (rate limiting becomes a no-op)
    app.state.rate_limiter = RateLimiter(redis)
    app.state.event_broker = EventBroker(redis)
    app.state.detector = CloudDetector(settings)  # built once, reused
    app.state.calibration_store = CalibrationStore(settings, app.state.db)
    app.state.retraining_store = RetrainingStore(settings, app.state.db)
    app.state.fcm_service = FcmService(settings.firebase_credentials_json)
    app.state.event_store = EventStore(
        settings, app.state.db, app.state.event_broker, app.state.fcm_service
    )
    app.state.device_service = DeviceService(settings, app.state.db)
    app.state.user_service = UserService(settings, app.state.db)
    app.state.pairing_service = PairingService(settings, app.state.db)
    app.state.refresh_token_service = RefreshTokenService(settings, app.state.db)
    app.state.audit_service = AuditService(app.state.db)
    yield
    if app.state.db is not None:
        await app.state.db.dispose()
    if app.state.redis is not None:
        await app.state.redis.aclose()


def create_app() -> FastAPI:
    configure_logging(get_settings())  # JSON logging + per-request trace_id (Phase 32)
    app = FastAPI(
        title="Fall Guardian — Cloud Gateway",
        version=__version__,
        lifespan=_lifespan,
    )
    app.add_middleware(TraceIDMiddleware)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(inference.router)
    app.include_router(retraining.router)
    app.include_router(events.router)
    app.include_router(devices.router)
    app.include_router(users.router)
    app.include_router(emergency.router)
    app.include_router(contacts.router)
    return app


app = create_app()
