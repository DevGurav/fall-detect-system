"""Redis-backed fixed-window rate limiting (ARCHITECTURE §5).

Gated on `FG_REDIS_URL`: with no Redis configured the limiter is a **no-op**, so
dev and the test suite run without it — mirroring the DB gate. Each window is a
Redis counter keyed by `(scope, client IP)` with a TTL; once the count exceeds the
limit the request gets a `429` with `Retry-After`. Used on the public auth +
pairing surface to blunt brute force.

`rate_limit(scope, limit, window_s)` is a dependency factory: drop
`Depends(rate_limit(...))` into a route's `dependencies=[...]`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, status

if TYPE_CHECKING:
    from redis.asyncio import Redis


class RateLimiter:
    def __init__(self, redis: Redis | None) -> None:
        self._redis = redis

    @property
    def is_stub(self) -> bool:
        return self._redis is None

    async def hit(self, request: Request, scope: str, limit: int, window_s: int) -> None:
        """Count one request against the (scope, IP) window; 429 once over the limit."""
        if self._redis is None:
            return  # no-op when Redis isn't configured
        ip = request.client.host if request.client else "unknown"
        key = f"rl:{scope}:{ip}"
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, window_s)
        if count > limit:
            ttl = await self._redis.ttl(key)
            retry_after = str(ttl if ttl and ttl > 0 else window_s)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"rate limit exceeded for {scope}; retry in {retry_after}s",
                headers={"Retry-After": retry_after},
            )


def rate_limit(scope: str, limit: int, window_s: int):
    """Build a dependency that charges one hit against the (scope, IP) window."""

    async def _dependency(request: Request) -> None:
        await request.app.state.rate_limiter.hit(request, scope, limit, window_s)

    return _dependency
