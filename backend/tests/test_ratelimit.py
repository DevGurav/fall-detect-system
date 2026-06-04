"""Rate limiter — deterministic unit tests with a fake Redis (no real server).

The limiter is a no-op without Redis (so the rest of the suite runs without one);
here a tiny in-memory fake exercises the 429 logic. Live behaviour against a real
Redis is verified separately (see backend/README.md / BUILD_LOG).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ratelimit import RateLimiter


class _FakeRedis:
    """Minimal async stand-in: INCR + EXPIRE + TTL over an in-memory dict."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.ttls[key] = seconds

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, -1)


def _request(ip: str = "1.2.3.4") -> SimpleNamespace:
    return SimpleNamespace(client=SimpleNamespace(host=ip))


def test_limiter_allows_up_to_limit_then_429s():
    rl = RateLimiter(_FakeRedis())

    async def run():
        for _ in range(3):  # limit = 3 -> first three pass
            await rl.hit(_request(), "auth", 3, 60)
        with pytest.raises(HTTPException) as exc:  # fourth is blocked
            await rl.hit(_request(), "auth", 3, 60)
        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers

    asyncio.run(run())


def test_limiter_is_per_ip():
    rl = RateLimiter(_FakeRedis())

    async def run():
        await rl.hit(_request("10.0.0.1"), "auth", 1, 60)  # IP-1 spends its single hit
        await rl.hit(_request("10.0.0.2"), "auth", 1, 60)  # IP-2 has its own allowance
        with pytest.raises(HTTPException):
            await rl.hit(_request("10.0.0.1"), "auth", 1, 60)  # IP-1 now over

    asyncio.run(run())


def test_limiter_is_noop_without_redis():
    rl = RateLimiter(None)
    assert rl.is_stub is True

    async def run():
        for _ in range(50):  # far past any limit -> never raises
            await rl.hit(_request(), "auth", 1, 60)

    asyncio.run(run())
