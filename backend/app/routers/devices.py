"""Device telemetry — POST /v1/devices/heartbeat + GET /v1/devices.

Both require a database (503 in DB-less mode). The heartbeat registers a device on
first contact (unowned until pairing); the list is scoped to the caller when an
identity is supplied (the `X-User-Id` stub today, per-user JWT later).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.deps import require_db
from app.schemas import DeviceOut, HeartbeatRequest
from app.security import optional_current_user

router = APIRouter(prefix="/v1/devices", tags=["devices"])


@router.post("/heartbeat", response_model=DeviceOut, dependencies=[Depends(require_db)])
async def heartbeat(req: HeartbeatRequest, request: Request) -> DeviceOut:
    return await request.app.state.device_service.heartbeat(
        device_id=req.device_id,
        battery_pct=req.battery_pct,
        signal_dbm=req.signal_dbm,
        edge_model_version=req.edge_model_version,
    )


@router.get("", response_model=list[DeviceOut], dependencies=[Depends(require_db)])
async def list_devices(
    request: Request,
    user_id: UUID | None = Depends(optional_current_user),
) -> list[DeviceOut]:
    return await request.app.state.device_service.list_devices(user_id=user_id)
