"""POST /v1/inference — run the cloud detector on a streamed window.

Auth (per-device JWT), rate-limiting, and event persistence are later Week-C/D
work; this skeleton nails the validated request → detector → typed response path.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.schemas import InferenceRequest, InferenceResponse

router = APIRouter(prefix="/v1", tags=["inference"])


@router.post("/inference", response_model=InferenceResponse)
def inference(req: InferenceRequest, request: Request) -> InferenceResponse:
    detector = request.app.state.detector
    return detector.predict(req)
