"""WEDA-FALL dataset loader.

WEDA-FALL is a wrist-worn (Fitbit Sense smartwatch) dataset of elderly + young
subjects performing 11 ADL types (D01-D11) and 8 fall types (F01-F08).

The Fitbit Sense delivers samples in Bluetooth-batched bursts, so the raw
timestamps in the CSVs are NON-UNIFORM (e.g. 0.000, 0.001, 0.003, 0.005, then
nothing until 0.121). The "50Hz" folder name describes the effective average
rate; samples are not actually spaced at 20 ms.

This loader resamples each sensor stream to true uniform 50 Hz via linear
interpolation before any downstream windowing or feature extraction.

Dataset layout (after extracting the GitHub ZIP into ml/data/raw/):

    WEDA-FALL-main/
    ├── README.md
    └── dataset/
        ├── fall_timestamps.csv               ← manually-labeled fall windows
        ├── 50Hz/                             ← we use this sample rate
        │   ├── D01/  U<id>_R<trial>_{accel,gyro,orientation,vertical_accel}.csv
        │   ├── ...   (D02-D11, F01-F08)
        ├── 40Hz/   25Hz/   10Hz/   5Hz/      (unused)

Reference: github.com/joaojtmarques/WEDA-FALL
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

# Movement codes from the WEDA-FALL README.
ADL_CODES = [f"D{i:02d}" for i in range(1, 12)]   # D01..D11
FALL_CODES = [f"F{i:02d}" for i in range(1, 9)]   # F01..F08

# Subject groupings (from WEDA-FALL README Tables 3 & 3-bis).
YOUNG_SUBJECTS = list(range(1, 15))    # U01..U14, ages 20–46
ELDER_SUBJECTS = list(range(21, 32))   # U21..U31, ages 77–95

# File naming pattern: U<user_id>_R<trial>_<sensor>.csv
_FNAME_RE = re.compile(r"^U(?P<user>\d+)_R(?P<trial>\d+)_(?P<sensor>\w+)\.csv$")

# Locked target uniform sample rate (matches 2.5 s × 50 Hz = 125-sample window).
TARGET_HZ = 50


@dataclass(frozen=True)
class RecordingId:
    """Unique identifier for one WEDA-FALL recording (one activity trial)."""

    movement: str       # e.g. "F01" or "D01"
    user_id: int
    trial: int

    @property
    def is_fall(self) -> bool:
        return self.movement.startswith("F")

    @property
    def is_elder(self) -> bool:
        return self.user_id in ELDER_SUBJECTS

    @property
    def filename_stem(self) -> str:
        return f"U{self.user_id:02d}_R{self.trial:02d}"

    @property
    def label_key(self) -> str:
        """Key used in fall_timestamps.csv (e.g. 'F01/U01_R01')."""
        return f"{self.movement}/{self.filename_stem}"


@dataclass
class Recording:
    """One recording = aligned accel + gyro (+ orientation) streams at uniform Hz."""

    id: RecordingId
    time: np.ndarray            # shape (T,) — seconds from recording start
    accel: np.ndarray           # shape (T, 3) — m/s² (x, y, z)
    gyro: np.ndarray            # shape (T, 3) — rad/s (x, y, z)
    orientation: np.ndarray | None = None  # shape (T, 4) — quaternion (s, i, j, k)
    fall_window: tuple[float, float] | None = None  # (start, end) from fall_timestamps.csv

    @property
    def sample_rate(self) -> int:
        return TARGET_HZ

    @property
    def duration_s(self) -> float:
        return float(self.time[-1] - self.time[0]) if self.time.size > 1 else 0.0


def _read_sensor_csv(path: Path) -> pd.DataFrame:
    """Read one WEDA-FALL sensor CSV and rename its time column to `time`.

    The CSVs have non-uniform timestamps in the first column (named
    `accel_time_list` / `gyro_time_list` / `orientation_time_list`).
    """
    df = pd.read_csv(path)
    time_cols = [c for c in df.columns if "time" in c.lower()]
    if not time_cols:
        raise ValueError(f"No time column found in {path}; columns: {df.columns.tolist()}")
    return df.rename(columns={time_cols[0]: "time"})


def _resample_to_uniform_hz(
    df: pd.DataFrame,
    value_cols: list[str],
    hz: int = TARGET_HZ,
) -> pd.DataFrame:
    """Resample a non-uniformly-timestamped DataFrame to a uniform sample rate.

    Uses linear interpolation on each value column.

    Why this is needed: the Fitbit Sense delivers samples in BLE-batched bursts,
    so raw timestamps cluster (e.g. 0.000, 0.001, 0.003, 0.005 then a gap to 0.121).
    A uniform resample is required before any windowed feature extraction.

    Returns a DataFrame with columns ['time'] + value_cols on a uniform grid
    spaced 1/hz apart, starting at the first raw sample's timestamp.
    """
    if df.empty:
        return df

    t_raw = df["time"].to_numpy()
    t_start, t_end = float(t_raw[0]), float(t_raw[-1])
    dt = 1.0 / hz
    n_samples = int(np.floor((t_end - t_start) / dt)) + 1
    t_uniform = t_start + np.arange(n_samples) * dt

    out = {"time": t_uniform}
    for col in value_cols:
        if col not in df.columns:
            raise KeyError(f"Column {col!r} not found in {df.columns.tolist()}")
        out[col] = np.interp(t_uniform, t_raw, df[col].to_numpy())
    return pd.DataFrame(out)


def load_fall_timestamps(dataset_root: Path) -> pd.DataFrame:
    """Load the manually-labeled fall window timestamps (one row per fall recording).

    Columns: filename (e.g. 'F01/U01_R01'), start_time, end_time (seconds).
    """
    df = pd.read_csv(dataset_root / "dataset" / "fall_timestamps.csv")
    # The first column header has a UTF-8 BOM in the shipped file — strip it.
    df.columns = [c.lstrip("﻿") for c in df.columns]
    return df


def load_recording(
    dataset_root: Path,
    rec_id: RecordingId,
    sample_rate: int = TARGET_HZ,
    include_orientation: bool = True,
    fall_timestamps: pd.DataFrame | None = None,
) -> Recording:
    """Load one WEDA-FALL recording, resampled to uniform `sample_rate` Hz.

    Parameters
    ----------
    dataset_root : Path to the extracted WEDA-FALL-main folder.
    rec_id : which recording to load.
    sample_rate : target uniform Hz (default 50, must match a sub-folder).
    include_orientation : also load + resample the orientation quaternion stream.
    fall_timestamps : pre-loaded fall_timestamps DataFrame for fall-window lookup
        (load once with `load_fall_timestamps`, then pass into many `load_recording`
        calls — avoids re-reading the CSV for every recording).
    """
    base = dataset_root / "dataset" / f"{sample_rate}Hz" / rec_id.movement
    stem = rec_id.filename_stem

    accel_df = _read_sensor_csv(base / f"{stem}_accel.csv")
    accel_u = _resample_to_uniform_hz(
        accel_df, ["accel_x_list", "accel_y_list", "accel_z_list"], hz=sample_rate
    )

    gyro_df = _read_sensor_csv(base / f"{stem}_gyro.csv")
    gyro_u = _resample_to_uniform_hz(
        gyro_df, ["gyro_x_list", "gyro_y_list", "gyro_z_list"], hz=sample_rate
    )

    # Align accel + gyro on a common time interval. Their raw start/end can differ
    # by a few ms because the Fitbit Sense doesn't emit them in lockstep.
    common_start = max(accel_u["time"].iloc[0], gyro_u["time"].iloc[0])
    common_end = min(accel_u["time"].iloc[-1], gyro_u["time"].iloc[-1])
    accel_u = accel_u[(accel_u["time"] >= common_start) & (accel_u["time"] <= common_end)]
    gyro_u = gyro_u[(gyro_u["time"] >= common_start) & (gyro_u["time"] <= common_end)]
    accel_u = accel_u.reset_index(drop=True)
    gyro_u = gyro_u.reset_index(drop=True)

    # Truncate to common length (interp may differ by ±1 sample after slicing).
    n = min(len(accel_u), len(gyro_u))
    accel_u = accel_u.iloc[:n]
    gyro_u = gyro_u.iloc[:n]

    orientation_arr: np.ndarray | None = None
    ori_path = base / f"{stem}_orientation.csv"
    if include_orientation and ori_path.exists():
        ori_df = _read_sensor_csv(ori_path)
        ori_u = _resample_to_uniform_hz(
            ori_df,
            [
                "orientation_s_list",
                "orientation_i_list",
                "orientation_j_list",
                "orientation_k_list",
            ],
            hz=sample_rate,
        )
        ori_u = ori_u[(ori_u["time"] >= common_start) & (ori_u["time"] <= common_end)]
        ori_u = ori_u.reset_index(drop=True).iloc[:n]
        orientation_arr = ori_u[
            [
                "orientation_s_list",
                "orientation_i_list",
                "orientation_j_list",
                "orientation_k_list",
            ]
        ].to_numpy()

    # Fall-window lookup (only meaningful for fall recordings).
    fall_window: tuple[float, float] | None = None
    if rec_id.is_fall and fall_timestamps is not None:
        match = fall_timestamps[fall_timestamps["filename"] == rec_id.label_key]
        if not match.empty:
            row = match.iloc[0]
            fall_window = (float(row["start_time"]), float(row["end_time"]))

    return Recording(
        id=rec_id,
        time=accel_u["time"].to_numpy(),
        accel=accel_u[["accel_x_list", "accel_y_list", "accel_z_list"]].to_numpy(),
        gyro=gyro_u[["gyro_x_list", "gyro_y_list", "gyro_z_list"]].to_numpy(),
        orientation=orientation_arr,
        fall_window=fall_window,
    )


def discover_recordings(
    dataset_root: Path,
    sample_rate: int = TARGET_HZ,
    movements: Iterable[str] | None = None,
    include_young: bool = True,
    include_elder: bool = True,
) -> list[RecordingId]:
    """Walk the dataset directory and return every (movement, user, trial) triple
    available at the requested sample rate.
    """
    base = dataset_root / "dataset" / f"{sample_rate}Hz"
    if not base.exists():
        raise FileNotFoundError(
            f"{base} does not exist — extract WEDA-FALL into ml/data/raw/ first."
        )

    selected = list(movements) if movements else (ADL_CODES + FALL_CODES)

    out: list[RecordingId] = []
    for movement in selected:
        movement_dir = base / movement
        if not movement_dir.exists():
            continue
        # Each recording has 4 sensor files; we discover by looking at the accel files.
        for fpath in movement_dir.glob("U*_R*_accel.csv"):
            m = _FNAME_RE.match(fpath.name)
            if not m:
                continue
            user_id = int(m["user"])
            trial = int(m["trial"])
            if user_id in YOUNG_SUBJECTS and not include_young:
                continue
            if user_id in ELDER_SUBJECTS and not include_elder:
                continue
            out.append(RecordingId(movement=movement, user_id=user_id, trial=trial))

    return sorted(out, key=lambda r: (r.movement, r.user_id, r.trial))
