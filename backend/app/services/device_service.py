"""Device service — heartbeat updates and the device-status read side.

`heartbeat` records battery / signal / last-seen for an already-paired device
(ARCHITECTURE §2.1); the row is created at pairing, so an unknown device returns
None → 404 (no more unauthenticated auto-registration). `list_devices` returns
live status with online/offline derived from `last_seen_at`, so it stays truthful
without a background sweeper. Both run in a `session_for(user_id)` so Postgres RLS
isolates rows to the caller.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from app.config import Settings
from app.models import Device
from app.schemas import DeviceOut
from app.security import get_device

if TYPE_CHECKING:
    from app.db import Database


def derive_status(last_seen_at: datetime | None, now: datetime, offline_after_s: int) -> str:
    """online if seen within the window, offline if stale, unknown if never seen."""
    if last_seen_at is None:
        return "unknown"
    return "online" if (now - last_seen_at).total_seconds() <= offline_after_s else "offline"


class DeviceService:
    def __init__(self, settings: Settings, db: Database | None) -> None:
        self.settings = settings
        self._db = db

    @property
    def is_stub(self) -> bool:
        return self._db is None

    def _to_out(self, device: Device, now: datetime) -> DeviceOut:
        return DeviceOut(
            id=device.id,
            device_id=device.device_id,
            status=derive_status(device.last_seen_at, now, self.settings.device_offline_after_s),
            battery_pct=device.battery_pct,
            signal_dbm=device.signal_dbm,
            last_seen_at=device.last_seen_at,
            paired_at=device.paired_at,
            edge_model_version=device.edge_model_version,
            created_at=device.created_at,
        )

    async def heartbeat(
        self,
        *,
        device_id: str,
        user_id: UUID,
        battery_pct: int | None,
        signal_dbm: int | None,
        edge_model_version: str | None = None,
    ) -> DeviceOut | None:
        """Update a paired device's live status; None if the caller has no such device."""
        now = datetime.now(tz=timezone.utc)
        async with self._db.session_for(user_id) as session:
            device = await get_device(session, device_id)
            if device is None:
                return None  # not paired to this user (RLS-scoped lookup) -> 404
            device.last_seen_at = now
            device.status = "online"
            if battery_pct is not None:
                device.battery_pct = battery_pct
            if signal_dbm is not None:
                device.signal_dbm = signal_dbm
            if edge_model_version is not None:
                device.edge_model_version = edge_model_version
            out = self._to_out(device, now)  # build before commit (GUC is txn-local)
            await session.commit()
            return out

    async def list_devices(self, *, user_id: UUID) -> list[DeviceOut]:
        now = datetime.now(tz=timezone.utc)
        async with self._db.session_for(user_id) as session:
            stmt = select(Device).where(Device.user_id == user_id).order_by(Device.created_at.desc())
            devices = (await session.execute(stmt)).scalars().all()
            return [self._to_out(d, now) for d in devices]
