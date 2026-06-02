"""43-d engineered feature extraction for the cloud detector (serving copy).

**Canonical source:** `ml/src/fall_guardian_ml/features/extraction.py`. This is a
verbatim vendored copy so the torch-free gateway can compute the model's fused
feature input without depending on the ML package. It MUST stay byte-for-byte
equivalent to the trainer's extractor — train/serve skew here silently corrupts
predictions. `tests/test_features_parity.py` guards a few known values; if the ML
extractor changes, re-export the model and update this file together.

Given a 2.5 s window of shape (125, 6) with channels [ax, ay, az, wx, wy, wz],
produce a fixed-length 43-d feature vector (36 per-channel stats + 7 whole-window).
"""
from __future__ import annotations

import numpy as np

RAW_CHANNEL_NAMES = ("ax", "ay", "az", "wx", "wy", "wz")
STAT_NAMES = ("mean", "std", "min", "max", "ptp", "rms")
EPS = 1e-12


def magnitude(xyz: np.ndarray) -> np.ndarray:
    """L2 norm along the last axis: (..., 3) -> (...,)."""
    return np.sqrt(np.sum(xyz * xyz, axis=-1))


def jerk(magnitude_t: np.ndarray, dt_s: float) -> np.ndarray:
    """First derivative of a 1D signal (central differences via numpy.gradient)."""
    return np.gradient(magnitude_t, dt_s)


def per_channel_stats(window: np.ndarray) -> np.ndarray:
    """Per-channel mean, std, min, max, peak-to-peak, rms -> (C*6,), channel-major."""
    means = window.mean(axis=0)
    stds = window.std(axis=0)
    mins = window.min(axis=0)
    maxs = window.max(axis=0)
    ptps = maxs - mins
    rms = np.sqrt(np.mean(window**2, axis=0))
    return np.column_stack([means, stds, mins, maxs, ptps, rms]).ravel()


def signal_magnitude_area(accel_xyz: np.ndarray) -> float:
    """SMA: mean of |ax| + |ay| + |az| over the window."""
    return float(np.mean(np.abs(accel_xyz).sum(axis=1)))


def fft_features(signal_1d: np.ndarray, sample_rate: int) -> tuple[float, float]:
    """Dominant frequency (Hz, DC excluded) + spectral entropy of a demeaned 1D signal."""
    n = len(signal_1d)
    if n < 2:
        return 0.0, 0.0

    spec = np.abs(np.fft.rfft(signal_1d - signal_1d.mean()))
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)

    if len(spec) > 1:
        dom_idx = int(np.argmax(spec[1:])) + 1
        dominant = float(freqs[dom_idx])
    else:
        dominant = 0.0

    power = spec**2
    total = power.sum() + EPS
    p = power / total
    entropy = float(-np.sum(p * np.log(p + EPS)))
    return dominant, entropy


def extract_features(window: np.ndarray, sample_rate: int = 50) -> np.ndarray:
    """Extract the full 43-d engineered-feature vector from one (125, 6) window."""
    if window.ndim != 2 or window.shape[1] != 6:
        raise ValueError(f"Expected window shape (N, 6); got {window.shape}")

    accel = window[:, :3]
    dt = 1.0 / sample_rate

    feats: list[float] = []
    feats.extend(per_channel_stats(window).tolist())            # 36
    feats.append(signal_magnitude_area(accel))                  # 1
    mag_a = magnitude(accel)
    feats.append(float(mag_a.max()))                            # 3 (peak, mean, std)
    feats.append(float(mag_a.mean()))
    feats.append(float(mag_a.std()))
    feats.append(float(np.max(np.abs(jerk(mag_a, dt)))))        # 1
    dom_freq, spec_entropy = fft_features(mag_a, sample_rate=sample_rate)  # 2
    feats.append(dom_freq)
    feats.append(spec_entropy)

    return np.asarray(feats, dtype=np.float32)


def feature_names() -> list[str]:
    """Names of every feature produced by `extract_features()`, in order."""
    names: list[str] = []
    for ch in RAW_CHANNEL_NAMES:
        for stat in STAT_NAMES:
            names.append(f"{ch}_{stat}")
    names.extend([
        "sma_accel", "mag_accel_peak", "mag_accel_mean", "mag_accel_std",
        "jerk_accel_max_abs", "freq_dominant_accel", "spectral_entropy_accel",
    ])
    return names
