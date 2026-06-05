"""Request-scoped FastAPI dependencies."""
from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.broker import EventBroker
from app.db import Database


def require_db(request: Request) -> Database:
    """Gate endpoints that need persistence; returns 503 in DB-less mode.

    Used by the telemetry + read-side routes, which have nothing to serve without
    a database. (The /v1/inference and /v1/retraining ingestion paths deliberately
    do NOT use this — they degrade to stub/no-op so ingestion stays available.)
    """
    db: Database | None = request.app.state.db
    if db is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "persistence is not configured (set FG_DATABASE_URL)",
        )
    return db


def require_broker(request: Request) -> EventBroker:
    """Gate the live SSE feed; returns 503 without Redis.

    The broker is always present on `app.state`, but it's a no-op publisher with
    no pub/sub backplane when Redis isn't configured — so a stream would never
    deliver. Like `require_db`, this fails fast rather than hanging open a feed
    that can't push.
    """
    broker: EventBroker = request.app.state.event_broker
    if broker.is_stub:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "the live event feed is not configured (set FG_REDIS_URL)",
        )
    return broker
