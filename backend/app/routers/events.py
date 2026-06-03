"""Fall-event read side — GET /v1/events + POST /v1/events/{id}/acknowledge.

User-authenticated; results are scoped to the caller's user_id (Postgres RLS
enforces the same isolation at the DB layer). Require a database.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.auth import get_current_user
from app.deps import require_db
from app.schemas import EventOut, EventPage

router = APIRouter(prefix="/v1/events", tags=["events"])


@router.get("", response_model=EventPage)
async def list_events(
    request: Request,
    user_id: UUID = Depends(get_current_user),
    device_id: str | None = Query(default=None, description="filter to one device's events"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> EventPage:
    require_db(request)
    return await request.app.state.event_store.list_events(
        user_id=user_id, device_id=device_id, limit=limit, offset=offset
    )


@router.post("/{event_id}/acknowledge", response_model=EventOut)
async def acknowledge_event(
    event_id: UUID, request: Request, user_id: UUID = Depends(get_current_user)
) -> EventOut:
    require_db(request)
    event = await request.app.state.event_store.acknowledge(event_id=event_id, user_id=user_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    return event
