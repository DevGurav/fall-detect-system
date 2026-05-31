"""Tests for pre-impact label re-derivation.

These tests use synthetic signals with KNOWN impact moments so we can verify
the math is correct independent of the real WEDA-FALL data.
"""
from __future__ import annotations

import numpy as np
import pytest

from fall_guardian_ml.datasets.pre_impact_labels import (
    FALL_MAG_THRESHOLD_MS2,
    Phase,
    assign_phase_labels,
    find_impact,
)


# ─── find_impact ─────────────────────────────────────────────────────────────

def _build_synthetic_fall(fs: int = 50, duration_s: float = 5.0, impact_t: float = 3.0):
    """A 5-second synthetic 'recording' with a known impact moment at t=3.0s."""
    t = np.arange(int(duration_s * fs)) / fs
    accel = np.zeros((len(t), 3))
    accel[:, 2] = 9.81                            # 1g baseline on z (wrist at rest)

    # Free-fall phase from t=2.5s to t=3.0s: reduced gravity component
    free_fall_mask = (t >= 2.5) & (t < impact_t)
    accel[free_fall_mask, 2] = 6.0

    # Impact spike at exactly t=impact_t: large transient
    impact_idx = int(impact_t * fs)
    accel[impact_idx, :] = [10.0, 12.0, 25.0]    # |a| ≈ 30.0 m/s²

    return t, accel


def test_find_impact_locates_known_peak_within_one_sample():
    fs = 50
    t, accel = _build_synthetic_fall(fs=fs, impact_t=3.0)
    ann = find_impact(t, accel, label_window=(2.5, 4.0))

    assert ann.valid is True
    # Peak magnitude = sqrt(10² + 12² + 25²)
    expected_peak = np.sqrt(10**2 + 12**2 + 25**2)
    assert ann.peak_magnitude_ms2 == pytest.approx(expected_peak, rel=1e-6)
    # t_impact should match the truth to within one sample period (1/50 s = 20 ms)
    assert abs(ann.t_impact_s - 3.0) < (1.0 / fs)
    # Lag from label start: 3.0 - 2.5 = 0.5 s
    assert ann.lag_from_label_start_s == pytest.approx(0.5, abs=1.0 / fs)


def test_find_impact_rejects_below_threshold_peak():
    """A 'fall' whose peak doesn't exceed the 2g threshold is marked invalid."""
    fs = 50
    t = np.arange(int(5.0 * fs)) / fs
    accel = np.zeros((len(t), 3))
    accel[:, 2] = 9.81
    # Tiny perturbation — peak |a| ≈ 11 m/s², below the 20 threshold
    accel[150, :] = [3.0, 3.0, 9.81]

    ann = find_impact(t, accel, label_window=(2.0, 4.0))
    assert ann.valid is False
    assert "below threshold" in ann.reason


def test_find_impact_constrains_to_label_window():
    """A huge spike OUTSIDE the labeled window must not be picked as the impact."""
    fs = 50
    t = np.arange(int(5.0 * fs)) / fs
    accel = np.zeros((len(t), 3))
    accel[:, 2] = 9.81
    # Real impact at t=3.0s, magnitude ~30
    accel[int(3.0 * fs), :] = [10.0, 12.0, 25.0]
    # Distractor spike at t=0.5s (outside the labeled window), magnitude ~100
    accel[int(0.5 * fs), :] = [60.0, 60.0, 60.0]

    ann = find_impact(t, accel, label_window=(2.5, 4.0))
    assert ann.valid is True
    # Should pick the real impact, not the distractor
    assert abs(ann.t_impact_s - 3.0) < (1.0 / fs)


def test_find_impact_handles_empty_window():
    """A label window that doesn't overlap the recording → not valid."""
    fs = 50
    t = np.arange(int(2.0 * fs)) / fs           # recording covers t=[0, 2)
    accel = np.zeros((len(t), 3))
    accel[:, 2] = 9.81

    ann = find_impact(t, accel, label_window=(5.0, 7.0))    # window after recording ends
    assert ann.valid is False
    assert "No samples in label window" in ann.reason


def test_find_impact_raises_on_shape_mismatch():
    with pytest.raises(ValueError, match="must align on axis 0"):
        find_impact(np.arange(100), np.zeros((50, 3)), label_window=(0.0, 1.0))


# ─── assign_phase_labels ─────────────────────────────────────────────────────

def test_assign_phase_labels_segments_correctly():
    """Verify the (lead, guard, tail) timing produces the expected phase regions."""
    fs = 50
    t = np.arange(int(5.0 * fs)) / fs

    t_impact = 3.0
    fall_window = (2.5, 4.0)
    # Defaults: 500 ms lead, 50 ms guard, 500 ms tail
    labels = assign_phase_labels(t, t_impact_s=t_impact, fall_window=fall_window)

    # Phase boundaries:
    #   PRE_IMPACT  = [max(2.5, 3.0-0.5)=2.5, 3.0-0.05=2.95)  → samples at t∈[2.5, 2.95)
    #   IMPACT      = [2.95, 3.0+0.5=3.5)                     → samples at t∈[2.95, 3.5)
    #   POST_IMPACT = [3.5, 4.0]                              → samples at t∈[3.5, 4.0]
    #   BACKGROUND  = elsewhere

    assert labels[int(2.7 * fs)] == Phase.PRE_IMPACT.value     # inside pre-impact
    assert labels[int(3.1 * fs)] == Phase.IMPACT.value         # inside impact
    assert labels[int(3.7 * fs)] == Phase.POST_IMPACT.value    # inside post-impact
    assert labels[int(1.0 * fs)] == Phase.BACKGROUND.value     # before fall window
    assert labels[int(4.5 * fs)] == Phase.BACKGROUND.value     # after fall window


def test_assign_phase_labels_clamps_pre_window_to_fall_start():
    """If t_impact - lead < fall_start, PRE_IMPACT starts at fall_start (no leakage)."""
    fs = 50
    t = np.arange(int(5.0 * fs)) / fs
    # Tight fall window — only 300 ms wide, less than the 500 ms lead.
    fall_window = (2.9, 3.2)
    labels = assign_phase_labels(t, t_impact_s=3.0, fall_window=fall_window)

    # Sample just before the window (t=2.88s) should NOT be PRE_IMPACT —
    # it would have been if we naively used t_impact-500ms = 2.5s.
    assert labels[int(2.88 * fs)] == Phase.BACKGROUND.value
    # Sample inside the clamped pre-impact range (e.g. t=2.92s)
    assert labels[int(2.92 * fs)] == Phase.PRE_IMPACT.value


def test_pure_adl_recording_returns_all_background():
    fs = 50
    t = np.arange(int(10.0 * fs)) / fs
    labels = assign_phase_labels(t, t_impact_s=None, fall_window=None)
    assert labels.dtype == np.int8
    assert np.all(labels == Phase.BACKGROUND.value)


def test_phase_enum_polarity_helpers():
    """PRE_IMPACT positive for prediction model; IMPACT/POST_IMPACT for detection."""
    assert Phase.PRE_IMPACT.is_positive_for_prediction
    assert not Phase.IMPACT.is_positive_for_prediction
    assert Phase.IMPACT.is_positive_for_detection
    assert Phase.POST_IMPACT.is_positive_for_detection
    assert not Phase.BACKGROUND.is_positive_for_detection
    assert not Phase.PRE_IMPACT.is_positive_for_detection
