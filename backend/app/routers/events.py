"""Fall-event read side — GET /v1/events + POST /v1/events/{id}/acknowledge.

Both require a database (503 in DB-less mode). Results are scoped to the caller
when an identity is supplied (the `X-User-Id` stub today, per-user JWT later) and
unscoped otherwise — a transitional single-tenant dev view until auth + RLS land.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.deps import require_db
from app.schemas import EventOut, EventPage
from app.security import optional_current_user

router = APIRouter(prefix="/v1/events", tags=["events"])


@router.get("", response_model=EventPage, dependencies=[Depends(require_db)])
async def list_events(
    request: Request,
    user_id: UUID | None = Depends(optional_current_user),
    device_id: str | None = Query(default=None, description="filter to one device's events"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> EventPage:
    return await request.app.state.event_store.list_events(
        user_id=user_id, device_id=device_id, limit=limit, offset=offset
    )


@router.post(
    "/{event_id}/acknowledge", response_model=EventOut, dependencies=[Depends(require_db)]
)
async def acknowledge_event(
    event_id: UUID,
    request: Request,
    user_id: UUID | None = Depends(optional_current_user),
) -> EventOut:
    event = await request.app.state.event_store.acknowledge(event_id=event_id, user_id=user_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    return event
