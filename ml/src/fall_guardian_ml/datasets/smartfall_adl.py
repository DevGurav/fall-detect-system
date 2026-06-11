"""SmartFallMM watch ADL streams — hard negatives + continuous-wear material.

SmartFallMM (Texas State) is a multimodal fall/ADL dataset. We use ONLY the
smartwatch accelerometer + gyroscope ADL trials (activities A01–A09: drinking,
pick-up, jacket on/off, stepping up, sweeping, washing/waving hands, TUG/walk,
sit/stand) from both the young and old groups. Two Phase-30 jobs:

  1. HARD ADL NEGATIVES for cloud training (`build_smartfall_adl_bundle`) — the
     vigorous wrist movements (sweeping, waving, jacket) are exactly the
     impact-like ADL family behind the 5% held-out FPR (BUILD_LOG Phase 20).
  2. CONTINUOUS-WEAR REPLAY material (`discover_adl_trials` +
     `load_trial_stream`) for `scripts/continuous_wear_sim.py`.

SmartFall FALL trials (A10–A14) are deliberately EXCLUDED: their impact instants
are not annotated in our pre-impact pipeline, so they cannot be labeled
positives — and mislabeling them negative would poison training.

Format notes (SmartFallMM README):
  • Watch CSVs are headerless `timestamp, x, y, z` at ~32 Hz, accel in m/s²,
    gyro in rad/s — same physical units as WEDA-FALL after loading.
  • Accel and gyro come from separate sensor streams with their own clocks, so
    each trial is linearly resampled onto a common uniform 50 Hz grid over the
    overlapping interval (mirrors `weda_fall._resample_to_uniform_hz`). 32→50 Hz
    is an upsample; linear interpolation adds no spectral content above the
    sensor's true band, which is the honest choice for a domain-transfer source.
  • Subject ids collide with WEDA-FALL's (both count from 1), so loaded subjects
    are offset: young +1000, old +2000. `groups` therefore never collide when a
    SmartFall bundle is merged with the WEDA bundle for subject-stratified CV.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from fall_guardian_ml.datasets.cloud_dataset import CloudBundle, _peak_accel_magnitude
from fall_guardian_ml.datasets.pre_impact_labels import Phase
from fall_guardian_ml.features.extraction import extract_features, feature_names
from fall_guardian_ml.features.windowing import (
    DEFAULT_STRIDE_SAMPLES,
    WINDOW_SAMPLES,
    slide,
)

# Activity ids from the SmartFallMM README table.
ADL_ACTIVITY_IDS = set(range(1, 10))     # A01–A09: daily activities
FALL_ACTIVITY_IDS = set(range(10, 15))   # A10–A14: falls (excluded — see module doc)

# Offsets keep SmartFall subject ids disjoint from WEDA-FALL's (U01–U31) and
# from each other (young S28–S63 overlaps old S01–S26 numerically).
SUBJECT_OFFSET = {"young": 1000, "old": 2000}

TARGET_HZ = 50

# Trial CSVs are named S<subject>A<activity>T<trial>.csv (e.g. S28A05T03.csv).
_FNAME_RE = re.compile(r"^S(?P<subject>\d+)A(?P<activity>\d+)T(?P<trial>\d+)\.csv$")


@dataclass(frozen=True)
class SmartFallTrial:
    """One ADL trial with both watch sensor files present."""

    group: str            # "young" | "old"
    subject: int          # raw SmartFall subject id (e.g. 28)
    activity: int         # 1–9 (ADL only)
    trial: int
    accel_path: Path
    gyro_path: Path

    @property
    def subject_uid(self) -> int:
        """Globally-unique subject id (offset per group; never collides with WEDA)."""
        return SUBJECT_OFFSET[self.group] + self.subject

    @property
    def movement(self) -> str:
        """Movement code for FP breakdowns, namespaced to avoid clashing with D01-D11."""
        return f"SF-A{self.activity:02d}"


def discover_adl_trials(
    dataset_root: Path,
    groups: Iterable[str] = ("young", "old"),
) -> list[SmartFallTrial]:
    """Walk the watch accel/gyro folders and pair up complete ADL trials.

    A trial is usable only if BOTH the accelerometer and gyroscope file exist
    (the cloud model needs all 6 channels); unpaired files are skipped — the
    README documents several subjects with one-sided watch data.
    """
    dataset_root = Path(dataset_root)
    out: list[SmartFallTrial] = []
    for group in groups:
        accel_dir = dataset_root / group / "accelerometer" / "watch"
        gyro_dir = dataset_root / group / "gyroscope" / "watch"
        if not (accel_dir.exists() and gyro_dir.exists()):
            continue
        for accel_path in sorted(accel_dir.glob("S*.csv")):
            m = _FNAME_RE.match(accel_path.name)
            if not m:
                continue
            activity = int(m["activity"])
            if activity not in ADL_ACTIVITY_IDS:
                continue
            gyro_path = gyro_dir / accel_path.name
            if not gyro_path.exists():
                continue
            out.append(
                SmartFallTrial(
                    group=group,
                    subject=int(m["subject"]),
                    activity=activity,
                    trial=int(m["trial"]),
                    accel_path=accel_path,
                    gyro_path=gyro_path,
                )
            )
    return sorted(out, key=lambda t: (t.group, t.subject, t.activity, t.trial))


def _read_watch_csv(path: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """Read one headerless watch CSV → (seconds-from-start, (T, 3) values).

    Timestamps are wall-clock strings; they arrive in BLE-batched bursts with
    occasional duplicates, so we sort and de-duplicate before interpolation.
    Returns None for unreadable/degenerate files (counted by the caller).
    """
    try:
        df = pd.read_csv(path, header=None, names=["time", "x", "y", "z"],
                         usecols=[0, 1, 2, 3])
    except (pd.errors.ParserError, ValueError):
        return None
    t = pd.to_datetime(df["time"], errors="coerce")
    vals = df[["x", "y", "z"]].apply(pd.to_numeric, errors="coerce")
    ok = t.notna() & vals.notna().all(axis=1)
    if ok.sum() < 2:
        return None
    t = t[ok]
    t_s = (t - t.iloc[0]).dt.total_seconds().to_numpy(dtype=np.float64)
    v = vals[ok].to_numpy(dtype=np.float32)
    order = np.argsort(t_s, kind="stable")
    t_s, v = t_s[order], v[order]
    t_s, keep = np.unique(t_s, return_index=True)
    return t_s, v[keep]


def load_trial_stream(
    trial: SmartFallTrial, hz: int = TARGET_HZ
) -> tuple[np.ndarray, np.ndarray] | None:
    """Load one trial as (time_s, (T, 6) [ax ay az wx wy wz]) at uniform `hz`.

    Accel and gyro are interpolated onto a shared uniform grid spanning their
    overlapping interval. Returns None when the overlap is shorter than one
    2.5 s window — too short to contribute anything downstream.
    """
    accel = _read_watch_csv(trial.accel_path)
    gyro = _read_watch_csv(trial.gyro_path)
    if accel is None or gyro is None:
        return None
    (ta, va), (tg, vg) = accel, gyro

    start = max(ta[0], tg[0])
    end = min(ta[-1], tg[-1])
    if end - start < WINDOW_SAMPLES / hz:
        return None
    n = int(np.floor((end - start) * hz)) + 1
    t = start + np.arange(n) / hz

    data = np.empty((n, 6), dtype=np.float32)
    for c in range(3):
        data[:, c] = np.interp(t, ta, va[:, c])
        data[:, 3 + c] = np.interp(t, tg, vg[:, c])
    return (t - start).astype(np.float64), data


def build_smartfall_adl_bundle(
    dataset_root: Path,
    groups: Iterable[str] = ("young", "old"),
    sample_rate: int = TARGET_HZ,
    stride_samples: int = DEFAULT_STRIDE_SAMPLES,
    max_windows_per_trial: int | None = None,
) -> CloudBundle:
    """Window every usable SmartFall watch ADL trial into a negatives-only CloudBundle.

    Every window: y=0, is_adl=True, phase=BACKGROUND, movement="SF-Axx",
    groups=offset subject id. Schema-identical to the WEDA bundle so the two
    concatenate cleanly for subject-stratified CV, and so the per-user feature
    normaliser ("fit-at-first" analogue) fits each SmartFall subject's own ADL.
    """
    trials = discover_adl_trials(dataset_root, groups)
    if not trials:
        raise RuntimeError(
            f"No paired watch ADL trials found under {dataset_root}. Is "
            f"SmartFallMM extracted at data/raw/SmartFallMM-Dataset-main/ ?"
        )

    X_list: list[np.ndarray] = []
    f_list: list[np.ndarray] = []
    g_list: list[int] = []
    sev_list: list[float] = []
    m_list: list[str] = []
    n_trials_used = 0
    n_trials_skipped = 0
    total_duration_s = 0.0

    for trial in trials:
        stream = load_trial_stream(trial, hz=sample_rate)
        if stream is None:
            n_trials_skipped += 1
            continue
        t, data = stream
        phase_labels = np.full(len(t), Phase.BACKGROUND.value, dtype=np.int64)
        windows = slide(data, t, phase_labels, WINDOW_SAMPLES, stride_samples)
        if max_windows_per_trial is not None:
            windows = windows[:max_windows_per_trial]
        if not windows:
            n_trials_skipped += 1
            continue
        n_trials_used += 1
        total_duration_s += float(t[-1] - t[0])
        for w in windows:
            X_list.append(w.data)
            f_list.append(extract_features(w.data, sample_rate=sample_rate))
            g_list.append(trial.subject_uid)
            sev_list.append(_peak_accel_magnitude(w.data))
            m_list.append(trial.movement)

    if not X_list:
        raise RuntimeError(f"All {len(trials)} SmartFall trials were unusable.")

    n = len(X_list)
    return CloudBundle(
        X_raw=np.stack(X_list).astype(np.float32),
        feats=np.stack(f_list).astype(np.float32),
        y=np.zeros(n, dtype=np.int64),
        groups=np.asarray(g_list, dtype=np.int64),
        is_adl=np.ones(n, dtype=bool),
        severity=np.asarray(sev_list, dtype=np.float32),
        phase=np.full(n, Phase.BACKGROUND.value, dtype=np.int64),
        movement=np.asarray(m_list),
        meta={
            "source": "SmartFallMM-watch-ADL",
            "sample_rate": sample_rate,
            "n_features": len(feature_names()),
            "n_trials_used": n_trials_used,
            "n_trials_skipped": n_trials_skipped,
            "total_duration_s": total_duration_s,
            "groups": list(groups),
            "positive_class": "none (hard-ADL-negatives only)",
        },
    )
