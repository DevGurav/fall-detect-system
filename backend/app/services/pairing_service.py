"""Device pairing — the 8-char code lifecycle (ARCHITECTURE §5).

A paired user mints a short-lived code (`create_code`); a device redeems it
(`redeem`) to bind itself to that user and receive its token. Codes are
single-use, TTL-bounded, and attempt-limited. (Per-IP backoff via Redis is the
documented complement — ARCHITECTURE §2.2; this layer protects the code itself.)

`pairing_codes` is RLS-free (redemption looks a code up before any user context
exists), but the `devices` write it performs is RLS-protected — so redeem sets the
`app.user_id` GUC to the code's owner before binding the device.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select, text

from app.auth import DeviceIdentity, generate_pairing_code
from app.config import Settings
from app.models import Device, PairingCode
from app.security import get_device

if TYPE_CHECKING:
    from app.db import Database


class PairingService:
    def __init__(self, settings: Settings, db: Database | None) -> None:
        self.settings = settings
        self._db = db

    @property
    def is_stub(self) -> bool:
        return self._db is None

    async def create_code(self, user_id: UUID) -> tuple[str, datetime]:
        code = generate_pairing_code()
        expires_at = datetime.now(tz=timezone.utc) + timedelta(
            minutes=self.settings.pairing_code_ttl_min
        )
        async with self._db.sessionmaker() as session:
            session.add(
                PairingCode(code=code, user_id=user_id, expires_at=expires_at, attempts=0)
            )
            await session.commit()
        return code, expires_at

    async def redeem(self, code: str, device_id: str) -> DeviceIdentity | None:
        """Bind `device_id` to the code's user and return its identity, or None."""
        now = datetime.now(tz=timezone.utc)
        async with self._db.sessionmaker() as session:
            pc = (
                await session.execute(select(PairingCode).where(PairingCode.code == code))
            ).scalar_one_or_none()
            if pc is None or pc.consumed_at is not None:
                return None
            pc.attempts += 1
            if pc.attempts > self.settings.pairing_max_attempts or pc.expires_at < now:
                await session.commit()  # persist the spent attempt, then reject
                return None
            # Bind the device under the code's owner. Set the RLS context so the
            # devices INSERT/UPDATE satisfies WITH CHECK (user_id = app.user_id).
            await session.execute(
                text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(pc.user_id)}
            )
            device = await get_device(session, device_id)
            if device is None:
                device = Device(id=uuid4(), device_id=device_id)
                session.add(device)
            device.user_id = pc.user_id
            device.paired_at = now
            pc.consumed_at = now
            identity = DeviceIdentity(
                device_pk=device.id, device_id=device.device_id, user_id=pc.user_id
            )
            await session.commit()
            return identity
