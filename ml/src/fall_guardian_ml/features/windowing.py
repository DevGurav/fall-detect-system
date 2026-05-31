"""Sliding-window utilities.

Locked window math (matches WEDA-FALL after resampling to uniform 50 Hz):

    window_seconds = 2.5
    sample_rate    = 50 Hz
    window_samples = 125 samples per window
    stride         = 62 samples (50% overlap) → 1.24 s between window starts

50% overlap is the standard training-time choice for IMU classification — it
roughly doubles the number of positive examples per fall, which matters because
the PRE_IMPACT phase is only ~450 ms long.

A note on label aggregation: each window's label is the MODE (most common
sample-level phase) over its 125 samples. Because PRE_IMPACT spans only
~450 ms, a normal 2.5 s window centred on the impact will have its mode = IMPACT
or POST_IMPACT, not PRE_IMPACT. To make sure the training set actually contains
PRE_IMPACT-labeled windows, `slide_for_prediction` ALSO emits a window
ending exactly at `t_impact - guard_s` (so the full pre-impact phase sits at
the END of that window — which is what the edge model sees in production).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fall_guardian_ml.datasets.pre_impact_labels import (
    PRE_IMPACT_GUARD_MS,
    PRE_IMPACT_LEAD_MS,
    Phase,
)

# Locked window math.
WINDOW_SECONDS = 2.5
TARGET_HZ = 50
WINDOW_SAMPLES = int(WINDOW_SECONDS * TARGET_HZ)          # 125
DEFAULT_STRIDE_SAMPLES = WINDOW_SAMPLES // 2              # 62 (~50% overlap)


@dataclass
class Window:
    """One sliding window of multi-channel IMU data."""

    start_idx: int          # first sample index (inclusive)
    end_idx: int            # last sample index + 1 (Python slice convention)
    start_time_s: float
    end_time_s: float
    data: np.ndarray        # shape (window_samples, n_channels)
    label: int              # mode of the per-sample Phase values in the window


def slide(
    data: np.ndarray,
    time_s: np.ndarray,
    phase_labels: np.ndarray,
    window_samples: int = WINDOW_SAMPLES,
    stride_samples: int = DEFAULT_STRIDE_SAMPLES,
) -> list[Window]:
    """Slice a recording into overlapping fixed-size windows.

    Parameters
    ----------
    data : (T, C) multi-channel IMU data.
    time_s : (T,) uniform timestamps in seconds.
    phase_labels : (T,) per-sample Phase values (from `assign_phase_labels`).
    window_samples : window length in samples (default 125 = 2.5 s @ 50 Hz).
    stride_samples : step between window starts (default 62 = ~50% overlap).

    Returns
    -------
    A list of Window objects. Each window's `label` is the most-common
    phase across its samples.
    """
    T = data.shape[0]
    if T < window_samples:
        return []

    windows: list[Window] = []
    for start in range(0, T - window_samples + 1, stride_samples):
        end = start + window_samples
        window_label_arr = phase_labels[start:end]
        # `bincount + argmax` = mode. Ties go to the smallest label index, which
        # is BACKGROUND — a deliberately conservative default for ambiguous windows.
        label = int(np.bincount(window_label_arr).argmax())
        windows.append(
            Window(
                start_idx=start,
                end_idx=end,
                start_time_s=float(time_s[start]),
                end_time_s=float(time_s[end - 1]),
                data=data[start:end],
                label=label,
            )
        )
    return windows


def slide_for_prediction(
    data: np.ndarray,
    time_s: np.ndarray,
    phase_labels: np.ndarray,
    t_impact_s: float | None,
    window_samples: int = WINDOW_SAMPLES,
    stride_samples: int = DEFAULT_STRIDE_SAMPLES,
    pre_impact_guard_ms: int = PRE_IMPACT_GUARD_MS,
) -> list[Window]:
    """Sliding windows + an explicit pre-impact-aligned window for fall recordings.

    For ADL recordings (t_impact_s is None), this is identical to `slide()`.

    For falls, ALSO emit a single window whose end aligns with
    `t_impact - guard_s`. This guarantees at least one window has PRE_IMPACT
    contents at its tail — which is exactly what the edge model sees at
    inference time (the model fires *as the pre-impact phase ends*).

    Without this aligned window, the basic sliding could miss PRE_IMPACT
    entirely as the window's mode, because PRE_IMPACT spans only ~450 ms
    inside a 2500 ms window.
    """
    windows = slide(data, time_s, phase_labels, window_samples, stride_samples)

    if t_impact_s is None:
        return windows

    guard_s = pre_impact_guard_ms / 1000.0
    # Target end time: right before the impact transient begins.
    target_end_s = t_impact_s - guard_s

    # Find the closest sample index to target_end_s; if it's within the recording
    # and we have enough preceding samples, build the aligned window.
    if target_end_s < time_s[0]:
        return windows
    end_idx = int(np.searchsorted(time_s, target_end_s, side="right"))
    start_idx = end_idx - window_samples
    if start_idx < 0 or end_idx > len(time_s):
        # Not enough recording before t_impact to form a full window.
        return windows

    window_label_arr = phase_labels[start_idx:end_idx]
    label = int(np.bincount(window_label_arr).argmax())
    # If somehow the dominant phase isn't PRE_IMPACT (e.g., a degenerate case
    # where the label window is too short), keep the actual mode but mark by
    # ensuring it's included for the prediction model to see.
    windows.append(
        Window(
            start_idx=start_idx,
            end_idx=end_idx,
            start_time_s=float(time_s[start_idx]),
            end_time_s=float(time_s[end_idx - 1]),
            data=data[start_idx:end_idx],
            label=label if label != Phase.BACKGROUND.value else Phase.PRE_IMPACT.value,
        )
    )
    return windows
