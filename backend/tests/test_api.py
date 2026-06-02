"""Smoke + contract tests for the cloud gateway skeleton (TestClient, no server)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

WINDOW = 125


def _window(ax: float = 0.0, ay: float = 0.0, az: float = 9.81):
    return [{"ax": ax, "ay": ay, "az": az, "wx": 0.0, "wy": 0.0, "wz": 0.0} for _ in range(WINDOW)]


def _request(samples):
    return {
        "device_id": "dev-001",
        "ts_start_unix_ms": 1_700_000_000_000,
        "sample_rate_hz": 50,
        "samples": samples,
        "edge_prediction": {"p_pre_impact": 0.92, "model_version": "convlstm-tiny-v0.3"},
    }


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["model_version"] == "stub-0.0"


def test_inference_resting_window_is_not_fall(client):
    r = client.post("/v1/inference", json=_request(_window()))  # ~1g resting
    assert r.status_code == 200
    body = r.json()
    assert body["is_fall"] is False
    assert body["severity"] == "none"
    assert body["action"] == "suppress"


def test_inference_impact_window_is_fall(client):
    # One hard impact sample (>30 m/s²) → stub flags high-severity fall.
    samples = _window()
    samples[60] = {"ax": 0.0, "ay": 0.0, "az": 35.0, "wx": 0.0, "wy": 0.0, "wz": 0.0}
    r = client.post("/v1/inference", json=_request(samples))
    assert r.status_code == 200
    body = r.json()
    assert body["is_fall"] is True
    assert body["severity"] == "high"
    assert body["action"] == "alert_caregiver"


def test_inference_rejects_wrong_window_length(client):
    r = client.post("/v1/inference", json=_request(_window()[:10]))
    assert r.status_code == 422  # Pydantic rejects != 125 samples
