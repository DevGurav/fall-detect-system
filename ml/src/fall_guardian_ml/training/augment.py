"""Light, physics-aware augmentation for the edge training set.

The fall pool is small (14 young subjects), so the model overfits the specific
way *those* people fell. Augmentation manufactures plausible variants of each
training window so the model sees more fall diversity without new data collection.

Two transforms, applied on-the-fly to TRAINING windows only (never val/test —
that would be evaluating on synthetic data):

  • Time-warp — resample the time axis by a small factor (±10%), mimicking a
    slightly faster/slower fall or activity. Applied to ALL channels; warping is
    a geometric reparametrisation of time and is valid for every signal.

  • Magnitude scaling — multiply by a small factor (±10%), mimicking subject
    strength / sensor-sensitivity variation. Applied ONLY to the dynamic channels
    (accel + gyro). The orientation quaternion is (near-)unit-norm and encodes a
    rotation — scaling it is physically meaningless and would corrupt the signal,
    so those channels are left untouched.

Augmentation runs on RAW windows (before standardization), so the factors mean
what they physically should. Each transform fires independently with prob `p`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class AugmentConfig:
    enabled: bool = True
    p: float = 0.5             # per-transform apply probability
    mag_scale: float = 0.10    # magnitude scaling range (± fraction)
    time_warp: float = 0.10    # time-warp range (± fraction)
    n_dynamic_channels: int = 6  # accel(3)+gyro(3) get scaled; orientation does not


def augment_window(window: np.ndarray, rng: np.random.Generator, cfg: AugmentConfig) -> np.ndarray:
    """Return an augmented copy of a single (T, C) raw window."""
    out = window.astype(np.float32, copy=True)
    T = out.shape[0]
    n_dyn = min(cfg.n_dynamic_channels, out.shape[1])

    # Time-warp (all channels): read each channel at warped sample positions.
    if rng.random() < cfg.p:
        factor = 1.0 + rng.uniform(-cfg.time_warp, cfg.time_warp)
        src = np.arange(T)
        pos = np.clip(src * factor, 0.0, T - 1)
        out = np.stack(
            [np.interp(pos, src, out[:, c]) for c in range(out.shape[1])], axis=1
        ).astype(np.float32)

    # Magnitude scaling (dynamic channels only).
    if rng.random() < cfg.p:
        scale = 1.0 + rng.uniform(-cfg.mag_scale, cfg.mag_scale)
        out[:, :n_dyn] *= scale

    return out
