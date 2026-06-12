"""Health + readiness endpoints (Phase 32).

`GET /health` is the **liveness / startup** probe: it returns 200 as soon as the
process is up and the app object is wired, with no dependency I/O. Fly.io's
`grace_period` plus this cheap check is what tells the platform the machine booted.

`GET /health/ready` is the **readiness** probe: it actually exercises the
configured dependencies (Postgres, Redis) and reports the loaded model. Optional
infra that isn't configured is reported as "skipped" (still ready); a configured
dependency that fails to answer flips the response to 503 + "degraded" so the load
balancer stops routing to a machine that can't serve.
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from app import __version__
from app.schemas import HealthResponse, ReadinessCheck, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Liveness / startup probe — no dependency I/O."""
    settings = request.app.state.settings
    return HealthResponse(
        status="ok",
        version=__version__,
        model_version=request.app.state.detector.model_version,  # real model or stub
        environment=settings.environment,
    )


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness(request: Request, response: Response) -> ReadinessResponse:
    """Readiness probe — verifies configured dependencies before accepting traffic."""
    app_state = request.app.state
    checks = [
        await _check_database(app_state),
        await _check_redis(app_state),
        _check_model(app_state),
    ]
    ready = all(c.status != "error" for c in checks)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if ready else "degraded",
        version=__version__,
        checks=checks,
    )


async def _check_database(app_state) -> ReadinessCheck:
    db = app_state.db
    if db is None:
        return ReadinessCheck(name="database", status="skipped", detail="FG_DATABASE_URL unset")
    try:
        async with db.session_for(None) as session:
            await session.execute(text("SELECT 1"))
        return ReadinessCheck(name="database", status="ok")
    except Exception as exc:  # surface the failing dependency, don't crash the probe
        return ReadinessCheck(name="database", status="error", detail=str(exc))


async def _check_redis(app_state) -> ReadinessCheck:
    redis = app_state.redis
    if redis is None:
        return ReadinessCheck(name="redis", status="skipped", detail="FG_REDIS_URL unset")
    try:
        await redis.ping()
        return ReadinessCheck(name="redis", status="ok")
    except Exception as exc:
        return ReadinessCheck(name="redis", status="error", detail=str(exc))


def _check_model(app_state) -> ReadinessCheck:
    detector = app_state.detector
    detail = "stub heuristic (no ONNX artifact)" if detector.is_stub else detector.model_version
    return ReadinessCheck(name="model", status="ok", detail=detail)
