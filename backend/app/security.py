"""Identity seam — transitional.

Real per-user OAuth + per-device JWT (ARCHITECTURE §2.2 / §5) is a later slice.
This module is the seam that work replaces:

  * `get_device` looks up the `devices` row for a §8 `device_id` (None when the
    device is unknown / unpaired) — used to scope ingested windows and persisted
    events to their owning device + user where possible.
  * `optional_current_user` / `get_current_user` are STUB dependencies that trust
    an `X-User-Id` header (dev only) so user-scoped endpoints can be built and
    scoped now; both are swapped for real JWT verification when the auth slice lands.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device


async def get_device(session: AsyncSession, device_id: str) -> Device | None:
    """The `devices` row for `device_id`, or None if it is unknown / unpaired."""
    result = await session.execute(select(Device).where(Device.device_id == device_id))
    return result.scalar_one_or_none()


async def optional_current_user(x_user_id: str | None = Header(default=None)) -> UUID | None:
    """STUB: resolve the caller from an `X-User-Id` header; None when absent.

    Lets read endpoints scope to a user when one is supplied and fall back to an
    unscoped (single-tenant dev) view otherwise. Replaced by JWT in the auth slice.
    """
    if x_user_id is None:
        return None
    try:
        return UUID(x_user_id)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid X-User-Id header") from None


async def get_current_user(x_user_id: str | None = Header(default=None)) -> UUID:
    """STUB: like `optional_current_user`, but required (401 when absent)."""
    user_id = await optional_current_user(x_user_id)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication not configured")
    return user_id
