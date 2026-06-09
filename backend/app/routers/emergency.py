"""Manual SOS — POST /v1/emergency.

The caregiver presses the SOS button in the Flutter app (e.g. they can see the
patient has fallen but the wearable didn't fire).  This creates a timeline event
with source=MANUAL, fans it out over SSE, and dispatches FCM.

This is distinct from /v1/inference (automatic, device-initiated) — separate
endpoint keeps the two paths auditable and prevents a manual press from ever
being fed back into the ML retraining pipeline.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from app.auth import get_current_user
from app.schemas import EmergencyRequest, EmergencyResponse

router = APIRouter(prefix="/v1/emergency", tags=["emergency"])


@router.post("", response_model=EmergencyResponse, status_code=status.HTTP_201_CREATED)
async def trigger_emergency(
    req: EmergencyRequest,
    request: Request,
    user_id: UUID = Depends(get_current_user),
) -> EmergencyResponse:
    """Create a manual-SOS event and alert the caregiver's registered contacts."""
    event_id, created_at = await request.app.state.event_store.record_sos(
        user_id=user_id,
        device_ref=req.device_ref or "manual-sos",
        note=req.note,
    )
    return EmergencyResponse(event_id=event_id, created_at=created_at)
