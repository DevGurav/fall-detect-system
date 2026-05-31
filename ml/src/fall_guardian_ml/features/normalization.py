"""Per-user Z-score normalization.

Justification (from 2026 research): individual differences in baseline IMU
patterns (resting wrist orientation, sensor calibration, build, walking style)
significantly affect feature distributions. A z-score normalizer fit
*per-user* — using only that user's ADL data, NOT their fall data — improves
cross-subject generalization measurably.

Usage:
    1. After pairing, the device records ~10–15 minutes of normal ADL.
    2. Compute per-feature mean + std from those windows.
    3. Store (mean, std) on-device for that user.
    4. At inference time, every feature vector is z-score-normalised with these
       per-user params before being fed to the model.

This keeps the model's effective input distribution stable across subjects.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class ZScoreParams:
    """Per-feature mean + std for z-score normalization."""

    mean: np.ndarray   # shape (n_features,)
    std: np.ndarray    # shape (n_features,)

    def transform(self, features: np.ndarray) -> np.ndarray:
        """Apply z-score to a (n_features,) or (n_windows, n_features) array.

        Constant features (std == 0) are mapped to 0 rather than dividing by zero.
        """
        safe_std = np.where(self.std > 0, self.std, 1.0)
        return (features - self.mean) / safe_std


def fit_zscore(features: np.ndarray) -> ZScoreParams:
    """Compute per-feature mean + std from a (N, F) feature matrix.

    Use ONLY the user's ADL (BACKGROUND-phase) windows for the fit — fall
    windows would skew the per-feature stats and defeat the purpose.
    """
    if features.ndim != 2:
        raise ValueError(f"Expected 2D array; got shape {features.shape}")
    return ZScoreParams(
        mean=features.mean(axis=0),
        std=features.std(axis=0),
    )
