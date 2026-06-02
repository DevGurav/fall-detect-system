"""Smoke + contract tests for the cloud gateway (TestClient, no server).

The committed ONNX model is loaded by default (`client`), so detection-value
assertions are made against a forced-stub fixture (`stub_client`); the real-model
tests assert the response contract, not specific verdicts.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

WINDOW = 125
SEVERITIES = {"none", "low", "medium", "high"}


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
    """Default app — loads the committed ONNX detector."""
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def stub_client(monkeypatch, tmp_path):
    """Force stub mode by pointing the model path at a non-existent file."""
    monkeypatch.setenv("FG_MODEL_PATH", str(tmp_path / "absent.onnx"))
    with TestClient(create_app()) as c:
        yield c


# ─── real model (committed ONNX) ─────────────────────────────────────────────


def test_health_reports_real_model(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["model_version"].startswith("cloud-transformer")


def test_real_model_is_loaded_not_stub(client):
    assert client.app.state.detector.is_stub is False


def test_inference_real_model_contract(client):
    """The trained model returns a valid InferenceResponse (verdict is model-dependent)."""
    r = client.post("/v1/inference", json=_request(_window()))
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["is_fall"], bool)
    assert 0.0 <= body["confidence"] <= 1.0
    assert body["severity"] in SEVERITIES
    assert body["action"] in {"alert_caregiver", "suppress"}
    assert body["model_version"].startswith("cloud-transformer")


# ─── contract / routing (model-agnostic) ─────────────────────────────────────


def test_inference_rejects_wrong_window_length(client):
    r = client.post("/v1/inference", json=_request(_window()[:10]))
    assert r.status_code == 422  # Pydantic rejects != 125 samples


def test_inference_accepts_explicit_emergency_payload_type(client):
    body = _request(_window())
    body["payload_type"] = "emergency"
    r = client.post("/v1/inference", json=body)
    assert r.status_code == 200


def test_retraining_stores_canceled_false_alarm(client):
    r = client.post("/v1/retraining", json=_request(_window()))
    assert r.status_code == 200
    body = r.json()
    assert body["stored"] is True
    assert body["label"] == "CANCELED_FALSE_ALARM"
    assert body["sample_id"]


def test_retraining_skips_the_detector(client):
    samples = _window()
    samples[60] = {"ax": 0.0, "ay": 0.0, "az": 35.0, "wx": 0.0, "wy": 0.0, "wz": 0.0}

    def _boom(_req):
        raise AssertionError("CloudDetector must not run on the retraining path")

    client.app.state.detector.predict = _boom

    r = client.post("/v1/retraining", json=_request(samples))
    assert r.status_code == 200
    body = r.json()
    assert body["label"] == "CANCELED_FALSE_ALARM"
    assert "is_fall" not in body and "severity" not in body


def test_retraining_rejects_wrong_window_length(client):
    r = client.post("/v1/retraining", json=_request(_window()[:10]))
    assert r.status_code == 422


def test_retraining_rejects_emergency_payload_type(client):
    body = _request(_window())
    body["payload_type"] = "emergency"
    r = client.post("/v1/retraining", json=body)
    assert r.status_code == 422


# ─── stub fallback (no model artifact) ───────────────────────────────────────


def test_stub_mode_when_model_absent(stub_client):
    assert stub_client.app.state.detector.is_stub is True
    assert stub_client.get("/health").json()["model_version"] == "stub-0.0"


def test_stub_resting_window_is_not_fall(stub_client):
    r = stub_client.post("/v1/inference", json=_request(_window()))  # ~1g resting
    assert r.status_code == 200
    body = r.json()
    assert body["is_fall"] is False
    assert body["severity"] == "none"
    assert body["action"] == "suppress"


def test_stub_impact_window_is_fall(stub_client):
    samples = _window()
    samples[60] = {"ax": 0.0, "ay": 0.0, "az": 35.0, "wx": 0.0, "wy": 0.0, "wz": 0.0}
    r = stub_client.post("/v1/inference", json=_request(samples))
    assert r.status_code == 200
    body = r.json()
    assert body["is_fall"] is True
    assert body["severity"] == "high"
    assert body["action"] == "alert_caregiver"
