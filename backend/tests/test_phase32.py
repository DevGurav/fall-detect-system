"""Phase 32 — readiness probe + per-request trace_id (TestClient, no server)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


# ─── readiness probe ─────────────────────────────────────────────────────────


def test_readiness_ready_when_optional_infra_absent(client):
    """DB-less + Redis-less is still 'ready': those checks are skipped, not errored."""
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    by_name = {c["name"]: c for c in body["checks"]}
    assert by_name["database"]["status"] == "skipped"
    assert by_name["redis"]["status"] == "skipped"
    assert by_name["model"]["status"] == "ok"


def test_readiness_returns_503_when_a_configured_dep_errors(client, monkeypatch):
    """A configured-but-unreachable dependency flips readiness to 503/degraded."""

    class _BrokenRedis:
        async def ping(self):
            raise ConnectionError("redis down")

    monkeypatch.setattr(client.app.state, "redis", _BrokenRedis())
    r = client.get("/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    redis_check = next(c for c in body["checks"] if c["name"] == "redis")
    assert redis_check["status"] == "error"


# ─── trace_id middleware ─────────────────────────────────────────────────────


def test_response_carries_request_id_header(client):
    assert client.get("/health").headers.get("x-request-id")


def test_inbound_request_id_is_echoed(client):
    r = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers.get("x-request-id") == "trace-abc-123"
