"""Fall-event read side — the caregiver timeline + live feed.

  * GET  /v1/events                       — paginated timeline (DB-backed)
  * POST /v1/events/{id}/acknowledge      — ack an alert (DB-backed)
  * GET  /v1/events/stream                — live SSE feed of confirmed falls (Redis)

User-authenticated; results are scoped to the caller's user_id (Postgres RLS
enforces the same isolation at the DB layer, and the SSE feed subscribes only to
the caller's own pub/sub channel).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.auth import get_current_user
from app.deps import require_broker, require_db
from app.schemas import EventOut, EventPage

router = APIRouter(prefix="/v1/events", tags=["events"])

# How long the stream waits for an alert before emitting a comment frame. Keeps
# the connection (and any intermediary proxy) warm and lets us notice a client
# that has gone away within roughly this interval.
_SSE_KEEPALIVE_S = 15


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
    await request.app.state.audit_service.log(
        "event.acknowledge",
        user_id=user_id,
        details={"event_id": str(event_id)},
    )
    return event


@router.get("/stream")
async def stream_events(request: Request, user_id: UUID = Depends(get_current_user)):
    """Server-Sent Events feed of the caller's confirmed falls (Redis pub/sub).

    Subscribes to the caller's own channel and relays each published alert as an
    SSE `fall` event; a comment frame every `_SSE_KEEPALIVE_S` keeps the link
    alive and surfaces client disconnects. 503 without Redis (see require_broker).
    """
    broker = require_broker(request)

    async def _source() -> AsyncIterator[str]:
        yield "retry: 5000\n\n"  # ask the browser/client to reconnect after 5 s if dropped
        async with broker.subscription(user_id) as pubsub:
            while not await request.is_disconnected():
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=_SSE_KEEPALIVE_S
                )
                if message is None:
                    yield ": keepalive\n\n"  # comment frame — ignored by SSE clients
                    continue
                # decode_responses=True on the client → data is already a JSON str.
                yield f"event: fall\ndata: {message['data']}\n\n"

    return StreamingResponse(
        _source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # tell nginx not to buffer the stream
        },
    )
