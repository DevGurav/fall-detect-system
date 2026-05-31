"""Tests for the feature extraction pipeline.

Verify math correctness on simple synthetic inputs.
"""
from __future__ import annotations

import numpy as np
import pytest

from fall_guardian_ml.features.extraction import (
    extract_features,
    feature_names,
    fft_features,
    jerk,
    magnitude,
    per_channel_stats,
    signal_magnitude_area,
)


# ─── magnitude ───────────────────────────────────────────────────────────────

def test_magnitude_classic_3_4_5_triangle():
    xyz = np.array([[3.0, 4.0, 0.0]])
    assert magnitude(xyz)[0] == pytest.approx(5.0)


def test_magnitude_resting_wrist_is_1g():
    """A wrist at rest reads ~[0, 0, 9.81] → |a| ≈ 9.81."""
    xyz = np.array([[0.0, 0.0, 9.81]])
    assert magnitude(xyz)[0] == pytest.approx(9.81)


def test_magnitude_batched():
    """magnitude works on a full (T, 3) window, not just one sample."""
    xyz = np.array([[3.0, 4.0, 0.0], [0.0, 0.0, 5.0], [1.0, 0.0, 0.0]])
    expected = np.array([5.0, 5.0, 1.0])
    assert np.allclose(magnitude(xyz), expected)


# ─── jerk ────────────────────────────────────────────────────────────────────

def test_jerk_constant_signal_is_zero():
    """Derivative of a constant is 0 (within float precision)."""
    sig = np.full(100, 9.81)
    j = jerk(sig, dt_s=1.0 / 50)
    assert np.allclose(j, 0.0)


def test_jerk_linear_signal_has_constant_derivative():
    """A signal sig(t) = 2*t has jerk = 2 everywhere in the interior."""
    fs = 50
    t = np.arange(100) / fs
    sig = 2.0 * t   # slope = 2.0 m/s² per second
    j = jerk(sig, dt_s=1.0 / fs)
    # Interior points should be exactly 2.0; edge points are fine to be slightly off
    assert np.allclose(j[5:-5], 2.0, atol=1e-9)


# ─── per_channel_stats ───────────────────────────────────────────────────────

def test_per_channel_stats_shape_and_layout():
    """6 channels × 6 stats = 36 features, in channel-major order."""
    window = np.tile(np.arange(125, dtype=float).reshape(-1, 1), (1, 6))
    stats = per_channel_stats(window)
    assert stats.shape == (36,)
    # Channel 0: mean of 0..124 = 62.0, std ~ 36.08, min=0, max=124, ptp=124, rms ~ 71.91
    expected_mean = np.mean(np.arange(125))    # 62.0
    expected_std = np.std(np.arange(125))      # ≈ 36.08
    assert stats[0] == pytest.approx(expected_mean)
    assert stats[1] == pytest.approx(expected_std)
    assert stats[2] == pytest.approx(0.0)
    assert stats[3] == pytest.approx(124.0)
    assert stats[4] == pytest.approx(124.0)    # ptp
    # rms = sqrt(mean(x^2))
    expected_rms = np.sqrt(np.mean(np.arange(125)**2))
    assert stats[5] == pytest.approx(expected_rms)


# ─── signal_magnitude_area ───────────────────────────────────────────────────

def test_sma_higher_for_fall_than_walking():
    """A 'fall' window with a transient spike has higher SMA than steady walking."""
    n = 125
    fs = 50
    t = np.arange(n) / fs

    walking = np.zeros((n, 3))
    walking[:, 2] = 9.81 + 0.5 * np.sin(2 * np.pi * 1.5 * t)   # 1.5 Hz wrist sway

    fall = np.zeros((n, 3))
    fall[:, 2] = 9.81
    fall[60, :] = [20.0, 25.0, 30.0]                          # impact spike

    assert signal_magnitude_area(fall) > signal_magnitude_area(walking)


def test_sma_classic_formula():
    """SMA(a) where a is constant [1, 2, 3] → mean(|1|+|2|+|3|) = 6.0."""
    n = 100
    accel = np.broadcast_to(np.array([1.0, 2.0, 3.0]), (n, 3))
    assert signal_magnitude_area(accel) == pytest.approx(6.0)


# ─── fft_features ────────────────────────────────────────────────────────────

def test_fft_features_pick_dominant_frequency_5hz():
    """A pure 5 Hz sine wave → dominant frequency should be ~5 Hz."""
    fs = 50
    n = 250
    t = np.arange(n) / fs
    sig = np.sin(2 * np.pi * 5.0 * t)

    dom, _entropy = fft_features(sig, sample_rate=fs)
    # FFT bin width is fs/n = 0.2 Hz, so 5.0 should land cleanly on a bin.
    assert dom == pytest.approx(5.0, abs=0.25)


def test_fft_features_walking_lower_entropy_than_noise():
    """Periodic walking signal has lower spectral entropy than white noise."""
    fs = 50
    n = 250
    t = np.arange(n) / fs
    walking = np.sin(2 * np.pi * 1.8 * t)              # clean 1.8 Hz oscillation
    rng = np.random.default_rng(seed=0)
    noise = rng.standard_normal(n)                     # broadband

    _, ent_walk = fft_features(walking, sample_rate=fs)
    _, ent_noise = fft_features(noise, sample_rate=fs)
    assert ent_walk < ent_noise


# ─── extract_features ────────────────────────────────────────────────────────

def test_extract_features_output_shape_and_no_nans():
    rng = np.random.default_rng(seed=42)
    window = rng.standard_normal((125, 6))
    feats = extract_features(window, sample_rate=50)
    names = feature_names()

    assert feats.shape == (len(names),)
    assert feats.shape == (43,)
    assert not np.any(np.isnan(feats))
    assert feats.dtype == np.float32


def test_extract_features_rejects_wrong_shape():
    with pytest.raises(ValueError, match="Expected window shape"):
        extract_features(np.zeros((125, 5)))   # only 5 channels — should fail
    with pytest.raises(ValueError, match="Expected window shape"):
        extract_features(np.zeros(125))        # 1D — should fail


def test_feature_names_unique_and_correct_length():
    names = feature_names()
    assert len(names) == 43
    assert len(set(names)) == 43               # all unique
    # Spot-check: per-channel stats should come first, in channel-major order
    assert names[0] == "ax_mean"
    assert names[5] == "ax_rms"
    assert names[6] == "ay_mean"
    # SMA + magnitude + freq features at the end
    assert "sma_accel" in names
    assert "jerk_accel_max_abs" in names
    assert "freq_dominant_accel" in names
    assert "spectral_entropy_accel" in names


def test_feature_separability_fall_vs_walking():
    """Sanity check: feature distributions for synthetic fall vs walking differ
    in the directions you'd expect (peak magnitude, jerk, entropy)."""
    fs = 50
    n = 125
    t = np.arange(n) / fs

    walking = np.zeros((n, 6))
    walking[:, 2] = 9.81 + 0.5 * np.sin(2 * np.pi * 1.8 * t)   # gentle 1.8 Hz wrist sway
    walking[:, 0] = 0.3 * np.sin(2 * np.pi * 1.8 * t)           # ax also has it

    fall = walking.copy()
    fall[60, :3] = [10.0, 12.0, 25.0]                          # impact spike

    f_walk = extract_features(walking, sample_rate=fs)
    f_fall = extract_features(fall, sample_rate=fs)
    names = feature_names()

    peak_idx = names.index("mag_accel_peak")
    jerk_idx = names.index("jerk_accel_max_abs")
    entropy_idx = names.index("spectral_entropy_accel")

    assert f_fall[peak_idx] > f_walk[peak_idx]      # falls have higher peak magnitude
    assert f_fall[jerk_idx] > f_walk[jerk_idx]      # falls have higher peak jerk
    assert f_fall[entropy_idx] > f_walk[entropy_idx]  # falls = broadband → higher entropy
