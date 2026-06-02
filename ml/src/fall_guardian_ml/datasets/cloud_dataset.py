"""Assemble the cloud detector's training set from WEDA-FALL.

The cloud (post-impact detection) model is the PRECISION gate behind the
recall-first edge model. It is a *binary* classifier on the 2.5 s window:

    positive (1) = IMPACT or POST_IMPACT window  — a fall actually happened
    negative (0) = everything else               — ADL / background / pre-impact

Positive class = ``Phase.is_positive_for_detection`` (IMPACT + POST_IMPACT), the
mirror of the edge model's PRE_IMPACT positive. Unlike the edge bundle, this uses
plain ``slide()`` (no staggered pre-impact-aligned family — that's a *prediction*
trick); a 2.5 s window straddling the impact naturally takes mode IMPACT/POST.

Each window carries BOTH model inputs:
  • ``X_raw``    — the raw (125, 6) window, fed to the Transformer encoder.
  • ``feats``    — the 43-d engineered vector (``features.extraction``), fused at
                   the pooled head. The backend has the raw window and computes
                   these the same way at serving time, so train == serve.

Channels are the 6 the API actually carries (ax, ay, az, wx, wy, wz — see
``backend/app/schemas.py::IMUSample``). Orientation is deliberately NOT used: the
device never sends it, so a model that needs it could not be served.

Per-window bookkeeping for honest evaluation + the severity head:
  • ``groups``   — subject id, so the split never crosses subjects.
  • ``is_adl``   — window came from an ADL recording (the "FPR on ADL" denominator).
  • ``severity`` — the window's peak |a| (m/s²); the severity head's regression target.

``make_synthetic_cloud_bundle`` mirrors the edge synthetic generator: same shape +
contract so the whole pipeline (model, train loop, MLflow, API mapping) is
smoke-testable before touching the real dataset. Synthetic metrics are clearly
labelled as such wherever they surface.
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
    discover_recordings,
    load_fall_timestamps,
    load_recording,
)
from fall_guardian_ml.features.extraction import extract_features, feature_names
from fall_guardian_ml.features.windowing import WINDOW_SAMPLES, slide

N_CHANNELS = 6  # ax, ay, az, wx, wy, wz — exactly what the API/IMUSample carries
N_FEATURES = 43  # len(feature_names()); asserted at build time


def _peak_accel_magnitude(window: np.ndarray) -> float:
    """Peak |a| over a (T, 6) window's accel channels — the severity target."""
    accel = window[:, :3]
    return float(np.sqrt((accel * accel).sum(axis=1)).max())


@dataclass
class CloudBundle:
    """A windowed, model-ready dataset for the cloud detection model."""

    X_raw: np.ndarray         # (N, window_samples, 6) float32 — raw IMU windows
    feats: np.ndarray         # (N, 43) float32 — engineered feature vectors
    y: np.ndarray             # (N,) int64 — 1 = IMPACT/POST_IMPACT, 0 = everything else
    groups: np.ndarray        # (N,) int64 — subject (user_id) per window
    is_adl: np.ndarray        # (N,) bool — window came from an ADL recording
    severity: np.ndarray      # (N,) float32 — peak |a| (m/s²), severity-head target
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return self.X_raw.shape[0]

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
            f"{n} windows | {pos} fall ({100 * pos / max(n, 1):.1f}%) | "
            f"{adl} ADL-sourced | {len(subjects)} subjects {subjects}"
        )


def build_cloud_bundle(
    dataset_root: Path,
    sample_rate: int = 50,
    include_young: bool = True,
    include_elder: bool = True,
    movements: list[str] | None = None,
) -> CloudBundle:
    """Walk WEDA-FALL and assemble the binary post-impact detection dataset.

    For each recording: resolve the impact instant (falls only), phase-label the
    samples, slide plain 2.5 s windows, and for each window record the raw 6-ch
    data, its 43-d feature vector, the detection label (IMPACT/POST_IMPACT → 1),
    the subject, the ADL flag, and the peak |a| (severity target).

    Fall recordings whose impact peak fails the sanity threshold (peak |a| <
    20 m/s²) are skipped — likely mislabeled — and counted in ``meta`` for QA.
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
    f_list: list[np.ndarray] = []
    y_list: list[int] = []
    g_list: list[int] = []
    adl_list: list[bool] = []
    sev_list: list[float] = []

    n_falls_used = 0
    n_falls_skipped = 0

    for rec_id in rec_ids:
        rec = load_recording(
            dataset_root, rec_id, sample_rate=sample_rate,
            include_orientation=False, fall_timestamps=fall_ts,
        )
        # 6-channel raw window: accel (x,y,z) ++ gyro (x,y,z). No orientation —
        # the device never sends it, so the served model must not depend on it.
        data = np.concatenate([rec.accel, rec.gyro], axis=1).astype(np.float32)

        t_impact: float | None = None
        if rec_id.is_fall and rec.fall_window is not None:
            ann = find_impact(rec.time, rec.accel, rec.fall_window)
            if not ann.valid:
                n_falls_skipped += 1
                continue
            t_impact = ann.t_impact_s
            n_falls_used += 1

        phase_labels = assign_phase_labels(rec.time, t_impact, rec.fall_window)
        windows = slide(data, rec.time, phase_labels, WINDOW_SAMPLES)

        for w in windows:
            is_fall_window = Phase(w.label).is_positive_for_detection
            X_list.append(w.data)
            f_list.append(extract_features(w.data, sample_rate=sample_rate))
            y_list.append(1 if is_fall_window else 0)
            g_list.append(rec_id.user_id)
            adl_list.append(not rec_id.is_fall)
            sev_list.append(_peak_accel_magnitude(w.data))

    if not X_list:
        raise RuntimeError(
            f"No windows assembled from {dataset_root}. Is WEDA-FALL extracted "
            f"under data/raw/WEDA-FALL-main/ ? See DATA.md."
        )

    feats = np.stack(f_list).astype(np.float32)
    assert feats.shape[1] == N_FEATURES, (
        f"feature vector is {feats.shape[1]}-d but N_FEATURES={N_FEATURES}; "
        f"extraction.feature_names() and cloud_dataset disagree."
    )

    return CloudBundle(
        X_raw=np.stack(X_list).astype(np.float32),
        feats=feats,
        y=np.asarray(y_list, dtype=np.int64),
        groups=np.asarray(g_list, dtype=np.int64),
        is_adl=np.asarray(adl_list, dtype=bool),
        severity=np.asarray(sev_list, dtype=np.float32),
        meta={
            "source": "WEDA-FALL",
            "sample_rate": sample_rate,
            "n_channels": N_CHANNELS,
            "n_features": N_FEATURES,
            "feature_names": feature_names(),
            "n_falls_used": n_falls_used,
            "n_falls_skipped_below_threshold": n_falls_skipped,
            "n_recordings": len(rec_ids),
            "positive_class": "IMPACT+POST_IMPACT",
        },
    )


# ─── Synthetic smoke-test data ───────────────────────────────────────────────


def make_synthetic_cloud_bundle(
    n_subjects: int = 8,
    falls_per_subject: int = 20,
    adls_per_subject: int = 50,
    seed: int = 0,
) -> CloudBundle:
    """Generate a shape-correct, signal-bearing synthetic detection bundle.

    Falls carry a sharp impact transient mid-window (a 20–40 m/s² spike + ringing),
    so their peak |a| and engineered features separate from ADL's band-limited
    quasi-periodic motion (~1.5–2 Hz, ~1 g). Linearly separable enough to prove
    the pipeline learns *something* end-to-end — NOT a stand-in for WEDA-FALL.
    Every subject gets both classes so the subject-stratified split can measure
    recall, and enough ADL windows to fit a per-user feature normaliser.
    """
    rng = np.random.default_rng(seed)
    T, C = WINDOW_SAMPLES, N_CHANNELS
    t = np.linspace(0.0, 2.5, T, dtype=np.float32)

    X_list, f_list, y_list, g_list, adl_list, sev_list = [], [], [], [], [], []

    def _emit(window: np.ndarray, label: int, subject: int, is_adl: bool) -> None:
        X_list.append(window)
        f_list.append(extract_features(window, sample_rate=50))
        y_list.append(label)
        g_list.append(subject)
        adl_list.append(is_adl)
        sev_list.append(_peak_accel_magnitude(window))

    for subject in range(1, n_subjects + 1):
        for _ in range(adls_per_subject):
            freq = rng.uniform(1.2, 2.2)               # walking-band ~1.5–2 Hz
            phase = rng.uniform(0, 2 * np.pi)
            w = np.zeros((T, C), dtype=np.float32)
            w[:, 2] = 9.81                              # gravity on az
            for c in range(C):
                amp = rng.uniform(0.5, 2.0)
                w[:, c] += amp * np.sin(2 * np.pi * freq * t + phase + c)
            w += rng.normal(0, 0.3, size=(T, C)).astype(np.float32)
            _emit(w, label=0, subject=subject, is_adl=True)

        for _ in range(falls_per_subject):
            w = np.zeros((T, C), dtype=np.float32)
            w[:, 2] = 9.81
            w += rng.normal(0, 0.4, size=(T, C)).astype(np.float32)
            # Sharp impact spike + decaying ring in the middle of the window —
            # the post-impact signature the detector confirms.
            impact_idx = int(rng.integers(T // 3, 2 * T // 3))
            peak = rng.uniform(20.0, 40.0)
            for c in range(3):  # accel channels carry the impact
                w[impact_idx, c] += peak * rng.choice([-1.0, 1.0])
                decay = np.exp(-np.arange(T - impact_idx) / 5.0).astype(np.float32)
                w[impact_idx:, c] += 0.4 * peak * decay * rng.uniform(0.5, 1.0)
            _emit(w, label=1, subject=subject, is_adl=False)

    X = np.stack(X_list).astype(np.float32)
    feats = np.stack(f_list).astype(np.float32)
    perm = rng.permutation(len(X))
    return CloudBundle(
        X_raw=X[perm],
        feats=feats[perm],
        y=np.asarray(y_list, dtype=np.int64)[perm],
        groups=np.asarray(g_list, dtype=np.int64)[perm],
        is_adl=np.asarray(adl_list, dtype=bool)[perm],
        severity=np.asarray(sev_list, dtype=np.float32)[perm],
        meta={"source": "SYNTHETIC", "seed": seed, "n_subjects": n_subjects,
              "n_channels": N_CHANNELS, "n_features": N_FEATURES,
              "positive_class": "IMPACT+POST_IMPACT"},
    )
