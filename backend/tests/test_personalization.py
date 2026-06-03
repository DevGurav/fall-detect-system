"""W3 personalization seam — the detector applies a device's calibration profile.

Deterministic and offline (uses the committed ONNX model, no DB). The key trick:
a `threshold_override` of 1.1 can never be met (prob <= 1) so the fall is always
suppressed, and 0.0 is always met (prob > 0) so it's always confirmed — which
proves the override is wired into `_model_predict` without depending on the model's
exact probability for a given window. Also checks the per-field fallback to the
model's global stats, and that the lookup store is a no-op without a database.
"""
from __future__ import annotations

import asyncio

import pytest

from app.config import get_settings
from app.schemas import InferenceRequest
from app.services.calibration_store import CalibrationStore
from app.services.detector import CalibrationProfile, CloudDetector


@pytest.fixture(scope="module")
def detector():
    det = CloudDetector(get_settings())  # loads the committed ONNX artifact
    if det.is_stub:
        pytest.skip("real model artifact not present")
    return det


def _req(az: float = 9.81) -> InferenceRequest:
    samples = [{"ax": 0.0, "ay": 0.0, "az": az, "wx": 0.0, "wy": 0.0, "wz": 0.0} for _ in range(125)]
    return InferenceRequest(device_id="t", ts_start_unix_ms=0, sample_rate_hz=50, samples=samples)


# ─── threshold_override is applied (deterministic via the real model) ────────


def test_threshold_override_forces_suppress(detector):
    verdict = detector.predict(_req(), CalibrationProfile(threshold_override=1.1))
    assert verdict.is_fall is False
    assert verdict.action == "suppress"


def test_threshold_override_forces_confirm(detector):
    verdict = detector.predict(_req(), CalibrationProfile(threshold_override=0.0))
    assert verdict.is_fall is True
    assert verdict.action == "alert_caregiver"


def test_no_profile_uses_global_threshold(detector):
    # A resting window sits well below the global threshold -> suppressed either way.
    assert detector.predict(_req()).is_fall is False
    assert detector.predict(_req(), None).is_fall is False


# ─── per-field fallback to the model's global stats ──────────────────────────


def test_channel_stats_use_profile_or_fall_back(detector):
    global_stats = detector._meta["channel_stats"]
    assert detector._channel_stats(None) is global_stats
    good = CalibrationProfile(channel_mean=[0.0] * 6, channel_std=[1.0] * 6)
    assert detector._channel_stats(good) == {"mean": [0.0] * 6, "std": [1.0] * 6}
    # wrong-length vectors are ignored -> global stats (guards against bad data)
    bad = CalibrationProfile(channel_mean=[0.0, 0.0], channel_std=[1.0, 1.0])
    assert detector._channel_stats(bad) is global_stats


def test_feature_norm_falls_back_when_partial(detector):
    global_norm = detector._meta["feature_norm"]
    partial = CalibrationProfile(feature_mean=[0.0] * 43)  # std missing -> unusable
    assert detector._feature_norm(partial) is global_norm


# ─── lookup store is a no-op without a database ──────────────────────────────


def test_calibration_store_returns_none_without_db():
    store = CalibrationStore(get_settings(), None)
    assert store.is_stub is True
    assert asyncio.run(store.get("any-device")) is None
