"""POST /v1/inference — run the cloud detector on a streamed window.

Device-authenticated: the caller presents its per-device JWT and the window's body
`device_id` must match the token (else 403), so a device can't post as another. The
device's calibration profile is looked up and applied per request; on a confirmed
fall the verdict is persisted to `events` (a no-op when DB-less). Rate-limiting is
a later slice.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth import DeviceIdentity, get_current_device
from app.schemas import InferenceRequest, InferenceResponse

router = APIRouter(prefix="/v1", tags=["inference"])


@router.post("/inference", response_model=InferenceResponse)
async def inference(
    req: InferenceRequest, request: Request, device: DeviceIdentity = Depends(get_current_device)
) -> InferenceResponse:
    if req.device_id != device.device_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "device_id does not match the authenticated device"
        )
    profile = await request.app.state.calibration_store.get(req.device_id)
    verdict = request.app.state.detector.predict(req, profile)
    if verdict.is_fall:
        await request.app.state.event_store.record_fall(req, verdict)
    return verdict
