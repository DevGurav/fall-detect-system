"""Device service — heartbeat upserts and the device-status read side.

`heartbeat` records battery / signal / last-seen for a watch (ARCHITECTURE §2.1),
creating the `devices` row on first contact — pairing (associating a device to a
user) is the auth slice; until then a device registers unowned. `list_devices`
returns live status with online/offline derived from `last_seen_at`, so it stays
truthful without a background sweeper flipping a stored flag.
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
        battery_pct: int | None,
        signal_dbm: int | None,
        edge_model_version: str | None = None,
    ) -> DeviceOut:
        """Update (or, on first contact, register) a device's live status."""
        now = datetime.now(tz=timezone.utc)
        async with self._db.sessionmaker() as session:
            device = await get_device(session, device_id)
            if device is None:
                # First contact: register the device, unowned until pairing.
                # Production hardens this behind a device JWT + an ON CONFLICT upsert.
                device = Device(device_id=device_id)
                session.add(device)
            device.last_seen_at = now
            device.status = "online"
            if battery_pct is not None:
                device.battery_pct = battery_pct
            if signal_dbm is not None:
                device.signal_dbm = signal_dbm
            if edge_model_version is not None:
                device.edge_model_version = edge_model_version
            await session.commit()
            await session.refresh(device)
            return self._to_out(device, now)

    async def list_devices(self, *, user_id: UUID | None) -> list[DeviceOut]:
        now = datetime.now(tz=timezone.utc)
        async with self._db.sessionmaker() as session:
            stmt = select(Device).order_by(Device.created_at.desc())
            if user_id is not None:
                stmt = stmt.where(Device.user_id == user_id)
            devices = (await session.execute(stmt)).scalars().all()
            return [self._to_out(d, now) for d in devices]
