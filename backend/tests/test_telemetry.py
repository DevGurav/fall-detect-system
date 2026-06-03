"""W2 telemetry + read side — DB-less contract tests and pure-logic units.

The DB-backed behavior (real inserts, timeline queries, acknowledge) needs
Postgres; here we assert the endpoints gate cleanly to 503 without a database,
that /v1/inference still returns a verdict (fall persistence no-ops DB-less), and
unit-test the derived online/offline status. End-to-end DB tests run against a
live Postgres (see backend/README.md).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from app.schemas import HeartbeatRequest
from app.services.device_service import derive_status

WINDOW = 125
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def _window(az: float = 9.81):
    return [{"ax": 0.0, "ay": 0.0, "az": az, "wx": 0.0, "wy": 0.0, "wz": 0.0} for _ in range(WINDOW)]


def _request(samples):
    return {
        "device_id": "dev-001",
        "ts_start_unix_ms": 1_700_000_000_000,
        "sample_rate_hz": 50,
        "samples": samples,
    }


@pytest.fixture
def client():
    """Default app — real ONNX detector, no database (DB-less mode)."""
    with TestClient(create_app()) as c:
        yield c


# ─── DB-less: the telemetry + read endpoints gate to 503 ─────────────────────


def test_heartbeat_requires_db(client):
    r = client.post("/v1/devices/heartbeat", json={"device_id": "dev-001", "battery_pct": 80})
    assert r.status_code == 503


def test_list_devices_requires_db(client):
    assert client.get("/v1/devices").status_code == 503


def test_list_events_requires_db(client):
    assert client.get("/v1/events").status_code == 503


def test_acknowledge_requires_db(client):
    r = client.post(f"/v1/events/{_ZERO_UUID}/acknowledge")
    assert r.status_code == 503  # 503 gates before the (absent) row is ever looked up


def test_heartbeat_request_rejects_out_of_range_battery():
    # The battery bound lives on the schema, independent of the DB gate.
    with pytest.raises(ValidationError):
        HeartbeatRequest(device_id="d", battery_pct=150)
    assert HeartbeatRequest(device_id="d", battery_pct=80).battery_pct == 80


# ─── /v1/inference stays available DB-less (fall persistence is a no-op) ──────


def test_inference_returns_verdict_without_db(client):
    r = client.post("/v1/inference", json=_request(_window()))
    assert r.status_code == 200
    assert isinstance(r.json()["is_fall"], bool)


def test_inference_fall_window_does_not_error_without_db(client):
    samples = _window()
    samples[60] = {"ax": 0.0, "ay": 0.0, "az": 35.0, "wx": 0.0, "wy": 0.0, "wz": 0.0}
    # Whatever the verdict, the is_fall -> record_fall path must no-op cleanly DB-less.
    assert client.post("/v1/inference", json=_request(samples)).status_code == 200


# ─── pure logic: derived device status ───────────────────────────────────────


def test_derive_status_transitions():
    now = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)
    assert derive_status(None, now, 600) == "unknown"
    assert derive_status(now - timedelta(seconds=60), now, 600) == "online"
    assert derive_status(now - timedelta(seconds=601), now, 600) == "offline"
