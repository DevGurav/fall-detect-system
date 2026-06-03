"""Event store — persist confirmed falls and serve the caregiver timeline.

When the `CloudDetector` confirms a fall on /v1/inference, the verdict is written
to the `events` table (ARCHITECTURE §3.2), scoped to the authenticated device's
owner. DB-less, `record_fall` is a no-op so /v1/inference still returns its verdict
without persistence. The read side (`list_events`, `acknowledge`) backs GET
/v1/events and POST /v1/events/{id}/acknowledge.

Every DB call runs in a `session_for(user_id)` so Postgres RLS isolates rows to the
caller; the explicit `user_id` filters below are kept as belt-and-suspenders.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.config import Settings
from app.models import Event
from app.schemas import EventOut, EventPage, InferenceRequest, InferenceResponse

if TYPE_CHECKING:
    from app.db import Database

logger = logging.getLogger(__name__)


class EventStore:
    def __init__(self, settings: Settings, db: Database | None) -> None:
        self.settings = settings
        self._db = db

    @property
    def is_stub(self) -> bool:
        return self._db is None

    async def record_fall(
        self, req: InferenceRequest, verdict: InferenceResponse, *, user_id: UUID, device_pk: UUID
    ) -> UUID | None:
        """Persist a confirmed fall to `events`. No-op (returns None) when DB-less."""
        if self._db is None:
            return None
        event_id = uuid4()
        async with self._db.session_for(user_id) as session:
            session.add(
                Event(
                    id=event_id,
                    device_ref=req.device_id,
                    device_id=device_pk,
                    user_id=user_id,
                    ts_start_unix_ms=req.ts_start_unix_ms,
                    is_fall=verdict.is_fall,
                    confidence=verdict.confidence,
                    severity=verdict.severity.value,
                    lead_time_ms=verdict.lead_time_ms,
                    model_version=verdict.model_version,
                )
            )
            await session.commit()
        logger.info(
            "event %s recorded: device=%s severity=%s conf=%.3f",
            event_id.hex,
            req.device_id,
            verdict.severity.value,
            verdict.confidence,
        )
        return event_id

    async def list_events(
        self, *, user_id: UUID, device_id: str | None, limit: int, offset: int
    ) -> EventPage:
        """A page of the caller's fall timeline, newest first, optionally filtered."""
        async with self._db.session_for(user_id) as session:
            rows = select(Event).where(Event.user_id == user_id)
            count = select(func.count()).select_from(Event).where(Event.user_id == user_id)
            if device_id is not None:
                rows = rows.where(Event.device_ref == device_id)
                count = count.where(Event.device_ref == device_id)
            total = (await session.execute(count)).scalar_one()
            result = await session.execute(
                rows.order_by(Event.created_at.desc()).limit(limit).offset(offset)
            )
            items = [EventOut.model_validate(e) for e in result.scalars().all()]
        return EventPage(items=items, total=total, limit=limit, offset=offset)

    async def acknowledge(self, *, event_id: UUID, user_id: UUID) -> EventOut | None:
        """Mark the caller's event acknowledged. None if it isn't theirs / absent."""
        async with self._db.session_for(user_id) as session:
            event = await session.get(Event, event_id)  # RLS already scopes to the caller
            if event is None or (event.user_id is not None and event.user_id != user_id):
                return None
            event.acknowledged_at = datetime.now(tz=timezone.utc)
            event.acked_by = user_id
            out = EventOut.model_validate(event)  # build before commit (GUC is txn-local)
            await session.commit()
            return out
