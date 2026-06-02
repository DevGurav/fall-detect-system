"""Read-only cascade evaluation: edge -> cloud false-alarm rate on WEDA-FALL ADL.

In production the cloud only scores windows the EDGE flagged. This measures the
TRUE end-to-end false-alarm behaviour (edge fires AND cloud confirms) on ADL
windows, broken down by movement, to test whether the cloud-standalone FPR gate
is the right bar — the hypothesis being that the edge (a pre-impact free-fall
predictor) never forwards impact-like ADLs (clap / hit-table / jump) because they
lack a run-up.

Honest setup: the cloud side reuses the HELD-OUT test predictions saved by the
last single-split run (no in-sample optimism); the edge model is run on those same
held-out windows. Loads both saved checkpoints; trains nothing.

    python scripts/cascade_eval.py        # from ml/
"""
from __future__ import annotations

import json

import numpy as np
import torch

from fall_guardian_ml.datasets.cloud_dataset import build_cloud_bundle
from fall_guardian_ml.datasets.edge_dataset import ChannelStats
from fall_guardian_ml.models.convlstm_tiny import ConvLSTMTinyConfig
from fall_guardian_ml.models.convlstm_tiny import build_model as build_edge
from fall_guardian_ml.training.train_cloud import (
    DEFAULT_ARTIFACT_DIR,
    DEFAULT_DATASET_ROOT,
    TrainConfig,
    subject_split,
)

EDGE_DIR = DEFAULT_ARTIFACT_DIR.parent / "edge"
WEDA_ADL_NAMES = {
    "D01": "walking", "D02": "jogging", "D03": "stairs", "D04": "sit-stand",
    "D05": "sit-collapse", "D06": "crouch-tie-shoes", "D07": "stumble",
    "D08": "gentle-jump", "D09": "hit-table", "D10": "clapping", "D11": "door",
}
STRIDE_S = 62 / 50.0  # 1.24 s between window starts (50% overlap @ 50 Hz)


def main() -> None:
    # --- cloud side: held-out test predictions from the last single-split run ---
    npz = np.load(DEFAULT_ARTIFACT_DIR / "test_predictions.npz", allow_pickle=True)
    cloud_prob = npz["prob"]
    y = npz["y"].astype(bool)
    is_adl = npz["is_adl"].astype(bool)
    movement = npz["movement"].astype(str)
    subject = npz["subject"]
    cloud_thr = float(npz["threshold"][0])

    # Recover the matching raw windows by rebuilding the bundle + the same split.
    cfg = TrainConfig()
    bundle = build_cloud_bundle(DEFAULT_DATASET_ROOT)
    ti = subject_split(bundle, cfg).test_idx
    assert len(ti) == len(y) and np.array_equal(bundle.y[ti].astype(bool), y), \
        "split/npz misaligned — re-run the single-split training first"
    X_test = bundle.X_raw[ti]

    # --- edge side: load model + its channel stats + operating threshold ---
    eck = torch.load(EDGE_DIR / "convlstm_tiny_fp32.pt", map_location="cpu", weights_only=False)
    ecfg = ConvLSTMTinyConfig(**eck["model_config"])
    assert ecfg.n_channels == X_test.shape[-1], \
        f"edge expects {ecfg.n_channels}ch but windows are {X_test.shape[-1]}ch"
    edge = build_edge(ecfg)
    edge.load_state_dict(eck["state_dict"])
    edge.eval()
    edge_thr = float(eck["threshold"])
    es = json.loads((EDGE_DIR / "channel_stats.json").read_text())
    estats = ChannelStats(mean=np.array(es["mean"], np.float32), std=np.array(es["std"], np.float32))
    with torch.no_grad():
        edge_logit = edge(torch.from_numpy(estats.apply(X_test)).float()).numpy()
    edge_prob = 1.0 / (1.0 + np.exp(-edge_logit))

    edge_fire = edge_prob >= edge_thr
    cloud_fire = cloud_prob >= cloud_thr
    cascade = edge_fire & cloud_fire

    adl = is_adl
    n_adl = int(adl.sum())
    edge_fpr = float(edge_fire[adl].mean())
    cloud_fpr = float(cloud_fire[adl].mean())
    casc_fpr = float(cascade[adl].mean())

    print(f"held-out test: {len(y)} windows, {n_adl} ADL "
          f"(subjects {sorted({int(s) for s in subject})})")
    print(f"thresholds: edge={edge_thr:.3f}  cloud={cloud_thr:.3f}\n")
    print("ADL false-positive rate (per window):")
    print(f"  edge-alone : {edge_fpr:6.3f}")
    print(f"  cloud-alone: {cloud_fpr:6.3f}")
    print(f"  CASCADE    : {casc_fpr:6.3f}   (edge AND cloud)  "
          f"-> {edge_fpr / max(casc_fpr, 1e-9):.0f}x suppression vs edge-alone")

    print("\nby movement (ADL only):  edge_fire | cascade | n")
    for mv in sorted({str(m) for m in movement[adl]}):
        sel = adl & (movement == mv)
        n = int(sel.sum())
        if n:
            nm = WEDA_ADL_NAMES.get(mv, "")
            print(f"  {mv} {nm:16s}: edge {edge_fire[sel].mean():5.2f} | "
                  f"cascade {cascade[sel].mean():6.3f} | n={n}")

    # --- /day estimates (clearly-caveated) ---
    win_per_day = 86400.0 / STRIDE_S
    print("\nfalse-alarm /day estimates (assumptions stated):")
    print(f"  per-window upper bound (no debounce): {casc_fpr * win_per_day:8.1f} alarm-windows/day "
          f"at {win_per_day:.0f} windows/day")
    print("  NOTE: WEDA-FALL ADL is an adversarial, impact-heavy mix (1/11th is clap/jump/"
          "hit-table); a real day has far fewer such events, and a device debounces bursts "
          "to one alarm. A representative continuous-wear sim is the proper /day measurement.")
    # Debounced + realistic-mix illustration: collapse each alarm burst (~one activity
    # instance) to a single alarm, and assume vigorous impact-like ADLs occupy ~30 min/day.
    cas_fp_per_s = casc_fpr / STRIDE_S
    print(f"  debounced illustration: ~{cas_fp_per_s * 1800:.2f} alarms over 30 min of "
          f"continuous impact-like ADL (before burst-debounce); product gate is <=0.5/day.")


if __name__ == "__main__":
    main()
