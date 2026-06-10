"""Event store — persist confirmed falls and serve the caregiver timeline.

When the `CloudDetector` confirms a fall on /v1/inference, the verdict is written
to the `events` table (ARCHITECTURE §3.2), scoped to the authenticated device's
owner, and fanned out to the owner's live SSE feed via the `EventBroker` (Phase
27). DB-less, the row is skipped but the alert is still published — a caregiver
watching the stream must hear about a fall whether or not it was persisted. The
read side (`list_events`, `acknowledge`) backs GET /v1/events and POST
/v1/events/{id}/acknowledge.

Phase 28b: FCM push is dispatched after SSE publish so a killed app is still
notified.  Like SSE, it never blocks the response and failures are logged only.

Every DB call runs in a `session_for(user_id)` so Postgres RLS isolates rows to
the caller; the explicit `user_id` filters below are kept as belt-and-suspenders.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.config import Settings
from app.models import Event, User
from app.schemas import EventOut, EventPage, InferenceRequest, InferenceResponse

if TYPE_CHECKING:
    from app.broker import EventBroker
    from app.db import Database
    from app.services.fcm_service import FcmService

logger = logging.getLogger(__name__)


def _alert_payload(
    req: InferenceRequest, verdict: InferenceResponse, *, event_id: UUID | None
) -> dict:
    """The JSON a caregiver's SSE stream receives for a confirmed fall."""
    return {
        "type": "fall",
        "event_id": str(event_id) if event_id is not None else None,
        "device_id": req.device_id,
        "ts_start_unix_ms": req.ts_start_unix_ms,
        "is_fall": verdict.is_fall,
        "confidence": verdict.confidence,
        "severity": verdict.severity.value,
        "lead_time_ms": verdict.lead_time_ms,
        "model_version": verdict.model_version,
    }


def _sos_payload(device_ref: str, event_id: UUID | None) -> dict:
    return {
        "type": "sos",
        "event_id": str(event_id) if event_id is not None else None,
        "device_id": device_ref,
        "is_fall": True,
        "severity": "high",
    }


class EventStore:
    def __init__(
        self,
        settings: Settings,
        db: "Database | None",
        broker: "EventBroker | None" = None,
        fcm: "FcmService | None" = None,
    ) -> None:
        self.settings = settings
        self._db = db
        self._broker = broker
        self._fcm = fcm

    @property
    def is_stub(self) -> bool:
        return self._db is None

    # ── fall from cloud detector ──────────────────────────────────────────────

    async def record_fall(
        self, req: InferenceRequest, verdict: InferenceResponse, *, user_id: UUID, device_pk: UUID
    ) -> UUID | None:
        """Persist a confirmed fall, publish to SSE, and push FCM.

        Persistence is DB-gated; SSE and FCM are not — an alert must reach the
        caregiver even when the DB is temporarily unavailable.
        """
        event_id = await self._persist_fall(req, verdict, user_id=user_id, device_pk=device_pk)
        if self._broker is not None:
            await self._broker.publish_fall(
                user_id, _alert_payload(req, verdict, event_id=event_id)
            )
        await self._push_fall_fcm(
            user_id=user_id,
            event_id=event_id,
            device_id=req.device_id,
            severity=verdict.severity.value,
            confidence=verdict.confidence,
        )
        return event_id

    async def _persist_fall(
        self, req: InferenceRequest, verdict: InferenceResponse, *, user_id: UUID, device_pk: UUID
    ) -> UUID | None:
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
                    peak_ms2=verdict.peak_ms2,
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

    async def _push_fall_fcm(
        self,
        *,
        user_id: UUID,
        event_id: UUID | None,
        device_id: str,
        severity: str,
        confidence: float,
    ) -> None:
        if self._fcm is None or self._fcm.is_stub or self._db is None:
            return
        fcm_token = await self._get_fcm_token(user_id)
        if fcm_token:
            await self._fcm.send_fall_notification(
                fcm_token=fcm_token,
                event_id=event_id,
                device_id=device_id,
                severity=severity,
                confidence=confidence,
            )

    # ── manual SOS (caregiver-initiated) ─────────────────────────────────────

    async def record_sos(
        self, *, user_id: UUID, device_ref: str, note: str | None
    ) -> tuple[UUID | None, datetime]:
        """Create a manual-SOS event, fan out to SSE, and dispatch FCM."""
        event_id = await self._persist_sos(user_id=user_id, device_ref=device_ref, note=note)
        now = datetime.now(tz=timezone.utc)
        if self._broker is not None:
            await self._broker.publish_fall(user_id, _sos_payload(device_ref, event_id))
        await self._push_sos_fcm(user_id=user_id)
        return event_id, now

    async def _persist_sos(
        self, *, user_id: UUID, device_ref: str, note: str | None
    ) -> UUID | None:
        if self._db is None:
            return None
        event_id = uuid4()
        async with self._db.session_for(user_id) as session:
            session.add(
                Event(
                    id=event_id,
                    device_ref=device_ref,
                    device_id=None,
                    user_id=user_id,
                    ts_start_unix_ms=int(datetime.now(tz=timezone.utc).timestamp() * 1000),
                    is_fall=True,
                    confidence=1.0,
                    severity="high",
                    lead_time_ms=None,
                    peak_ms2=None,
                    model_version="manual-sos",
                )
            )
            await session.commit()
        logger.info("SOS event %s recorded by user %s", event_id.hex, user_id)
        return event_id

    async def _push_sos_fcm(self, *, user_id: UUID) -> None:
        if self._fcm is None or self._fcm.is_stub or self._db is None:
            return
        fcm_token = await self._get_fcm_token(user_id)
        if fcm_token:
            await self._fcm.send_sos_notification(
                fcm_token=fcm_token, triggered_by="caregiver"
            )

    # ── read side ────────────────────────────────────────────────────────────

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
            event = await session.get(Event, event_id)
            if event is None or (event.user_id is not None and event.user_id != user_id):
                return None
            event.acknowledged_at = datetime.now(tz=timezone.utc)
            event.acked_by = user_id
            out = EventOut.model_validate(event)
            await session.commit()
            return out

    # ── helpers ───────────────────────────────────────────────────────────────

    async def _get_fcm_token(self, user_id: UUID) -> str | None:
        async with self._db.session_for(user_id) as session:
            user = await session.get(User, user_id)
            return user.fcm_token if user else None
