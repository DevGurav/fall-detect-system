"""Health / readiness endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app import __version__
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    return HealthResponse(
        status="ok",
        version=__version__,
        model_version=request.app.state.detector.model_version,  # real model or stub
        environment=settings.environment,
    )
