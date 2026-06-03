"""Device lookup helper shared by the ingestion + telemetry services.

Authentication/identity now lives in `app/auth.py` (real per-user / per-device
JWTs). This module keeps just the one DB helper the services use to resolve a §8
`device_id` string to its row.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device


async def get_device(session: AsyncSession, device_id: str) -> Device | None:
    """The `devices` row for `device_id`, or None if it is unknown / unpaired."""
    result = await session.execute(select(Device).where(Device.device_id == device_id))
    return result.scalar_one_or_none()
