"""Phase 30: continuous-wear simulation — the honest false-alarms-per-day number.

Per-window FPR is not what a wearer experiences. This script replays SmartFall
watch ADL streams through the FULL production cascade and counts ALARMS:

    50 Hz 6-ch stream → 2.5 s windows (62-sample stride, as on-device)
      → edge ConvLSTM (recall-first gate, fires often by design)
      → windows the edge forwards → cloud Transformer ONNX, served EXACTLY as
        the backend does (meta.json channel/feature norms + Platt + threshold)
      → cascade-positive windows → 30 s BURST-DEBOUNCE: positives within
        `debounce_s` of the last alarm collapse into that alarm, modelling the
        device's grace-period/cooldown (one shake-out ≠ five pages).

Per subject, trials are concatenated on a single wear clock (debounce spans
trial boundaries; windows never do). The reported number is

    alarms / total replayed hours × 8   →   alarms per 8-hour wear day

Product gate (PLAN Phase 30): ≤ 0.5 alarms/day. NOTE the estimate is
CONSERVATIVE: SmartFall ADL trials are wall-to-wall scripted activity (sweeping,
waving, jacket on/off) with no idle/rest time, so a real day looks quieter.

Env-overridable paths (Colab / "write now, run later"):

    FG_SMARTFALL_ROOT      extracted SmartFallMM-Dataset-main directory
    FG_EDGE_ARTIFACT_DIR   edge checkpoint dir (default ml/artifacts/edge)
    FG_BACKEND_MODEL_DIR   cloud_detector.onnx location (default backend/app/model)
    FG_CLOUD_ARTIFACT_DIR  where the results JSON lands (default ml/artifacts/cloud)

Run it (from ml/):

    python scripts/continuous_wear_sim.py
    python scripts/continuous_wear_sim.py --debounce-s 30 --groups young old
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch

from fall_guardian_ml.datasets.edge_dataset import ChannelStats
from fall_guardian_ml.datasets.smartfall_adl import (
    SmartFallTrial,
    discover_adl_trials,
    load_trial_stream,
)
from fall_guardian_ml.features.extraction import extract_features
from fall_guardian_ml.features.windowing import DEFAULT_STRIDE_SAMPLES, WINDOW_SAMPLES
from fall_guardian_ml.models.convlstm_tiny import ConvLSTMTinyConfig
from fall_guardian_ml.models.convlstm_tiny import build_model as build_edge

ML_ROOT = Path(__file__).resolve().parents[1]


def _env_path(var: str, default: Path) -> Path:
    return Path(os.environ.get(var) or default)


DEFAULT_SMARTFALL_ROOT = _env_path(
    "FG_SMARTFALL_ROOT", ML_ROOT / "data" / "raw" / "SmartFallMM-Dataset-main")
DEFAULT_EDGE_DIR = _env_path("FG_EDGE_ARTIFACT_DIR", ML_ROOT / "artifacts" / "edge")
DEFAULT_MODEL_DIR = _env_path(
    "FG_BACKEND_MODEL_DIR", ML_ROOT.parent / "backend" / "app" / "model")
DEFAULT_OUT_DIR = _env_path("FG_CLOUD_ARTIFACT_DIR", ML_ROOT / "artifacts" / "cloud")

SAMPLE_RATE = 50
WEAR_DAY_HOURS = 8.0          # the PLAN's "/day" unit is an 8-hour wear day
TARGET_ALARMS_PER_DAY = 0.5
EDGE_BATCH = 512


# ─── Model loading ───────────────────────────────────────────────────────────


def load_edge(edge_dir: Path):
    """Edge ConvLSTM + its global channel stats + operating threshold."""
    ck = torch.load(edge_dir / "convlstm_tiny_fp32.pt", map_location="cpu",
                    weights_only=False)
    model = build_edge(ConvLSTMTinyConfig(**ck["model_config"]))
    model.load_state_dict(ck["state_dict"])
    model.eval()
    stats_raw = json.loads((edge_dir / "channel_stats.json").read_text())
    stats = ChannelStats(mean=np.asarray(stats_raw["mean"], np.float32),
                         std=np.asarray(stats_raw["std"], np.float32))
    return model, stats, float(ck["threshold"])


class CloudOnnx:
    """The cloud detector served the way the backend serves it (detector.py):
    meta.json channel/feature normalisers → ONNX → Platt → threshold."""

    def __init__(self, model_dir: Path) -> None:
        import onnxruntime as ort

        onnx_path = Path(model_dir) / "cloud_detector.onnx"
        self.meta = json.loads(onnx_path.with_suffix(".meta.json").read_text())
        self.session = ort.InferenceSession(str(onnx_path),
                                            providers=["CPUExecutionProvider"])
        self.threshold = float(self.meta["threshold"])

    @staticmethod
    def _standardize(x: np.ndarray, stats: dict) -> np.ndarray:
        mean = np.asarray(stats["mean"], dtype=np.float32)
        std = np.asarray(stats["std"], dtype=np.float32)
        return ((x - mean) / np.where(std > 0, std, 1.0)).astype(np.float32)

    def predict_prob(self, windows: np.ndarray) -> np.ndarray:
        """Calibrated fall probability for (B, 125, 6) raw windows.

        Runs the graph one window at a time: the exported ONNX declares dynamic
        batch axes but bakes a batch-1 reshape into the graph, so B>1 fails at
        runtime. Batch-1 is also exactly how the backend serves it, and the
        cloud only ever sees the (few) edge-forwarded windows here.
        """
        raw = self._standardize(windows, self.meta["channel_stats"])
        feats = np.stack([extract_features(w, sample_rate=SAMPLE_RATE) for w in windows])
        feats = self._standardize(feats, self.meta["feature_norm"])
        logit = np.empty(len(raw), dtype=np.float64)
        for i in range(len(raw)):
            out, _severity = self.session.run(
                None, {"raw": raw[i: i + 1], "feats": feats[i: i + 1]})
            logit[i] = float(np.asarray(out).reshape(-1)[0])
        platt = self.meta.get("platt")
        z = (platt["coef"] * logit + platt["intercept"]) if platt else logit
        return 1.0 / (1.0 + np.exp(-z))


# ─── Stream replay ───────────────────────────────────────────────────────────


@dataclass
class SubjectResult:
    subject_uid: int
    group: str
    wear_hours: float
    n_windows: int
    n_edge_fired: int
    n_cascade_pos: int
    n_alarms: int                 # after burst-debounce
    alarm_movements: list[str]    # activity code of each debounced alarm

    @property
    def alarms_per_day(self) -> float:
        return self.n_alarms / self.wear_hours * WEAR_DAY_HOURS if self.wear_hours else 0.0


def _window_starts(n_samples: int, stride: int) -> range:
    return range(0, n_samples - WINDOW_SAMPLES + 1, stride)


def replay_subject(
    trials: list[SmartFallTrial],
    edge_model, edge_stats: ChannelStats, edge_thr: float,
    cloud: CloudOnnx,
    debounce_s: float,
    stride: int = DEFAULT_STRIDE_SAMPLES,
) -> SubjectResult | None:
    """Replay one subject's concatenated ADL trials through the cascade.

    Windows are cut WITHIN each trial (no artificial junction windows), but all
    sit on one continuous wear clock, so the debounce behaves like a real
    device cooldown across back-to-back activities.
    """
    windows: list[np.ndarray] = []
    end_times: list[float] = []
    movements: list[str] = []
    clock_s = 0.0

    for trial in trials:
        stream = load_trial_stream(trial, hz=SAMPLE_RATE)
        if stream is None:
            continue
        t, data = stream
        for start in _window_starts(len(data), stride):
            windows.append(data[start: start + WINDOW_SAMPLES])
            end_times.append(clock_s + t[start + WINDOW_SAMPLES - 1])
            movements.append(trial.movement)
        clock_s += float(t[-1] - t[0])

    if not windows:
        return None
    X = np.stack(windows).astype(np.float32)
    end_t = np.asarray(end_times)

    # Edge gate: recall-first, fires often (that's its design — Phase 14 pivot).
    edge_prob = np.empty(len(X), dtype=np.float64)
    with torch.no_grad():
        for i in range(0, len(X), EDGE_BATCH):
            batch = torch.from_numpy(edge_stats.apply(X[i: i + EDGE_BATCH])).float()
            edge_prob[i: i + len(batch)] = torch.sigmoid(edge_model(batch)).numpy().reshape(-1)
    edge_fire = edge_prob >= edge_thr

    # Cloud gate: only the forwarded windows, exactly like production traffic.
    cascade = np.zeros(len(X), dtype=bool)
    fired = np.flatnonzero(edge_fire)
    if fired.size:
        cloud_prob = cloud.predict_prob(X[fired])
        cascade[fired] = cloud_prob >= cloud.threshold

    # Burst-debounce: a cascade-positive within `debounce_s` of the last alarm
    # belongs to that alarm's burst; only a later one pages the caregiver again.
    n_alarms = 0
    alarm_movements: list[str] = []
    last_alarm_t = -np.inf
    for i in np.flatnonzero(cascade):
        if end_t[i] - last_alarm_t > debounce_s:
            n_alarms += 1
            alarm_movements.append(movements[i])
            last_alarm_t = end_t[i]

    return SubjectResult(
        subject_uid=trials[0].subject_uid,
        group=trials[0].group,
        wear_hours=clock_s / 3600.0,
        n_windows=len(X),
        n_edge_fired=int(edge_fire.sum()),
        n_cascade_pos=int(cascade.sum()),
        n_alarms=n_alarms,
        alarm_movements=alarm_movements,
    )


# ─── Orchestration ───────────────────────────────────────────────────────────


def run_simulation(
    smartfall_root: Path = DEFAULT_SMARTFALL_ROOT,
    edge_dir: Path = DEFAULT_EDGE_DIR,
    model_dir: Path = DEFAULT_MODEL_DIR,
    out_dir: Path = DEFAULT_OUT_DIR,
    groups: tuple[str, ...] = ("young", "old"),
    debounce_s: float = 30.0,
    target_per_day: float = TARGET_ALARMS_PER_DAY,
) -> dict:
    edge_model, edge_stats, edge_thr = load_edge(edge_dir)
    cloud = CloudOnnx(model_dir)
    print(f"[models] edge thr={edge_thr:.3f} | cloud {cloud.meta['model_version']} "
          f"thr={cloud.threshold:.4f} | debounce={debounce_s:.0f}s")

    trials = discover_adl_trials(smartfall_root, groups)
    by_subject: dict[int, list[SmartFallTrial]] = defaultdict(list)
    for tr in trials:
        by_subject[tr.subject_uid].append(tr)
    print(f"[data] {len(trials)} paired watch ADL trials, {len(by_subject)} subjects")

    results: list[SubjectResult] = []
    for uid in sorted(by_subject):
        r = replay_subject(by_subject[uid], edge_model, edge_stats, edge_thr,
                           cloud, debounce_s)
        if r is None:
            continue
        results.append(r)
        print(f"  S{uid:04d} ({r.group:5s}): {r.wear_hours:5.2f} h, {r.n_windows:5d} win, "
              f"edge {r.n_edge_fired:4d}, cascade {r.n_cascade_pos:3d}, "
              f"alarms {r.n_alarms:2d}  -> {r.alarms_per_day:5.2f}/day")

    total_hours = sum(r.wear_hours for r in results)
    total_alarms = sum(r.n_alarms for r in results)
    total_windows = sum(r.n_windows for r in results)
    total_edge = sum(r.n_edge_fired for r in results)
    total_cascade = sum(r.n_cascade_pos for r in results)
    alarms_per_day = total_alarms / total_hours * WEAR_DAY_HOURS if total_hours else 0.0

    by_movement: dict[str, int] = defaultdict(int)
    for r in results:
        for mv in r.alarm_movements:
            by_movement[mv] += 1

    ok = alarms_per_day <= target_per_day
    print("\n" + "=" * 64)
    print(f"  CONTINUOUS-WEAR SIMULATION  (SmartFall watch ADL, {debounce_s:.0f}s debounce)")
    print("=" * 64)
    print(f"  Replayed wear     : {total_hours:7.2f} h over {len(results)} subjects")
    print(f"  Windows           : {total_windows} | edge fired {total_edge} "
          f"({100 * total_edge / max(total_windows, 1):.1f}%) | "
          f"cascade {total_cascade} ({100 * total_cascade / max(total_windows, 1):.2f}%)")
    print(f"  Debounced alarms  : {total_alarms}")
    print(f"  ALARMS / {WEAR_DAY_HOURS:.0f}h DAY  : {alarms_per_day:6.3f}   "
          f"target <={target_per_day} {'[PASS]' if ok else '[FAIL]'}")
    if by_movement:
        print("  Alarms by activity:")
        for mv, n in sorted(by_movement.items(), key=lambda kv: -kv[1]):
            print(f"      {mv}: {n}")
    print("  NOTE: scripted wall-to-wall ADL with no idle time — a conservative")
    print("        (pessimistic) stand-in for a real wear day.")
    print("=" * 64 + "\n")

    summary = {
        "model_version": cloud.meta["model_version"],
        "edge_threshold": edge_thr,
        "cloud_threshold": cloud.threshold,
        "debounce_s": debounce_s,
        "wear_day_hours": WEAR_DAY_HOURS,
        "target_alarms_per_day": target_per_day,
        "groups": list(groups),
        "n_subjects": len(results),
        "total_wear_hours": total_hours,
        "total_windows": total_windows,
        "edge_fire_rate": total_edge / max(total_windows, 1),
        "cascade_positive_rate": total_cascade / max(total_windows, 1),
        "total_alarms": total_alarms,
        "alarms_per_day": alarms_per_day,
        "meets_target": ok,
        "alarms_by_activity": dict(sorted(by_movement.items())),
        "per_subject": [
            {"subject_uid": r.subject_uid, "group": r.group,
             "wear_hours": r.wear_hours, "n_alarms": r.n_alarms,
             "alarms_per_day": r.alarms_per_day}
            for r in results
        ],
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "continuous_wear_sim.json"
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"[out] wrote {out_path}")
    return summary


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Replay SmartFall ADL through the edge->cloud cascade and "
                    "measure debounced false alarms per 8-hour wear day.")
    p.add_argument("--smartfall-root", type=Path, default=DEFAULT_SMARTFALL_ROOT)
    p.add_argument("--edge-dir", type=Path, default=DEFAULT_EDGE_DIR)
    p.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR,
                   help="dir containing cloud_detector.onnx + .meta.json")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument("--groups", nargs="+", choices=["young", "old"],
                   default=["young", "old"])
    p.add_argument("--debounce-s", type=float, default=30.0,
                   help="burst-debounce window (seconds)")
    p.add_argument("--target-per-day", type=float, default=TARGET_ALARMS_PER_DAY)
    args = p.parse_args(argv)

    run_simulation(
        smartfall_root=args.smartfall_root,
        edge_dir=args.edge_dir,
        model_dir=args.model_dir,
        out_dir=args.out_dir,
        groups=tuple(args.groups),
        debounce_s=args.debounce_s,
        target_per_day=args.target_per_day,
    )


if __name__ == "__main__":
    main()
