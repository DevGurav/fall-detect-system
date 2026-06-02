"""Assemble the edge model's training set from WEDA-FALL.

The edge (pre-impact prediction) model is a *binary* classifier on raw 6-channel
2.5 s windows:

    positive (1) = PRE_IMPACT window   — a fall impact is ~50–500 ms away
    negative (0) = everything else     — ADL background, impact, post-impact

This module turns the dataset modules from Week A into ready-to-train tensors:

    discover_recordings → load_recording → find_impact (falls only)
    → assign_phase_labels → slide_for_prediction → stack windows

Each window carries three pieces of bookkeeping we need for honest evaluation:
  • `subject` — the user_id, so the train/test split never crosses subjects.
  • `is_adl`  — whether the source recording is an ADL (vs a fall). The headline
                "FPR on ADL ≤ 5%" target is measured over ADL windows only.
  • `t_to_impact_s` — for positive windows, how far the window end sits before
                the impact peak. This drives the lead-time histogram.

A synthetic generator (`make_synthetic_bundle`) produces a bundle with the same
shape and contract so the whole pipeline — model, training loop, MLflow logging,
quantization, benchmark — can be smoke-tested before the real dataset is on disk.
It is NOT a stand-in for real results; metrics from it are clearly labelled as
synthetic wherever they surface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from fall_guardian_ml.datasets.pre_impact_labels import (
    Phase,
    assign_phase_labels,
    find_impact,
)
from fall_guardian_ml.datasets.weda_fall import (
    RecordingId,
    discover_recordings,
    load_fall_timestamps,
    load_recording,
)
from fall_guardian_ml.features.windowing import (
    WINDOW_SAMPLES,
    slide_for_prediction,
)

N_CHANNELS = 6  # ax, ay, az, wx, wy, wz


@dataclass
class EdgeBundle:
    """A windowed, model-ready dataset for the edge prediction model."""

    X: np.ndarray            # (N, window_samples, 6) float32 — raw IMU windows
    y: np.ndarray            # (N,) int64 — 1 = PRE_IMPACT, 0 = everything else
    groups: np.ndarray       # (N,) int64 — subject (user_id) for each window
    is_adl: np.ndarray       # (N,) bool — window came from an ADL recording
    t_to_impact_s: np.ndarray  # (N,) float32 — lead time for positives, NaN otherwise
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return self.X.shape[0]

    @property
    def n_positive(self) -> int:
        return int(self.y.sum())

    @property
    def pos_weight(self) -> float:
        """neg/pos ratio — feeds BCEWithLogitsLoss `pos_weight` to fight imbalance."""
        pos = max(self.n_positive, 1)
        neg = len(self) - self.n_positive
        return float(neg) / float(pos)

    def summary(self) -> str:
        n = len(self)
        pos = self.n_positive
        adl = int(self.is_adl.sum())
        subjects = sorted({int(s) for s in self.groups})
        return (
            f"{n} windows | {pos} pre-impact ({100 * pos / max(n, 1):.1f}%) | "
            f"{adl} ADL-sourced | {len(subjects)} subjects {subjects}"
        )


def build_edge_bundle(
    dataset_root: Path,
    sample_rate: int = 50,
    include_young: bool = True,
    include_elder: bool = True,
    movements: list[str] | None = None,
    include_orientation: bool = True,
) -> EdgeBundle:
    """Walk WEDA-FALL and assemble the binary pre-impact windowed dataset.

    Channels (in order): accel (x,y,z), gyro (x,y,z), and — when
    `include_orientation` — the orientation quaternion (s,i,j,k), for 10 total.
    Orientation gives the model absolute posture/tumble context that accel+gyro
    (which only see acceleration and angular *velocity*) can't, and we already
    load+resample it, so it's free signal. A recording missing the orientation
    stream is zero-padded on those 4 channels to keep the channel count uniform.

    Skips any fall recording whose impact peak fails the sanity threshold
    (peak |a| < 20 m/s²) — those are likely mislabeled and would inject noisy
    positives. Their count is reported in `meta` for QA.
    """
    dataset_root = Path(dataset_root)
    fall_ts = load_fall_timestamps(dataset_root)
    rec_ids = discover_recordings(
        dataset_root,
        sample_rate=sample_rate,
        movements=movements,
        include_young=include_young,
        include_elder=include_elder,
    )

    X_list: list[np.ndarray] = []
    y_list: list[int] = []
    g_list: list[int] = []
    adl_list: list[bool] = []
    lead_list: list[float] = []

    n_falls_used = 0
    n_falls_skipped = 0

    for rec_id in rec_ids:
        rec = load_recording(
            dataset_root, rec_id, sample_rate=sample_rate, fall_timestamps=fall_ts
        )
        # Raw window: accel (x,y,z) ++ gyro (x,y,z) [++ orientation quat (s,i,j,k)].
        parts = [rec.accel, rec.gyro]
        if include_orientation:
            T = rec.accel.shape[0]
            ori = rec.orientation
            if ori is None:
                ori = np.zeros((T, 4), dtype=np.float32)
            elif ori.shape[0] != T:
                # The loader can leave orientation a few samples short of
                # accel/gyro. Align to T: edge-hold pad (quaternion drifts slowly)
                # or truncate, so all channels share one time axis.
                if ori.shape[0] < T:
                    pad = np.repeat(ori[-1:], T - ori.shape[0], axis=0)
                    ori = np.concatenate([ori, pad], axis=0)
                else:
                    ori = ori[:T]
            parts.append(ori)
        data = np.concatenate(parts, axis=1).astype(np.float32)

        t_impact: float | None = None
        if rec_id.is_fall and rec.fall_window is not None:
            ann = find_impact(rec.time, rec.accel, rec.fall_window)
            if not ann.valid:
                n_falls_skipped += 1
                continue
            t_impact = ann.t_impact_s
            n_falls_used += 1

        phase_labels = assign_phase_labels(rec.time, t_impact, rec.fall_window)
        windows = slide_for_prediction(data, rec.time, phase_labels, t_impact)

        for w in windows:
            is_pre = w.label == Phase.PRE_IMPACT.value
            X_list.append(w.data)
            y_list.append(1 if is_pre else 0)
            g_list.append(rec_id.user_id)
            adl_list.append(not rec_id.is_fall)
            # Lead time = how far the window END sits before the impact peak.
            lead_list.append(
                float(t_impact - w.end_time_s) if (is_pre and t_impact is not None)
                else float("nan")
            )

    if not X_list:
        raise RuntimeError(
            f"No windows assembled from {dataset_root}. Is WEDA-FALL extracted "
            f"under data/raw/WEDA-FALL-main/ ? See DATA.md."
        )

    bundle = EdgeBundle(
        X=np.stack(X_list).astype(np.float32),
        y=np.asarray(y_list, dtype=np.int64),
        groups=np.asarray(g_list, dtype=np.int64),
        is_adl=np.asarray(adl_list, dtype=bool),
        t_to_impact_s=np.asarray(lead_list, dtype=np.float32),
        meta={
            "source": "WEDA-FALL",
            "sample_rate": sample_rate,
            "n_channels": int(X_list[0].shape[1]),
            "include_orientation": include_orientation,
            "n_falls_used": n_falls_used,
            "n_falls_skipped_below_threshold": n_falls_skipped,
            "n_recordings": len(rec_ids),
        },
    )
    return bundle


# ─── Channel standardization ─────────────────────────────────────────────────


@dataclass
class ChannelStats:
    """Per-channel mean/std for standardizing raw IMU windows before the model.

    Unlike the per-USER z-score on engineered features (features/normalization.py),
    the edge model standardizes its 6 raw channels with stats fit on the TRAINING
    windows. These constants ship with the model and become the fixed input scale
    the quantizer calibrates against — so train and on-device inference agree.
    """

    mean: np.ndarray   # (6,)
    std: np.ndarray    # (6,)

    def apply(self, X: np.ndarray) -> np.ndarray:
        safe = np.where(self.std > 0, self.std, 1.0)
        return ((X - self.mean) / safe).astype(np.float32)


def fit_channel_stats(X: np.ndarray) -> ChannelStats:
    """Fit per-channel mean/std over a (N, T, 6) window stack (axes N and T)."""
    return ChannelStats(
        mean=X.mean(axis=(0, 1)).astype(np.float32),
        std=X.std(axis=(0, 1)).astype(np.float32),
    )


# ─── Synthetic smoke-test data ───────────────────────────────────────────────


def make_synthetic_bundle(
    n_subjects: int = 8,
    falls_per_subject: int = 25,
    adls_per_subject: int = 60,
    seed: int = 0,
) -> EdgeBundle:
    """Generate a shape-correct, signal-bearing synthetic bundle.

    Falls get a rising-energy ramp toward the window tail (mimicking the run-up
    to impact); ADLs get band-limited quasi-periodic motion (mimicking walking /
    repetitive activity). The two are linearly separable enough that the pipeline
    can demonstrably learn *something* — proving the plumbing end-to-end — without
    pretending to be WEDA-FALL. Every positive carries a plausible lead time.
    """
    rng = np.random.default_rng(seed)
    T, C = WINDOW_SAMPLES, N_CHANNELS
    t = np.linspace(0.0, 2.5, T, dtype=np.float32)

    X_list, y_list, g_list, adl_list, lead_list = [], [], [], [], []

    for subject in range(1, n_subjects + 1):
        for _ in range(adls_per_subject):
            freq = rng.uniform(1.2, 2.2)          # walking-band ~1.5–2 Hz
            phase = rng.uniform(0, 2 * np.pi)
            base = np.zeros((T, C), dtype=np.float32)
            base[:, 2] = 9.81                      # gravity on az
            for c in range(C):
                amp = rng.uniform(0.5, 2.0)
                base[:, c] += amp * np.sin(2 * np.pi * freq * t + phase + c)
            base += rng.normal(0, 0.3, size=(T, C)).astype(np.float32)
            X_list.append(base)
            y_list.append(0)
            g_list.append(subject)
            adl_list.append(True)
            lead_list.append(float("nan"))

        for _ in range(falls_per_subject):
            base = np.zeros((T, C), dtype=np.float32)
            base[:, 2] = 9.81
            base += rng.normal(0, 0.4, size=(T, C)).astype(np.float32)
            # Rising energy in the last ~500 ms — the pre-impact run-up signature.
            ramp = np.clip((t - 2.0) / 0.5, 0.0, 1.0).astype(np.float32)
            for c in range(C):
                base[:, c] += ramp * rng.uniform(3.0, 8.0) * np.sin(
                    2 * np.pi * rng.uniform(3, 6) * t
                )
            X_list.append(base)
            y_list.append(1)
            g_list.append(subject)
            adl_list.append(False)
            lead_list.append(float(rng.uniform(0.30, 0.50)))  # 300–500 ms lead

    X = np.stack(X_list).astype(np.float32)
    perm = rng.permutation(len(X))
    return EdgeBundle(
        X=X[perm],
        y=np.asarray(y_list, dtype=np.int64)[perm],
        groups=np.asarray(g_list, dtype=np.int64)[perm],
        is_adl=np.asarray(adl_list, dtype=bool)[perm],
        t_to_impact_s=np.asarray(lead_list, dtype=np.float32)[perm],
        meta={"source": "SYNTHETIC", "seed": seed, "n_subjects": n_subjects},
    )
