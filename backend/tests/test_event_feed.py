"""The live SSE caregiver feed (Phase 27) — broker + publish wiring + the gate.

The broker is Redis-gated exactly like the rate limiter: a no-op publisher with
no backplane when Redis is unset, so the stream endpoint 503s and the rest of the
suite runs without a server. Pub/sub against a real Redis is verified separately
(see BUILD_LOG). Here a tiny fake exercises the channel/JSON contract and proves a
confirmed fall is published even DB-less.
"""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.broker import EventBroker, channel_for
from app.config import get_settings
from app.main import create_app
from app.schemas import IMUSample, InferenceRequest, InferenceResponse, Severity
from app.services.event_store import EventStore

WINDOW = 125


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


class _FakeRedis:
    """Records publishes so we can assert the channel + payload."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


def _request() -> InferenceRequest:
    return InferenceRequest(
        device_id="dev-001",
        ts_start_unix_ms=1_700_000_000_000,
        samples=[IMUSample(ax=0.0, ay=0.0, az=9.81, wx=0.0, wy=0.0, wz=0.0) for _ in range(WINDOW)],
    )


def _verdict() -> InferenceResponse:
    return InferenceResponse(
        is_fall=True,
        confidence=0.97,
        severity=Severity.high,
        action="alert_caregiver",
        lead_time_ms=420.0,
        model_version="cloud-transformer-v0.1",
    )


# ─── broker (fake Redis) ─────────────────────────────────────────────────────


def test_broker_publishes_json_to_the_users_channel():
    redis = _FakeRedis()
    broker = EventBroker(redis)
    uid = uuid4()

    asyncio.run(broker.publish_fall(uid, {"type": "fall", "is_fall": True}))

    assert broker.is_stub is False
    assert redis.published == [(channel_for(uid), json.dumps({"type": "fall", "is_fall": True}))]


def test_broker_is_noop_without_redis():
    broker = EventBroker(None)
    assert broker.is_stub is True
    # Far past any backplane — must not raise and must publish nothing observable.
    asyncio.run(broker.publish_fall(uuid4(), {"x": 1}))


# ─── EventStore → broker wiring (DB-less) ────────────────────────────────────


def test_record_fall_publishes_alert_even_db_less():
    redis = _FakeRedis()
    store = EventStore(get_settings(), None, EventBroker(redis))  # db=None → no persistence
    uid = uuid4()

    event_id = asyncio.run(
        store.record_fall(_request(), _verdict(), user_id=uid, device_pk=uuid4())
    )

    assert event_id is None  # DB-less: nothing stored...
    assert len(redis.published) == 1  # ...but the caregiver alert still fired
    channel, raw = redis.published[0]
    assert channel == channel_for(uid)
    payload = json.loads(raw)
    assert payload["type"] == "fall"
    assert payload["event_id"] is None  # no stored row to deep-link into
    assert payload["device_id"] == "dev-001"
    assert payload["severity"] == "high"
    assert payload["is_fall"] is True


def test_record_fall_without_broker_is_inert():
    store = EventStore(get_settings(), None)  # no broker, no db
    event_id = asyncio.run(
        store.record_fall(_request(), _verdict(), user_id=uuid4(), device_pk=uuid4())
    )
    assert event_id is None  # no raise, nothing to publish


# ─── stream endpoint gate ────────────────────────────────────────────────────


def test_stream_requires_redis(client, user_headers):
    """No FG_REDIS_URL in the test env → the broker is a stub → 503."""
    assert client.app.state.event_broker.is_stub is True
    r = client.get("/v1/events/stream", headers=user_headers)
    assert r.status_code == 503


def test_stream_requires_a_user_token(client):
    r = client.get("/v1/events/stream")
    assert r.status_code == 401
