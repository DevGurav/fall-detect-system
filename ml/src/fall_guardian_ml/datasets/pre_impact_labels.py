"""Pre-impact label re-derivation for WEDA-FALL.

WEDA-FALL ships `fall_timestamps.csv` with per-fall (start_time, end_time)
covering the full 4-phase fall sequence (pre-fall → impact → body-adjustment →
post-fall). It does NOT label the impact INSTANT itself. For pre-impact
PREDICTION (the edge model's job) we need that instant to a few-ms precision.

Algorithm — peak-magnitude impact detection:

  1. Within the labeled fall window [start_time, end_time], compute the
     acceleration magnitude:
         |a|(t) = sqrt(ax(t)^2 + ay(t)^2 + az(t)^2)
  2. Find the time of the maximum magnitude:
         t_impact = argmax_t |a|(t)
     This is the body-to-ground (or chair) collision peak — the largest
     transient in the signal.
  3. Sanity check: peak |a| must exceed FALL_MAG_THRESHOLD_MS2 (~20 m/s² ≈ 2g),
     well above 1g = 9.81 m/s² baseline. Below that, the recording is likely
     mislabeled or a near-fall.
  4. Define temporal phase labels around t_impact:
       PRE_IMPACT  = [t_impact - 500 ms,  t_impact -  50 ms]   ← prediction target
       IMPACT      = [t_impact -  50 ms,  t_impact + 500 ms]
       POST_IMPACT = [t_impact + 500 ms,  end_time]
       BACKGROUND  = everything else (outside fall window for falls;
                                      all samples for ADL recordings)
  5. Validate t_impact against the dataset's manual labels: report the lag
     `t_impact - start_time` across all fall recordings (expected 0.5–1.5 s
     since start_time marks the pre-fall onset). The distribution is logged
     in the EDA notebook for QA.

Reference: this is the standard approach in pre-impact wrist-fall literature
(e.g., Yu et al. 2021 used a similar peak-magnitude rule on waist data).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np

# ─── Timing constants ────────────────────────────────────────────────────────

# Pre-impact lead time: when the edge model is allowed to alert.
PRE_IMPACT_LEAD_MS = 500       # 500 ms before impact peak = earliest plausible signal
PRE_IMPACT_GUARD_MS = 50       # 50 ms guard band — the last bit before impact is
                               # effectively part of the impact itself

# Post-impact tail: body-adjustment phase.
POST_IMPACT_TAIL_MS = 500

# Sanity threshold: a real fall impact peak should exceed this magnitude.
# 9.81 = 1g (standing baseline). 20 m/s² ≈ 2g — conservative; published lit
# uses 15–25 m/s² depending on subject + fall type.
FALL_MAG_THRESHOLD_MS2 = 20.0


# ─── Phase enum (one per sample) ─────────────────────────────────────────────

class Phase(IntEnum):
    """Phase label assigned to every sample of a recording."""

    BACKGROUND = 0   # ADL or outside the labeled fall window
    PRE_IMPACT = 1   # the edge model's prediction target
    IMPACT = 2       # ~50 ms before to ~500 ms after the peak
    POST_IMPACT = 3  # body adjustment / lying

    @property
    def is_positive_for_prediction(self) -> bool:
        """The edge (prediction) model treats PRE_IMPACT as positive, all else as negative."""
        return self is Phase.PRE_IMPACT

    @property
    def is_positive_for_detection(self) -> bool:
        """The cloud (detection) model treats IMPACT + POST_IMPACT as positive."""
        return self in (Phase.IMPACT, Phase.POST_IMPACT)


# ─── Impact detection ────────────────────────────────────────────────────────

@dataclass
class ImpactAnnotation:
    """Result of running `find_impact` on one fall recording."""

    t_impact_s: float          # estimated impact moment (seconds from recording start)
    peak_magnitude_ms2: float  # |a| at t_impact
    label_window: tuple[float, float]  # the (start, end) from fall_timestamps.csv
    lag_from_label_start_s: float      # t_impact - start_time (typical: 0.5–1.5 s)
    valid: bool                # True iff all sanity checks passed
    reason: str = ""           # if not valid, why


def find_impact(
    time_s: np.ndarray,
    accel_xyz: np.ndarray,
    label_window: tuple[float, float],
    threshold_ms2: float = FALL_MAG_THRESHOLD_MS2,
) -> ImpactAnnotation:
    """Detect the impact instant in a single fall recording.

    Parameters
    ----------
    time_s : (T,) array of uniformly-spaced timestamps (seconds).
    accel_xyz : (T, 3) accelerometer readings in m/s² (x, y, z).
    label_window : (start, end) — the WEDA-FALL fall_timestamps.csv span.
    threshold_ms2 : peak |a| must exceed this for the impact to be plausible.

    Returns
    -------
    ImpactAnnotation with t_impact + diagnostics.
    """
    if accel_xyz.shape[0] != time_s.shape[0]:
        raise ValueError(
            f"accel_xyz {accel_xyz.shape} and time_s {time_s.shape} must align on axis 0"
        )

    start_s, end_s = label_window
    in_window = (time_s >= start_s) & (time_s <= end_s)
    if not np.any(in_window):
        return ImpactAnnotation(
            t_impact_s=float("nan"),
            peak_magnitude_ms2=float("nan"),
            label_window=label_window,
            lag_from_label_start_s=float("nan"),
            valid=False,
            reason=f"No samples in label window [{start_s}, {end_s}]",
        )

    # Acceleration magnitude per sample.
    mag = np.linalg.norm(accel_xyz, axis=1)        # shape (T,)
    # Mask out samples outside the labeled window so they can't win the argmax.
    mag_in = np.where(in_window, mag, -np.inf)

    impact_idx = int(np.argmax(mag_in))
    t_impact = float(time_s[impact_idx])
    peak = float(mag[impact_idx])

    valid = peak >= threshold_ms2
    reason = "" if valid else f"peak |a|={peak:.2f} m/s² below threshold {threshold_ms2}"

    return ImpactAnnotation(
        t_impact_s=t_impact,
        peak_magnitude_ms2=peak,
        label_window=label_window,
        lag_from_label_start_s=t_impact - start_s,
        valid=valid,
        reason=reason,
    )


# ─── Phase-label assignment ──────────────────────────────────────────────────

def assign_phase_labels(
    time_s: np.ndarray,
    t_impact_s: float | None,
    fall_window: tuple[float, float] | None,
    pre_impact_lead_ms: int = PRE_IMPACT_LEAD_MS,
    pre_impact_guard_ms: int = PRE_IMPACT_GUARD_MS,
    post_impact_tail_ms: int = POST_IMPACT_TAIL_MS,
) -> np.ndarray:
    """Return a (T,) int8 array of Phase values, one per sample.

    For an ADL recording → pass `t_impact_s=None`, `fall_window=None` → all BACKGROUND.
    For a fall → pass both; samples inside the labeled window get phase-segmented.

    Phase boundaries around the detected impact peak:

        ─BACKGROUND─PRE_IMPACT─IMPACT─POST_IMPACT─BACKGROUND─
                   │           │      │           │
            t_impact-lead     -guard +tail      end_time
    """
    labels = np.full(len(time_s), Phase.BACKGROUND.value, dtype=np.int8)

    if t_impact_s is None or fall_window is None:
        return labels

    fall_start, fall_end = fall_window
    lead_s = pre_impact_lead_ms / 1000.0
    guard_s = pre_impact_guard_ms / 1000.0
    tail_s = post_impact_tail_ms / 1000.0

    # Clamp pre_start to fall_window start (a labeled window may be short).
    pre_start = max(t_impact_s - lead_s, fall_start)
    pre_end = t_impact_s - guard_s
    impact_start = pre_end
    impact_end = t_impact_s + tail_s
    post_start = impact_end
    post_end = fall_end

    labels[(time_s >= pre_start) & (time_s < pre_end)] = Phase.PRE_IMPACT.value
    labels[(time_s >= impact_start) & (time_s < impact_end)] = Phase.IMPACT.value
    labels[(time_s >= post_start) & (time_s <= post_end)] = Phase.POST_IMPACT.value

    return labels
