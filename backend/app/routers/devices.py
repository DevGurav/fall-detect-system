"""Device pairing + telemetry + fit-at-first calibration — /v1/devices/*.

Pairing: a user mints an 8-char code (`POST /pairing-codes`, user-auth) that a
device redeems (`POST /pair`, no auth — the code IS the one-time credential) to
bind itself to that user and receive its long-lived token. Telemetry: `/heartbeat`
(device-auth) records status; `GET ""` (user-auth) lists the caller's devices.

Calibration (Phase 29): the device streams ADL windows via
`POST /{id}/calibration-windows` (device-auth) during the 15-min fit-at-first
session; the caregiver triggers the fit via `POST /{id}/calibrate` (user-auth)
when the session ends.  All require a database.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth import DeviceIdentity, create_device_token, get_current_device, get_current_user
from app.deps import require_db
from app.ratelimit import rate_limit
from app.schemas import (
    CalibrationResponse,
    CalibrationWindowsRequest,
    DeviceOut,
    HeartbeatRequest,
    PairingCodeResponse,
    PairRequest,
    PairResponse,
)

router = APIRouter(prefix="/v1/devices", tags=["devices"])


@router.post(
    "/pairing-codes",
    response_model=PairingCodeResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("pairing_codes", 20, 3600))],
)
async def create_pairing_code(
    request: Request, user_id: UUID = Depends(get_current_user)
) -> PairingCodeResponse:
    require_db(request)
    code, expires_at = await request.app.state.pairing_service.create_code(user_id)
    return PairingCodeResponse(code=code, expires_at=expires_at)


@router.post(
    "/pair",
    response_model=PairResponse,
    dependencies=[Depends(rate_limit("pair", 10, 3600))],
)
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


# ─── Fit-at-first calibration (Phase 29) ─────────────────────────────────────


@router.post(
    "/{device_id}/calibration-windows",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def submit_calibration_windows(
    device_id: str,
    req: CalibrationWindowsRequest,
    request: Request,
    device: DeviceIdentity = Depends(get_current_device),
) -> None:
    """Stream ADL windows during the 15-min fit-at-first session (device-JWT).

    The device_id in the path must match the authenticated device's identity.
    Windows are stored in retraining_samples (label=ADL_CALIBRATION) and used
    by `POST /{id}/calibrate` to fit the per-user z-score normalisers.
    """
    if device_id != device.device_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "device_id does not match the authenticated device"
        )
    require_db(request)
    await request.app.state.calibration_store.accumulate_windows(
        req.windows,
        device_pk=device.device_pk,
        device_id=device.device_id,
        user_id=device.user_id,
    )


@router.post(
    "/{device_id}/calibrate",
    response_model=CalibrationResponse,
    status_code=status.HTTP_200_OK,
)
async def calibrate_device(
    device_id: str,
    request: Request,
    user_id: UUID = Depends(get_current_user),
) -> CalibrationResponse:
    """Fit per-user z-score normalisers from accumulated ADL windows (user-JWT).

    Triggered by the caregiver app once the 15-min session is complete.
    Computes channel-level + feature-level statistics from all stored
    ADL_CALIBRATION windows for the device and upserts device_calibration.
    """
    require_db(request)
    # Resolve device_pk — needs the device row for the authenticated user.
    device_pk = await request.app.state.device_service.get_device_pk(
        device_id=device_id, user_id=user_id
    )
    if device_pk is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found or not paired to this account")
    n, fitted_at = await request.app.state.calibration_store.fit(
        device_pk=device_pk,
        device_id=device_id,
        user_id=user_id,
    )
    await request.app.state.audit_service.log(
        "device.calibrate",
        user_id=user_id,
        device_ref=device_id,
        details={"n_adl_windows": n},
    )
    return CalibrationResponse(device_id=device_id, n_adl_windows=n, fitted_at=fitted_at)
