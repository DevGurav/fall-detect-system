"""POST /v1/inference — run the cloud detector on a streamed window.

The device's calibration profile (per-user z-score normalisers + threshold
override) is looked up and applied per request; absent one, the detector uses the
model's global stats. On a **confirmed** fall the verdict is persisted to the
`events` table via the EventStore (a no-op when DB-less, so the verdict is always
returned). Auth (per-device JWT) and rate-limiting are a later slice.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.schemas import InferenceRequest, InferenceResponse

router = APIRouter(prefix="/v1", tags=["inference"])


@router.post("/inference", response_model=InferenceResponse)
async def inference(req: InferenceRequest, request: Request) -> InferenceResponse:
    profile = await request.app.state.calibration_store.get(req.device_id)
    verdict = request.app.state.detector.predict(req, profile)
    if verdict.is_fall:
        await request.app.state.event_store.record_fall(req, verdict)
    return verdict
