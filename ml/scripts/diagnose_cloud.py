"""Diagnose the cloud detector's recall failure on held-out WEDA-FALL subjects.

Read-only: loads the saved `transformer_detector_fp32.pt`, reproduces the exact
held-out test evaluation from the last `train_cloud` run (deterministic seed +
preprocessing), then dissects the misses:

  1. Reproduce the headline recall/FPR (sanity vs the training report).
  2. FN breakdown by PHASE — are we missing IMPACT (sharp spike) or POST_IMPACT
     (lying still, ADL-like) windows?
  3. FN breakdown by SUBJECT — a few hard held-out subjects, or uniform?
  4. Test recall/FPR-vs-threshold sweep — is the val-chosen threshold simply
     mis-calibrated for held-out subjects (FPR has headroom under the 2% cap)?

The threshold sweep on test is DIAGNOSTIC ONLY — never used to *select* an
operating point (that would be test-set leakage). Trains nothing.

    python -m scripts.diagnose_cloud        # from ml/
"""
from __future__ import annotations

import numpy as np
import torch

from fall_guardian_ml.datasets.cloud_dataset import build_cloud_bundle
from fall_guardian_ml.datasets.edge_dataset import fit_channel_stats
from fall_guardian_ml.datasets.pre_impact_labels import Phase
from fall_guardian_ml.eval.metrics import compute_metrics
from fall_guardian_ml.models.transformer_detector import (
    TransformerDetectorConfig,
    build_model,
)
from fall_guardian_ml.training.train_cloud import (
    DEFAULT_ARTIFACT_DIR,
    DEFAULT_DATASET_ROOT,
    TrainConfig,
    _apply_feature_norm,
    _apply_platt,
    _fit_feature_norm,
    subject_split,
)


def main() -> None:
    cfg = TrainConfig()  # defaults match the run: seed 42, feature_norm per_user
    bundle = build_cloud_bundle(DEFAULT_DATASET_ROOT)
    split = subject_split(bundle, cfg)
    print(f"[data] {bundle.summary()}")
    print(f"[split] test subjects {split.test_subjects}")

    # Reproduce preprocessing exactly (deterministic from seed 42).
    chan = fit_channel_stats(bundle.X_raw[split.train_idx])
    Xn = chan.apply(bundle.X_raw)
    f_global, f_per_user = _fit_feature_norm(bundle, split.train_idx, cfg.feature_norm)
    Fn = _apply_feature_norm(bundle.feats, bundle.groups, f_global, f_per_user, cfg.feature_norm)

    ckpt = torch.load(
        DEFAULT_ARTIFACT_DIR / "transformer_detector_fp32.pt",
        map_location="cpu", weights_only=False,
    )
    model = build_model(TransformerDetectorConfig(**ckpt["model_config"]))
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    threshold = float(ckpt["threshold"])
    platt = ckpt["platt"]

    ti = split.test_idx
    with torch.no_grad():
        out = model(torch.from_numpy(Xn[ti]).float(), torch.from_numpy(Fn[ti]).float())
    probs = _apply_platt(out.fall_logit.numpy(), platt)
    y = bundle.y[ti]
    is_adl = bundle.is_adl[ti]
    phase = bundle.phase[ti]
    subj = bundle.groups[ti]
    preds = probs >= threshold

    m = compute_metrics(y, probs, is_adl, threshold)
    print("\n=== reproduce headline (sanity vs training report) ===")
    print(f"  recall={m.recall:.3f}  fpr_adl={m.fpr_adl:.3f}  thr={threshold:.4f}  "
          f"TP={m.tp} FP={m.fp} TN={m.tn} FN={m.fn}")

    pos = y == 1
    fn = pos & ~preds
    print("\n=== FN by phase (of the positive = IMPACT+POST_IMPACT windows) ===")
    for ph in (Phase.IMPACT, Phase.POST_IMPACT):
        p_pos = pos & (phase == ph.value)
        p_fn = fn & (phase == ph.value)
        npos, nfn = int(p_pos.sum()), int(p_fn.sum())
        rec = 1 - nfn / max(npos, 1)
        print(f"  {ph.name:12s}: positives={npos:3d}  FN={nfn:3d}  recall={rec:.3f}")

    print("\n=== FN by held-out subject ===")
    for s in sorted({int(x) for x in subj[pos]}):
        s_pos = pos & (subj == s)
        s_fn = fn & (subj == s)
        npos, nfn = int(s_pos.sum()), int(s_fn.sum())
        print(f"  U{s:02d}: positives={npos:3d}  FN={nfn:3d}  recall={1 - nfn / max(npos, 1):.3f}")

    print("\n=== test recall/FPR vs threshold (DIAGNOSTIC ONLY -- not for selection) ===")
    for t in (0.05, 0.10, 0.15, 0.20, 0.25, threshold, 0.40, 0.50):
        mm = compute_metrics(y, probs, is_adl, t)
        print(f"  thr={t:.4f}  recall={mm.recall:.3f}  fpr_adl={mm.fpr_adl:.3f}")
    # Lowest threshold that would reach recall >= 0.97 on test, and its FPR.
    for t in sorted(np.unique(probs)):
        mm = compute_metrics(y, probs, is_adl, float(t))
        if mm.recall >= 0.97:
            print(f"  -> test recall >=0.97 reachable at thr~{t:.4f}: "
                  f"recall={mm.recall:.3f}  fpr_adl={mm.fpr_adl:.3f}  "
                  f"(under 2% cap: {'YES' if mm.fpr_adl <= 0.02 else 'NO'})")
            break
    else:
        print("  -> recall >=0.97 NOT reachable at any threshold on test (capacity/generalization issue)")


if __name__ == "__main__":
    main()
