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
PRE_IMPACT-labeled windows, `slide_for_prediction` ALSO emits explicit
pre-impact-aligned windows whose tails land inside the run-up phase.

Lead-time and the staggered window family
------------------------------------------
An earlier version emitted a SINGLE aligned window ending at `t_impact - guard`
(guard = 50 ms). That pinned every pre-impact positive to one fixed offset, so
the model could only ever learn to fire ~50 ms before impact, and the measured
lead time collapsed to a degenerate spike at ~60 ms — structurally unable to
reach the ≥300 ms lead-time target (see BUILD_LOG "60 ms geometry lock").

The fix: emit a STAGGERED FAMILY of aligned windows whose end-times step back
across the pre-impact phase (default tails at t-50/-150/-250/-350/-450 ms). Each
is force-labeled PRE_IMPACT — the intent being "the window tail shows the run-up,
so predict an imminent impact". This (a) turns lead time into a real distribution
instead of a constant, and (b) teaches the model to recognise the EARLY run-up so
it can fire with usable lead. The trade-off: the earliest windows carry only a
sliver of pre-impact signal at the tail, a deliberately harder — and more honest —
positive than the late-firing one.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fall_guardian_ml.datasets.pre_impact_labels import Phase

# Locked window math.
WINDOW_SECONDS = 2.5
TARGET_HZ = 50
WINDOW_SAMPLES = int(WINDOW_SECONDS * TARGET_HZ)          # 125
DEFAULT_STRIDE_SAMPLES = WINDOW_SAMPLES // 2              # 62 (~50% overlap)

# Staggered pre-impact-aligned window tails (ms before the impact peak). They
# span the pre-impact phase [guard=50 ms, lead=500 ms]; each yields one aligned
# window whose lead time equals the offset. Tunable from training config.
DEFAULT_PRE_IMPACT_OFFSETS_MS = (50, 150, 250, 350, 450)


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


def _aligned_window(
    data: np.ndarray,
    time_s: np.ndarray,
    target_end_s: float,
    window_samples: int,
) -> Window | None:
    """Build one window whose tail ends at (the sample nearest) `target_end_s`.

    Returns None if the target is before the recording starts or there aren't
    `window_samples` samples preceding it. The window is force-labeled
    PRE_IMPACT — by construction its tail sits in the run-up phase, which is the
    signal the edge model must learn to fire on.
    """
    if target_end_s < time_s[0]:
        return None
    end_idx = int(np.searchsorted(time_s, target_end_s, side="right"))
    start_idx = end_idx - window_samples
    if start_idx < 0 or end_idx > len(time_s):
        return None
    return Window(
        start_idx=start_idx,
        end_idx=end_idx,
        start_time_s=float(time_s[start_idx]),
        end_time_s=float(time_s[end_idx - 1]),
        data=data[start_idx:end_idx],
        label=Phase.PRE_IMPACT.value,
    )


def slide_for_prediction(
    data: np.ndarray,
    time_s: np.ndarray,
    phase_labels: np.ndarray,
    t_impact_s: float | None,
    window_samples: int = WINDOW_SAMPLES,
    stride_samples: int = DEFAULT_STRIDE_SAMPLES,
    aligned_offsets_ms: tuple[int, ...] = DEFAULT_PRE_IMPACT_OFFSETS_MS,
) -> list[Window]:
    """Sliding windows + a STAGGERED FAMILY of pre-impact-aligned windows.

    For ADL recordings (``t_impact_s is None``) this is identical to ``slide()``.

    For falls, in addition to the overlapping windows, emit one aligned window
    per entry in ``aligned_offsets_ms`` — each ending at ``t_impact - offset``
    and force-labeled PRE_IMPACT. The family of offsets makes the model's lead
    time a real distribution (it sees run-up windows from 50 ms up to 450 ms
    before impact) instead of the single fixed 50 ms offset of the old design,
    which had locked the measured lead to a ~60 ms spike.

    Offsets that fall before the recording start, or that lack a full window of
    preceding samples, are silently skipped (short recordings simply contribute
    fewer aligned windows).
    """
    windows = slide(data, time_s, phase_labels, window_samples, stride_samples)

    if t_impact_s is None:
        return windows

    seen_ends: set[int] = set()
    # Step from the latest (smallest offset) back to the earliest so the most
    # signal-rich windows are added first; de-dupe on end index in case two
    # offsets snap to the same sample on a short/low-rate recording.
    for offset_ms in sorted(aligned_offsets_ms):
        target_end_s = t_impact_s - offset_ms / 1000.0
        w = _aligned_window(data, time_s, target_end_s, window_samples)
        if w is None or w.end_idx in seen_ends:
            continue
        seen_ends.add(w.end_idx)
        windows.append(w)

    return windows
