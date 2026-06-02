"""Train the Transformer cloud detector with MLflow tracking.

Week-C precision gate. Mirrors the Week-B edge pipeline (train_edge.py) so the
two share one mental model:

    assemble windows → subject-stratified split → standardize raw + per-user
    z-score features → train Transformer (BCE detection + MSE severity)
    → pick threshold for the recall floor → evaluate (recall, FPR-on-ADL,
    severity MAE) → calibrate → log everything to MLflow → save the FP32
    checkpoint + the normalisers + threshold + a sample API payload.

Validation methodology (same non-negotiables as the edge, per ARCHITECTURE §4.7):
  • Subject-stratified split — held-out test subjects never appear in training.
  • Honest metrics — recall + FPR-on-ADL (+ severity MAE), not accuracy.
  • Everything MLflow-tracked under the "fall-guardian/cloud" experiment.

The cloud is the PRECISION gate: it must keep recall high (don't drop a real fall
the edge caught) while suppressing the edge's ~20% ADL false positives. So we use
the same recall-first threshold selection as the edge (recall floor at lowest FPR).

Run it:
    python -m fall_guardian_ml.training.train_cloud                 # real WEDA-FALL
    python -m fall_guardian_ml.training.train_cloud --synthetic     # smoke test
"""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np

from fall_guardian_ml.datasets.cloud_dataset import (
    CloudBundle,
    build_cloud_bundle,
    make_synthetic_cloud_bundle,
)
from fall_guardian_ml.datasets.edge_dataset import fit_channel_stats
from fall_guardian_ml.eval.metrics import compute_metrics, pick_threshold_for_recall
from fall_guardian_ml.features.normalization import ZScoreParams, fit_zscore
from fall_guardian_ml.models.transformer_detector import (
    TransformerDetectorConfig,
    build_model,
    count_parameters,
)

# Repo paths: this file is ml/src/fall_guardian_ml/training/train_cloud.py
ML_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET_ROOT = ML_ROOT / "data" / "raw" / "WEDA-FALL-main"
DEFAULT_ARTIFACT_DIR = ML_ROOT / "artifacts" / "cloud"

EXPERIMENT_NAME = "fall-guardian/cloud"
MODEL_VERSION = "cloud-transformer-v0.1"
# Product targets for the cloud detector (MODEL_CARD §3.1).
TARGET_RECALL = 0.97
TARGET_FPR_ADL = 0.02
# Severity → enum cut-points (m/s²), matching the backend stub so train == serve.
SEVERITY_MEDIUM_MS2 = 20.0
SEVERITY_HIGH_MS2 = 30.0


@dataclass
class TrainConfig:
    """All knobs for one cloud training run — logged verbatim to MLflow params."""

    epochs: int = 40
    batch_size: int = 128
    lr: float = 1e-3
    weight_decay: float = 1e-4
    test_fraction: float = 0.2          # fraction of SUBJECTS held out for test
    val_fraction: float = 0.15          # fraction of TRAIN subjects used for val
    seed: int = 42
    # Recall-first operating point (same rationale as the edge): a missed fall is
    # the fatal error, so guarantee recall ≥ this floor and take the lowest FPR
    # among thresholds that meet it. Set to the product floor; raise it if the
    # val→test gap (flagged for the edge) pushes held-out recall under target.
    target_recall: float = TARGET_RECALL
    # Weight on the severity (MSE) head relative to detection (BCE). Small: the
    # detection logit is the gate; severity is a secondary, standardized regression.
    severity_loss_weight: float = 0.2
    # Per-user z-score on engineered features (the locked pipeline step + the
    # personalization story) vs a single global normaliser. "per_user" fits each
    # subject's own ADL windows (unsupervised; matches on-device calibration).
    feature_norm: str = "per_user"     # "per_user" | "global"
    synthetic: bool = False
    model: TransformerDetectorConfig = field(default_factory=TransformerDetectorConfig)

    def flat_params(self) -> dict[str, object]:
        d = {k: v for k, v in asdict(self).items() if k != "model"}
        d.update({f"model.{k}": v for k, v in asdict(self.model).items()})
        return d


# ─── Reproducibility ─────────────────────────────────────────────────────────


def _seed_everything(seed: int) -> None:
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


# ─── Subject-stratified split (mirrors train_edge.subject_split) ─────────────


@dataclass
class Split:
    train_idx: np.ndarray
    val_idx: np.ndarray
    test_idx: np.ndarray
    test_subjects: list[int]
    val_subjects: list[int]


def subject_split(bundle: CloudBundle, cfg: TrainConfig) -> Split:
    """Partition windows by SUBJECT so no subject is in more than one split.

    Fall subjects (the only source of positive IMPACT/POST_IMPACT windows) are
    spread across train/val/test so every split can actually measure recall.
    """
    rng = np.random.default_rng(cfg.seed)
    subjects = np.array(sorted({int(s) for s in bundle.groups}))

    pos_subjects = np.array(sorted({int(s) for s in bundle.groups[bundle.y == 1]}))
    neg_only = np.array([s for s in subjects if s not in set(pos_subjects.tolist())])

    def _carve(pool: np.ndarray, frac: float) -> tuple[list[int], np.ndarray]:
        pool = pool.copy()
        rng.shuffle(pool)
        k = max(1, int(round(len(pool) * frac))) if len(pool) else 0
        return pool[:k].tolist(), pool[k:]

    test_pos, rest_pos = _carve(pos_subjects, cfg.test_fraction)
    test_neg, rest_neg = _carve(neg_only, cfg.test_fraction)
    test_subjects = sorted(test_pos + test_neg)

    val_pos, train_pos = _carve(rest_pos, cfg.val_fraction)
    val_neg, train_neg = _carve(rest_neg, cfg.val_fraction)
    val_subjects = sorted(val_pos + val_neg)
    train_subjects = sorted(train_pos.tolist() + train_neg.tolist())

    def _mask(subs: list[int]) -> np.ndarray:
        want = set(subs)
        return np.array([i for i, s in enumerate(bundle.groups) if int(s) in want])

    return Split(
        train_idx=_mask(train_subjects),
        val_idx=_mask(val_subjects),
        test_idx=_mask(test_subjects),
        test_subjects=test_subjects,
        val_subjects=val_subjects,
    )


# ─── Feature normalisation (per-user z-score, global fallback) ───────────────


def _fit_feature_norm(
    bundle: CloudBundle, train_idx: np.ndarray, mode: str
) -> tuple[ZScoreParams, dict[int, ZScoreParams]]:
    """Fit the engineered-feature normaliser.

    Returns (global_fallback, per_user). The global fallback is fit on TRAIN ADL
    windows only (leak-free). For "per_user", each subject additionally gets a
    normaliser fit on *that subject's own ADL windows* — unsupervised and
    subject-local, exactly the ~10–15 min ADL calibration the watch does at
    pairing, so fitting it for held-out subjects is not label leakage.
    """
    train_adl = bundle.is_adl.copy()
    mask = np.zeros(len(bundle), dtype=bool)
    mask[train_idx] = True
    train_adl &= mask
    global_params = fit_zscore(bundle.feats[train_adl]) if train_adl.any() \
        else fit_zscore(bundle.feats[train_idx])

    per_user: dict[int, ZScoreParams] = {}
    if mode == "per_user":
        for s in sorted({int(g) for g in bundle.groups}):
            sub_adl = (bundle.groups == s) & bundle.is_adl
            per_user[s] = fit_zscore(bundle.feats[sub_adl]) if sub_adl.any() else global_params
    return global_params, per_user


def _apply_feature_norm(
    feats: np.ndarray,
    groups: np.ndarray,
    global_params: ZScoreParams,
    per_user: dict[int, ZScoreParams],
    mode: str,
) -> np.ndarray:
    if mode != "per_user" or not per_user:
        return global_params.transform(feats).astype(np.float32)
    out = np.empty_like(feats, dtype=np.float32)
    for i in range(len(feats)):
        params = per_user.get(int(groups[i]), global_params)
        out[i] = params.transform(feats[i])
    return out


# ─── Training ────────────────────────────────────────────────────────────────


def _make_loader(X_raw, feats, y, sev, batch_size, shuffle):
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    ds = TensorDataset(
        torch.from_numpy(X_raw).float(),
        torch.from_numpy(feats).float(),
        torch.from_numpy(y).float(),
        torch.from_numpy(sev).float(),
    )
    return DataLoader(ds, batch_size=batch_size, shuffle=shuffle)


def _infer(model, loader, device):
    """Return (logits, severity_std, y) as numpy over the whole loader."""
    import torch

    model.eval()
    logits, sevs, ys = [], [], []
    with torch.no_grad():
        for xb, fb, yb, _sb in loader:
            out = model(xb.to(device), fb.to(device))
            logits.append(np.atleast_1d(out.fall_logit.cpu().numpy()))
            sevs.append(np.atleast_1d(out.severity.cpu().numpy()))
            ys.append(yb.numpy())
    return np.concatenate(logits), np.concatenate(sevs), np.concatenate(ys)


def _train_loop(model, loaders, cfg: TrainConfig, pos_weight: float, device, val_is_adl):
    import torch
    from torch import nn

    opt = torch.optim.AdamW(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
    bce = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([pos_weight], device=device))
    mse = nn.MSELoss()
    train_loader, val_loader = loaders

    best_score = (-1.0, -1e9)
    best_state = None
    history: list[dict] = []

    for epoch in range(cfg.epochs):
        model.train()
        epoch_loss = 0.0
        for xb, fb, yb, sb in train_loader:
            xb, fb, yb, sb = xb.to(device), fb.to(device), yb.to(device), sb.to(device)
            opt.zero_grad()
            out = model(xb, fb)
            loss = bce(out.fall_logit, yb) + cfg.severity_loss_weight * mse(out.severity, sb)
            loss.backward()
            opt.step()
            epoch_loss += loss.item() * xb.size(0)
        epoch_loss /= max(len(train_loader.dataset), 1)

        # Recall-first checkpoint selection (identical objective to the edge):
        # prefer epochs that MEET the recall floor on val at the lowest FPR;
        # if none do yet, prefer the highest recall.
        val_logits, _val_sev, val_y = _infer(model, val_loader, device)
        val_probs = 1.0 / (1.0 + np.exp(-val_logits))
        op = pick_threshold_for_recall(val_y, val_probs, val_is_adl, cfg.target_recall)
        meets = op.recall >= cfg.target_recall
        score = (1.0, -op.fpr_adl) if meets else (0.0, op.recall)

        history.append(
            {"epoch": epoch, "train_loss": epoch_loss,
             "val_recall": op.recall, "val_fpr_adl": op.fpr_adl, "val_threshold": op.threshold}
        )
        if score > best_score:
            best_score = score
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)
    return model, history


# ─── Severity + calibration helpers ──────────────────────────────────────────


def _severity_enum_from_peak(is_fall: bool, peak_ms2: float) -> str:
    """Map a (predicted) peak |a| to the API Severity enum (matches the stub cuts)."""
    if not is_fall:
        return "none"
    if peak_ms2 >= SEVERITY_HIGH_MS2:
        return "high"
    if peak_ms2 >= SEVERITY_MEDIUM_MS2:
        return "medium"
    return "low"


def _brier(probs: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((probs - y) ** 2))


def _fit_platt(val_logits: np.ndarray, val_y: np.ndarray):
    """Platt scaling: 1-D logistic fit on val logits. None if val is single-class."""
    if len(np.unique(val_y)) < 2:
        return None
    from sklearn.linear_model import LogisticRegression

    lr = LogisticRegression()
    lr.fit(val_logits.reshape(-1, 1), val_y.astype(int))
    return {"coef": float(lr.coef_[0][0]), "intercept": float(lr.intercept_[0])}


def _apply_platt(logits: np.ndarray, platt: dict | None) -> np.ndarray:
    if platt is None:
        return 1.0 / (1.0 + np.exp(-logits))
    z = platt["coef"] * logits + platt["intercept"]
    return 1.0 / (1.0 + np.exp(-z))


# ─── Orchestration ───────────────────────────────────────────────────────────


def run_training(
    cfg: TrainConfig,
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    artifact_dir: Path = DEFAULT_ARTIFACT_DIR,
) -> dict:
    """Execute one full cloud-training run and return a results dict.

    Logs params, metrics and artifacts to MLflow under "fall-guardian/cloud".
    """
    import mlflow
    import torch

    _seed_everything(cfg.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # 1. Assemble the dataset.
    bundle = make_synthetic_cloud_bundle(seed=cfg.seed) if cfg.synthetic \
        else build_cloud_bundle(dataset_root)
    print(f"[data] {bundle.summary()}")

    # 2. Subject-stratified split.
    split = subject_split(bundle, cfg)
    if len(split.train_idx) == 0 or len(split.test_idx) == 0:
        raise RuntimeError("Empty train or test split — too few subjects to partition.")

    # 3. Normalisers (fit on TRAIN only for the global parts; per-user is subject-local).
    chan_stats = fit_channel_stats(bundle.X_raw[split.train_idx])     # raw 6-ch
    Xn = chan_stats.apply(bundle.X_raw)
    feat_global, feat_per_user = _fit_feature_norm(bundle, split.train_idx, cfg.feature_norm)
    Fn = _apply_feature_norm(bundle.feats, bundle.groups, feat_global, feat_per_user, cfg.feature_norm)
    # Standardize the severity target (peak |a|) on TRAIN; un-standardize at serve.
    sev_train = bundle.severity[split.train_idx]
    sev_mean, sev_std = float(sev_train.mean()), float(sev_train.std() or 1.0)
    sev_z = ((bundle.severity - sev_mean) / sev_std).astype(np.float32)

    # 4. Build the model, sized to the actual channel + feature counts.
    n_channels = int(bundle.X_raw.shape[-1])
    n_features = int(bundle.feats.shape[-1])
    model_cfg = replace(cfg.model, n_channels=n_channels, n_features=n_features)
    model = build_model(model_cfg).to(device)
    n_params = count_parameters(model)
    print(f"[model] TransformerDetector - {n_params} params, {n_channels}ch + {n_features}feat")

    mlflow.set_experiment(EXPERIMENT_NAME)
    with mlflow.start_run(run_name="transformer-detector-baseline") as run:
        mlflow.log_params(cfg.flat_params())
        mlflow.log_params(
            {
                "data_source": bundle.meta.get("source"),
                "n_windows": len(bundle),
                "n_positive": bundle.n_positive,
                "n_channels": n_channels,
                "n_features": n_features,
                "n_params": n_params,
                "test_subjects": split.test_subjects,
                "val_subjects": split.val_subjects,
                "severity_mean": round(sev_mean, 3),
                "severity_std": round(sev_std, 3),
                "device": str(device),
            }
        )

        # 5. Train (BCE detection + weighted MSE severity).
        train_loader = _make_loader(
            Xn[split.train_idx], Fn[split.train_idx], bundle.y[split.train_idx],
            sev_z[split.train_idx], cfg.batch_size, shuffle=True,
        )
        val_loader = _make_loader(
            Xn[split.val_idx], Fn[split.val_idx], bundle.y[split.val_idx],
            sev_z[split.val_idx], cfg.batch_size, shuffle=False,
        )
        y_train = bundle.y[split.train_idx]
        pos_weight = float(len(y_train) - int(y_train.sum())) / float(max(int(y_train.sum()), 1))
        mlflow.log_params({"pos_weight": round(pos_weight, 3)})
        print(f"[loss] pos_weight = {pos_weight:.2f}")
        model, history = _train_loop(
            model, (train_loader, val_loader), cfg, pos_weight, device,
            bundle.is_adl[split.val_idx],
        )
        for h in history:
            mlflow.log_metrics(
                {"train_loss": h["train_loss"], "val_recall": h["val_recall"],
                 "val_fpr_adl": h["val_fpr_adl"]},
                step=h["epoch"],
            )

        # 6. Pick the operating threshold on VAL (recall floor at lowest FPR).
        val_logits, _vs, val_y = _infer(model, val_loader, device)
        val_probs = 1.0 / (1.0 + np.exp(-val_logits))
        val_op = pick_threshold_for_recall(
            val_y, val_probs, bundle.is_adl[split.val_idx], cfg.target_recall
        )
        threshold = val_op.threshold
        print(f"[threshold] picked {threshold:.4f} on val "
              f"(recall {val_op.recall:.3f} @ FPR-ADL {val_op.fpr_adl:.3f}, floor {cfg.target_recall})")

        # 7. Calibrate (Platt on val) so the served `confidence` is trustworthy.
        platt = _fit_platt(val_logits, val_y)
        brier_raw = _brier(val_probs, val_y)
        brier_cal = _brier(_apply_platt(val_logits, platt), val_y)

        # 8. Evaluate on the held-out TEST subjects at that threshold.
        test_loader = _make_loader(
            Xn[split.test_idx], Fn[split.test_idx], bundle.y[split.test_idx],
            sev_z[split.test_idx], cfg.batch_size, shuffle=False,
        )
        test_logits, test_sev_z, test_y = _infer(model, test_loader, device)
        test_probs = _apply_platt(test_logits, platt)
        test_metrics = compute_metrics(test_y, test_probs, bundle.is_adl[split.test_idx], threshold)
        # Severity MAE in m/s² (un-standardize predictions + targets).
        sev_pred_ms2 = test_sev_z * sev_std + sev_mean
        sev_true_ms2 = bundle.severity[split.test_idx]
        severity_mae = float(np.mean(np.abs(sev_pred_ms2 - sev_true_ms2)))

        mlflow.log_metrics(test_metrics.as_flat_dict(prefix="test_"))
        mlflow.log_metrics(
            {
                "severity_mae_ms2": severity_mae,
                "val_brier_raw": brier_raw,
                "val_brier_calibrated": brier_cal,
                "meets_recall_target": float(test_metrics.recall >= TARGET_RECALL),
                "meets_fpr_adl_target": float(test_metrics.fpr_adl <= TARGET_FPR_ADL),
            }
        )

        # 9. Persist artifacts: FP32 checkpoint (+ everything the backend needs to
        #    reproduce serving), normalisers, threshold, calibrator, severity scaler.
        ckpt_path = artifact_dir / "transformer_detector_fp32.pt"
        torch.save(
            {
                "state_dict": model.state_dict(),
                "model_config": asdict(model_cfg),
                "threshold": threshold,
                "platt": platt,
                "severity_scaler": {"mean": sev_mean, "std": sev_std},
                "severity_cuts_ms2": {"medium": SEVERITY_MEDIUM_MS2, "high": SEVERITY_HIGH_MS2},
                "channel_stats": {"mean": chan_stats.mean.tolist(), "std": chan_stats.std.tolist()},
                "feature_norm_global": {"mean": feat_global.mean.tolist(), "std": feat_global.std.tolist()},
                "feature_norm_mode": cfg.feature_norm,
                "model_version": MODEL_VERSION + ("-synthetic" if cfg.synthetic else ""),
            },
            ckpt_path,
        )
        (artifact_dir / "channel_stats.json").write_text(
            json.dumps({"mean": chan_stats.mean.tolist(), "std": chan_stats.std.tolist()}, indent=2)
        )
        (artifact_dir / "feature_norm.json").write_text(
            json.dumps({"mode": cfg.feature_norm,
                        "global": {"mean": feat_global.mean.tolist(), "std": feat_global.std.tolist()}},
                       indent=2)
        )

        # 10. A sample inference mapped to the API contract — the cross-package
        #     compatibility check validates this against backend InferenceResponse.
        sample = _sample_inference_payload(
            model, Xn, Fn, bundle, split, threshold, platt, sev_mean, sev_std, cfg, device
        )
        (artifact_dir / "sample_inference.json").write_text(json.dumps(sample, indent=2))

        results = {
            "run_id": run.info.run_id,
            "data_source": bundle.meta.get("source"),
            "n_params": n_params,
            "threshold": threshold,
            "test": test_metrics.as_flat_dict(),
            "severity_mae_ms2": severity_mae,
            "calibration": {"val_brier_raw": brier_raw, "val_brier_calibrated": brier_cal},
            "targets": {"recall": TARGET_RECALL, "fpr_adl": TARGET_FPR_ADL},
            "sample_inference": sample,
            "checkpoint": str(ckpt_path),
        }
        (artifact_dir / "cloud_results.json").write_text(json.dumps(results, indent=2, default=str))
        for name in ("transformer_detector_fp32.pt", "channel_stats.json",
                     "feature_norm.json", "sample_inference.json", "cloud_results.json"):
            mlflow.log_artifact(str(artifact_dir / name))

        _print_report(test_metrics, severity_mae, n_params, cfg, bundle.meta.get("source"), sample)
        return results


def _sample_inference_payload(
    model, Xn, Fn, bundle, split, threshold, platt, sev_mean, sev_std, cfg, device
) -> dict:
    """Run the model on one window and shape the output like the API InferenceResponse."""
    import torch

    # Prefer a positive test window so the payload exercises the fall path.
    pool = split.test_idx if len(split.test_idx) else split.val_idx
    pos = [i for i in pool if bundle.y[i] == 1]
    idx = pos[0] if pos else int(pool[0])

    model.eval()
    with torch.no_grad():
        out = model(
            torch.from_numpy(Xn[idx : idx + 1]).float().to(device),
            torch.from_numpy(Fn[idx : idx + 1]).float().to(device),
        )
    prob = float(_apply_platt(out.fall_logit.cpu().numpy(), platt)[0])
    peak = float(out.severity.cpu().numpy()[0] * sev_std + sev_mean)
    is_fall = bool(prob >= threshold)
    return {
        "is_fall": is_fall,
        "confidence": max(0.0, min(1.0, prob)),
        "severity": _severity_enum_from_peak(is_fall, peak),
        "action": "alert_caregiver" if is_fall else "suppress",
        "lead_time_ms": None,
        "model_version": MODEL_VERSION + ("-synthetic" if cfg.synthetic else ""),
    }


def _print_report(test_metrics, severity_mae, n_params, cfg, source, sample) -> None:
    tick = lambda ok: "[PASS]" if ok else "[FAIL]"  # noqa: E731
    print("\n" + "=" * 60)
    print(f"  CLOUD DETECTOR - Transformer  (data: {source})")
    print("=" * 60)
    print(f"  Recall      : {test_metrics.recall:6.3f}   target >={TARGET_RECALL:.2f} "
          f"{tick(test_metrics.recall >= TARGET_RECALL)}")
    print(f"  FPR on ADL  : {test_metrics.fpr_adl:6.3f}   target <={TARGET_FPR_ADL:.2f} "
          f"{tick(test_metrics.fpr_adl <= TARGET_FPR_ADL)}")
    print(f"  Precision   : {test_metrics.precision:6.3f}")
    print(f"  F1          : {test_metrics.f1:6.3f}")
    print(f"  Severity MAE: {severity_mae:6.2f} m/s²")
    print(f"  Confusion   : TP={test_metrics.tp} FP={test_metrics.fp} "
          f"TN={test_metrics.tn} FN={test_metrics.fn}")
    print(f"  Params      : {n_params}")
    print(f"  Sample API  : {sample}")
    print("=" * 60)
    if source == "SYNTHETIC":
        print("  ! SYNTHETIC DATA - pipeline-smoke numbers, NOT WEDA-FALL.")
    print()


def main(argv: list[str] | None = None) -> None:
    import argparse

    p = argparse.ArgumentParser(description="Train the Transformer cloud detector.")
    p.add_argument("--synthetic", action="store_true", help="use synthetic smoke-test data")
    p.add_argument("--epochs", type=int, default=TrainConfig.epochs)
    p.add_argument("--batch-size", type=int, default=TrainConfig.batch_size)
    p.add_argument("--lr", type=float, default=TrainConfig.lr)
    p.add_argument("--seed", type=int, default=TrainConfig.seed)
    p.add_argument("--target-recall", type=float, default=TrainConfig.target_recall,
                   help="HARD recall floor; threshold meets it at the lowest FPR")
    p.add_argument("--feature-norm", choices=["per_user", "global"],
                   default=TrainConfig.feature_norm)
    p.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    args = p.parse_args(argv)

    cfg = TrainConfig(
        epochs=args.epochs, batch_size=args.batch_size, lr=args.lr, seed=args.seed,
        target_recall=args.target_recall, feature_norm=args.feature_norm,
        synthetic=args.synthetic,
    )
    run_training(cfg, dataset_root=args.dataset_root)


if __name__ == "__main__":
    main()
