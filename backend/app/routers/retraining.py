"""POST /v1/retraining — store a window the user canceled during the grace period.

Device-authenticated (per-device JWT; the body `device_id` must match the token).
Kept separate from /v1/inference: a canceled false alarm is labeled training data
and must NOT run through the CloudDetector.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth import DeviceIdentity, get_current_device
from app.schemas import RetrainingAck, RetrainingRequest

router = APIRouter(prefix="/v1", tags=["retraining"])


@router.post("/retraining", response_model=RetrainingAck)
async def retraining(
    req: RetrainingRequest, request: Request, device: DeviceIdentity = Depends(get_current_device)
) -> RetrainingAck:
    if req.device_id != device.device_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "device_id does not match the authenticated device"
        )
    return await request.app.state.retraining_store.store(
        req, user_id=device.user_id, device_pk=device.device_pk
    )
