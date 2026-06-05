"""Redis pub/sub for the live caregiver feed (Phase 27).

A confirmed fall is published to a per-user channel (`events:user:{user_id}`); the
SSE endpoint (`GET /v1/events/stream`) subscribes to the caller's channel and
streams alerts as they arrive. Gated on Redis: with no client the broker is a
no-op publisher and the stream endpoint returns 503 — mirroring the other
optional-infra gates (DB, rate limiting).
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from redis.asyncio import Redis
    from redis.asyncio.client import PubSub


def channel_for(user_id: UUID) -> str:
    return f"events:user:{user_id}"


class EventBroker:
    def __init__(self, redis: Redis | None) -> None:
        self._redis = redis

    @property
    def is_stub(self) -> bool:
        return self._redis is None

    async def publish_fall(self, user_id: UUID, payload: dict) -> None:
        """Fan a confirmed-fall payload out to the user's channel (no-op without Redis)."""
        if self._redis is None:
            return
        await self._redis.publish(channel_for(user_id), json.dumps(payload))

    @asynccontextmanager
    async def subscription(self, user_id: UUID) -> AsyncIterator[PubSub]:
        """A subscribed pub/sub bound to the user's channel; unsubscribes on exit."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel_for(user_id))
        try:
            yield pubsub
        finally:
            await pubsub.unsubscribe(channel_for(user_id))
            await pubsub.aclose()
