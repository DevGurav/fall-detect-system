"""Append-only audit log writer (ARCHITECTURE §5).

Writes a row to `audit_events` on every security-sensitive action (pair,
calibrate, acknowledge, SOS, login, register).  Failures are logged and
silently swallowed — an audit write must never propagate and block the main
operation.  The table itself has no RLS policy (audit rows are never queried by
user-scoped sessions), so we use the bare `sessionmaker`.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from app.models import AuditEvent

if TYPE_CHECKING:
    from app.db import Database

logger = logging.getLogger(__name__)


class AuditService:
    def __init__(self, db: "Database | None") -> None:
        self._db = db

    async def log(
        self,
        action: str,
        *,
        user_id: UUID | None = None,
        device_ref: str | None = None,
        details: dict | None = None,
    ) -> None:
        if self._db is None:
            return
        try:
            async with self._db.sessionmaker() as session:
                session.add(
                    AuditEvent(
                        user_id=user_id,
                        device_ref=device_ref,
                        action=action,
                        details=details,
                    )
                )
                await session.commit()
        except Exception:
            logger.exception("audit write failed for action=%s user=%s", action, user_id)
