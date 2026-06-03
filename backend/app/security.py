"""Identity seam — transitional.

Real per-user OAuth + per-device JWT (ARCHITECTURE §2.2 / §5) is a later slice.
This module is the seam that work replaces:

  * `resolve_user_id_for_device` maps a window's §8 `device_id` to its owning
    user via the `devices` table (NULL when the device isn't paired yet) — used
    now by the retraining-sample write to scope rows by owner where possible.
  * `get_current_user` is a STUB dependency that trusts an `X-User-Id` header
    (dev only) so user-scoped read endpoints can be built and scoped now; it is
    swapped for real JWT verification when the auth slice lands.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device


async def resolve_user_id_for_device(session: AsyncSession, device_id: str) -> UUID | None:
    """Owner of `device_id`, or None if the device is unknown / unpaired."""
    result = await session.execute(select(Device.user_id).where(Device.device_id == device_id))
    return result.scalar_one_or_none()


async def get_current_user(x_user_id: str | None = Header(default=None)) -> UUID:
    """STUB: trust an `X-User-Id` header. Replaced by JWT in the auth slice."""
    if x_user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication not configured")
    try:
        return UUID(x_user_id)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid X-User-Id header") from None
