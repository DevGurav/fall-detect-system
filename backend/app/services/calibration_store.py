"""Per-device calibration lookup — feeds the detector's personalization seam.

Reads the `device_calibration` row for a request's §8 `device_id` (joined through
`devices`) and returns a `CalibrationProfile` for the detector to apply: per-user
z-score normalisers + a threshold override (ARCHITECTURE §4.6, §3.2). Runs in a
`session_for(user_id)` so RLS only exposes the calling device's own calibration.
Returns None when DB-less, or when the device is unpaired / has no calibration yet
— so the detector falls back to the model's global stats. The fit-at-pairing
*write* path is a later slice; this is read-only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from app.config import Settings
from app.models import Device, DeviceCalibration
from app.services.detector import CalibrationProfile

if TYPE_CHECKING:
    from app.db import Database


class CalibrationStore:
    def __init__(self, settings: Settings, db: Database | None) -> None:
        self.settings = settings
        self._db = db

    @property
    def is_stub(self) -> bool:
        return self._db is None

    async def get(self, device_id: str, user_id: UUID) -> CalibrationProfile | None:
        """The device's calibration, or None (DB-less / unpaired / uncalibrated)."""
        if self._db is None:
            return None
        async with self._db.session_for(user_id) as session:
            row = (
                await session.execute(
                    select(DeviceCalibration)
                    .join(Device, DeviceCalibration.device_id == Device.id)
                    .where(Device.device_id == device_id)
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return CalibrationProfile(
            channel_mean=row.channel_mean,
            channel_std=row.channel_std,
            feature_mean=row.feature_mean,
            feature_std=row.feature_std,
            threshold_override=row.threshold_override,
        )
