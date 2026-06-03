"""Device pairing + telemetry — /v1/devices/*.

Pairing: a user mints an 8-char code (`POST /pairing-codes`, user-auth) that a
device redeems (`POST /pair`, no auth — the code IS the one-time credential) to
bind itself to that user and receive its long-lived token. Telemetry: `/heartbeat`
(device-auth) records status; `GET ""` (user-auth) lists the caller's devices.
All require a database.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth import DeviceIdentity, create_device_token, get_current_device, get_current_user
from app.deps import require_db
from app.schemas import (
    DeviceOut,
    HeartbeatRequest,
    PairingCodeResponse,
    PairRequest,
    PairResponse,
)

router = APIRouter(prefix="/v1/devices", tags=["devices"])


@router.post(
    "/pairing-codes", response_model=PairingCodeResponse, status_code=status.HTTP_201_CREATED
)
async def create_pairing_code(
    request: Request, user_id: UUID = Depends(get_current_user)
) -> PairingCodeResponse:
    require_db(request)
    code, expires_at = await request.app.state.pairing_service.create_code(user_id)
    return PairingCodeResponse(code=code, expires_at=expires_at)


@router.post("/pair", response_model=PairResponse)
async def pair(req: PairRequest, request: Request) -> PairResponse:
    require_db(request)
    identity = await request.app.state.pairing_service.redeem(req.code, req.device_id)
    if identity is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "invalid, expired, or already-used pairing code"
        )
    token = create_device_token(
        request.app.state.settings,
        device_pk=identity.device_pk,
        device_id=identity.device_id,
        user_id=identity.user_id,
    )
    return PairResponse(device_token=token, device_id=identity.device_id, user_id=identity.user_id)


@router.post("/heartbeat", response_model=DeviceOut)
async def heartbeat(
    req: HeartbeatRequest, request: Request, device: DeviceIdentity = Depends(get_current_device)
) -> DeviceOut:
    if req.device_id != device.device_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "device_id does not match the authenticated device"
        )
    require_db(request)
    result = await request.app.state.device_service.heartbeat(
        device_id=device.device_id,
        user_id=device.user_id,
        battery_pct=req.battery_pct,
        signal_dbm=req.signal_dbm,
        edge_model_version=req.edge_model_version,
    )
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device is not paired to this account")
    return result


@router.get("", response_model=list[DeviceOut])
async def list_devices(
    request: Request, user_id: UUID = Depends(get_current_user)
) -> list[DeviceOut]:
    require_db(request)
    return await request.app.state.device_service.list_devices(user_id=user_id)
