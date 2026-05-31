"""Per-window feature extraction.

Given a 2.5 s window of shape (125, 6) with channels
    [ax, ay, az, wx, wy, wz]  (accel m/s², gyro rad/s)
produce a fixed-length feature vector of length 43.

This is the input to the **cloud** model. The **edge** model uses just the raw
6-channel window (no engineered features) because it must run in ≤80 KB INT8.

Feature inventory (43 total):

    Per-channel statistics (6 stats × 6 channels = 36):
        For each of (ax, ay, az, wx, wy, wz):
            mean, std, min, max, peak-to-peak (max-min), rms

    Whole-window summary (7):
        sma_accel            Signal Magnitude Area on accel:
                                 (1/N) Σ_t (|ax_t| + |ay_t| + |az_t|)
        mag_accel_peak       max_t |a|_t,  where |a|_t = sqrt(ax² + ay² + az²)
        mag_accel_mean       mean_t |a|_t
        mag_accel_std        std_t  |a|_t
        jerk_accel_max_abs   max_t |d|a|_t / dt|   (numerical derivative)
        freq_dominant_accel  frequency with the highest FFT amplitude on |a|
                                 (DC bin excluded; distinguishes walking from falls)
        spectral_entropy     -Σ p_i log(p_i) over the normalized power spectrum
                                 of |a| (low for periodic motion, high for transients)

Why these features matter for walking vs falling:

  - **Magnitude statistics** capture overall acceleration intensity. A fall has
    a much higher peak and std than walking.
  - **Jerk** is the rate of change of acceleration — a fall has a brief but
    enormous jerk at impact; walking is smooth.
  - **SMA** captures total movement work over the window. Bigger for any
    high-energy event.
  - **Spectral features** distinguish periodic motion (walking has a clear
    dominant frequency around 1.5–2 Hz, low entropy) from transient events
    (a fall is a broadband click — no clear dominant freq, high entropy).
"""
from __future__ import annotations

import numpy as np

# Names used by `feature_names()` — order matches the output of `extract_features()`.
RAW_CHANNEL_NAMES = ("ax", "ay", "az", "wx", "wy", "wz")
STAT_NAMES = ("mean", "std", "min", "max", "ptp", "rms")
EPS = 1e-12  # numerical safety for log + division


def magnitude(xyz: np.ndarray) -> np.ndarray:
    """L2 norm along the last axis: (..., 3) → (...,).

    For accel data, |a|_t = sqrt(ax² + ay² + az²) is the orientation-invariant
    intensity — the fundamental quantity for fall detection.
    """
    return np.sqrt(np.sum(xyz * xyz, axis=-1))


def jerk(magnitude_t: np.ndarray, dt_s: float) -> np.ndarray:
    """First derivative of a 1D signal (m/s³ when computed on accel magnitude).

    Uses numpy.gradient — central differences in the interior + forward/backward
    at the edges. dt_s should be 1/sample_rate (= 0.02 s at 50 Hz).
    """
    return np.gradient(magnitude_t, dt_s)


def per_channel_stats(window: np.ndarray) -> np.ndarray:
    """Per-channel summary stats: mean, std, min, max, peak-to-peak, rms.

    Parameters
    ----------
    window : (N, C) array.

    Returns
    -------
    (C * 6,) flattened in channel-major order:
        [ch0_mean, ch0_std, ch0_min, ch0_max, ch0_ptp, ch0_rms,
         ch1_mean, ... ]

    Matches the order of `STAT_NAMES` × `RAW_CHANNEL_NAMES` for legibility.
    """
    means = window.mean(axis=0)
    stds = window.std(axis=0)
    mins = window.min(axis=0)
    maxs = window.max(axis=0)
    ptps = maxs - mins
    rms = np.sqrt(np.mean(window**2, axis=0))
    stacked = np.column_stack([means, stds, mins, maxs, ptps, rms])   # (C, 6)
    return stacked.ravel()                                            # (C * 6,)


def signal_magnitude_area(accel_xyz: np.ndarray) -> float:
    """SMA: mean of |ax| + |ay| + |az| over the window.

    Captures total movement intensity — separates walking (rhythmic, low SMA)
    from falls (large transient, high SMA).
    """
    return float(np.mean(np.abs(accel_xyz).sum(axis=1)))


def fft_features(signal_1d: np.ndarray, sample_rate: int) -> tuple[float, float]:
    """Dominant frequency (Hz) + spectral entropy of a 1D signal.

    - Dominant frequency: argmax over the one-sided amplitude spectrum, with the
      DC bin excluded. Walking shows ~1.5–2 Hz; falls produce a broadband
      transient with no clear peak.
    - Spectral entropy: -Σ p_i log(p_i) over the normalized power spectrum.
      Periodic signals (walking) → low entropy. Transient/broadband (falls) →
      high entropy.

    The signal is demeaned first to suppress the constant gravity component
    when used on accel magnitude.
    """
    n = len(signal_1d)
    if n < 2:
        return 0.0, 0.0

    spec = np.abs(np.fft.rfft(signal_1d - signal_1d.mean()))
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)

    if len(spec) > 1:
        # argmax over spec[1:] — slicing skips the DC bin at index 0.
        # Add 1 back to map the sliced index to the original spec[] index.
        dom_idx = int(np.argmax(spec[1:])) + 1
        dominant = float(freqs[dom_idx])
    else:
        dominant = 0.0

    # Normalize power into a probability distribution → Shannon entropy.
    power = spec**2
    total = power.sum() + EPS
    p = power / total
    entropy = float(-np.sum(p * np.log(p + EPS)))

    return dominant, entropy


def extract_features(window: np.ndarray, sample_rate: int = 50) -> np.ndarray:
    """Extract the full 43-d engineered-feature vector from one window.

    Parameters
    ----------
    window : (N, 6) array with columns [ax, ay, az, wx, wy, wz]
        — accel in m/s², gyro in rad/s.
    sample_rate : Hz (default 50).

    Returns
    -------
    (43,) float32 feature vector. Order: see `feature_names()`.
    """
    if window.ndim != 2 or window.shape[1] != 6:
        raise ValueError(f"Expected window shape (N, 6); got {window.shape}")

    accel = window[:, :3]   # ax, ay, az
    # gyro = window[:, 3:]    # (currently unused beyond per-channel stats)
    dt = 1.0 / sample_rate

    feats: list[float] = []

    # 1. Per-channel stats: 6 channels × 6 stats = 36
    feats.extend(per_channel_stats(window).tolist())

    # 2. SMA on accel: 1 feature
    feats.append(signal_magnitude_area(accel))

    # 3. Accel-magnitude statistics: 3 features (peak, mean, std)
    mag_a = magnitude(accel)
    feats.append(float(mag_a.max()))
    feats.append(float(mag_a.mean()))
    feats.append(float(mag_a.std()))

    # 4. Jerk peak: 1 feature
    jerk_a = jerk(mag_a, dt)
    feats.append(float(np.max(np.abs(jerk_a))))

    # 5. FFT features on accel magnitude: 2 features
    dom_freq, spec_entropy = fft_features(mag_a, sample_rate=sample_rate)
    feats.append(dom_freq)
    feats.append(spec_entropy)

    return np.asarray(feats, dtype=np.float32)


def feature_names() -> list[str]:
    """Names of every feature produced by `extract_features()`, in order.

    Useful for MLflow logging + downstream feature-importance reports.
    """
    names: list[str] = []
    for ch in RAW_CHANNEL_NAMES:
        for stat in STAT_NAMES:
            names.append(f"{ch}_{stat}")
    names.extend(
        [
            "sma_accel",
            "mag_accel_peak",
            "mag_accel_mean",
            "mag_accel_std",
            "jerk_accel_max_abs",
            "freq_dominant_accel",
            "spectral_entropy_accel",
        ]
    )
    return names
