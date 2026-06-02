"""POST /v1/retraining — store a window the user canceled during the grace period.

This is the data-collection path, kept deliberately separate from `/v1/inference`
(the detection path). A canceled false alarm must NOT run through the CloudDetector
— there's no fall to confirm; the window is just labeled MLOps training data. Auth
(per-device JWT) and rate-limiting are later Week-C/D work, same as /v1/inference.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.schemas import RetrainingAck, RetrainingRequest

router = APIRouter(prefix="/v1", tags=["retraining"])


@router.post("/retraining", response_model=RetrainingAck)
def retraining(req: RetrainingRequest, request: Request) -> RetrainingAck:
    store = request.app.state.retraining_store
    return store.store(req)
