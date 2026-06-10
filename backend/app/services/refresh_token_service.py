"""Refresh token lifecycle — create, rotate, revoke (ARCHITECTURE §5).

Tokens are 32-byte opaque hex strings; only the SHA-256 hash is persisted so
a DB leak can't be replayed.  Each use rotates the token (old revoked, new
issued) so stolen refresh tokens self-invalidate on first legitimate use.

The token_hash index gives O(log n) lookup.  Expired and revoked rows are left
in place for the 30-day window so the timeline is auditable; a background
sweeper (Phase 32) can GC them later.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from app.config import Settings
from app.models import RefreshToken

if TYPE_CHECKING:
    from app.db import Database

_TTL_DAYS = 30


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class InvalidRefreshTokenError(Exception):
    """The token is absent, expired, or already revoked."""


class RefreshTokenService:
    def __init__(self, settings: Settings, db: "Database | None") -> None:
        self.settings = settings
        self._db = db

    @property
    def is_stub(self) -> bool:
        return self._db is None

    async def create(self, user_id: UUID) -> str | None:
        """Mint a new refresh token for `user_id`; returns None in DB-less mode."""
        if self._db is None:
            return None
        raw = secrets.token_hex(32)
        expires = datetime.now(tz=timezone.utc) + timedelta(days=_TTL_DAYS)
        async with self._db.sessionmaker() as session:
            session.add(
                RefreshToken(
                    user_id=user_id,
                    token_hash=_hash(raw),
                    expires_at=expires,
                )
            )
            await session.commit()
        return raw

    async def rotate(self, raw_token: str) -> tuple[UUID, str]:
        """Verify `raw_token`, revoke it, and return (user_id, new_raw_token).

        Raises `InvalidRefreshTokenError` if the token is unknown, expired, or
        already revoked — the caller should 401 in all three cases.
        """
        if self._db is None:
            raise InvalidRefreshTokenError("DB-less mode")
        h = _hash(raw_token)
        now = datetime.now(tz=timezone.utc)
        async with self._db.sessionmaker() as session:
            row = (
                await session.execute(
                    select(RefreshToken).where(RefreshToken.token_hash == h)
                )
            ).scalar_one_or_none()
            if row is None or row.revoked_at is not None or row.expires_at < now:
                raise InvalidRefreshTokenError("invalid, expired or revoked token")
            # Rotate: revoke old, issue new.
            row.revoked_at = now
            new_raw = secrets.token_hex(32)
            session.add(
                RefreshToken(
                    user_id=row.user_id,
                    token_hash=_hash(new_raw),
                    expires_at=now + timedelta(days=_TTL_DAYS),
                )
            )
            user_id = row.user_id
            await session.commit()
        return user_id, new_raw

    async def revoke_all(self, user_id: UUID) -> None:
        """Revoke every active refresh token for a user (used on password change/logout)."""
        if self._db is None:
            return
        now = datetime.now(tz=timezone.utc)
        async with self._db.sessionmaker() as session:
            rows = (
                await session.execute(
                    select(RefreshToken).where(
                        RefreshToken.user_id == user_id,
                        RefreshToken.revoked_at.is_(None),
                    )
                )
            ).scalars().all()
            for row in rows:
                row.revoked_at = now
            await session.commit()
