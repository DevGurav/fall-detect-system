"""Phase 30: 5-fold subject-stratified CV with SmartFall hard ADL negatives.

A training WRAPPER around the `train_cloud` machinery. What it adds over
`train_cloud.run_cv_training`:

  1. SmartFall watch ADL windows merged in as HARD NEGATIVES — the impact-like
     wrist movements behind the 5% held-out FPR (BUILD_LOG Phase 20). SmartFall
     subjects participate in the subject k-fold like any other subject (ids are
     offset, so folds never leak a subject across train/holdout).
  2. A FOLD-AVERAGED PR threshold: each fold's holdout gets its own
     recall-floor threshold on the (pooled-OOF) Platt-calibrated scale; the
     served threshold is the mean across folds. The pooled-OOF threshold is
     also computed and reported — both, plus per-fold metrics, are persisted to
     `cv_threshold_meta.json` and embedded in `cloud_detector.meta.json`.
  3. ONNX export straight into the backend (`backend/app/model/`), so a
     successful run leaves the gateway serving the new model.

Every path is env-overridable for Colab ("write now, run later" — develop on
Windows, train where the GPU is):

    FG_WEDA_ROOT           extracted WEDA-FALL-main directory
    FG_SMARTFALL_ROOT      extracted SmartFallMM-Dataset-main directory
    FG_CLOUD_ARTIFACT_DIR  where checkpoints/metadata land (default ml/artifacts/cloud)
    FG_BACKEND_MODEL_DIR   where the ONNX ships (default backend/app/model)
    FG_MLFLOW=0            disable MLflow logging (Colab has no tracking store)

Colab usage (after `pip install -e ml/` and mounting/extracting the datasets):

    %env FG_WEDA_ROOT=/content/data/WEDA-FALL-main
    %env FG_SMARTFALL_ROOT=/content/data/SmartFallMM-Dataset-main
    %env FG_MLFLOW=0
    !python -m fall_guardian_ml.training.cross_validate --epochs 40

Local (from ml/):

    python -m fall_guardian_ml.training.cross_validate
    python -m fall_guardian_ml.training.cross_validate --no-smartfall   # WEDA-only baseline
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np

from fall_guardian_ml.datasets.cloud_dataset import CloudBundle, build_cloud_bundle
from fall_guardian_ml.datasets.smartfall_adl import build_smartfall_adl_bundle
from fall_guardian_ml.eval.metrics import compute_metrics, pick_threshold_for_recall
from fall_guardian_ml.training import train_cloud as tc
from fall_guardian_ml.training.train_cloud import TrainConfig

# Phase 30 retrain gets its own version so a served response is attributable.
MODEL_VERSION = "cloud-transformer-v0.2"

ML_ROOT = Path(__file__).resolve().parents[3]


def _env_path(var: str, default: Path) -> Path:
    """Env-var path override with a repo-relative default (Colab seam)."""
    return Path(os.environ.get(var) or default)


DEFAULT_WEDA_ROOT = _env_path("FG_WEDA_ROOT", ML_ROOT / "data" / "raw" / "WEDA-FALL-main")
DEFAULT_SMARTFALL_ROOT = _env_path(
    "FG_SMARTFALL_ROOT", ML_ROOT / "data" / "raw" / "SmartFallMM-Dataset-main")
DEFAULT_ARTIFACT_DIR = _env_path("FG_CLOUD_ARTIFACT_DIR", ML_ROOT / "artifacts" / "cloud")
DEFAULT_BACKEND_MODEL_DIR = _env_path(
    "FG_BACKEND_MODEL_DIR", ML_ROOT.parent / "backend" / "app" / "model")


def _maybe_mlflow():
    """MLflow handle, or None when disabled/unavailable (Colab-friendly no-op)."""
    if os.environ.get("FG_MLFLOW", "1") == "0":
        return None
    try:
        import mlflow
        return mlflow
    except ImportError:
        return None


# ─── Bundle composition ──────────────────────────────────────────────────────


def merge_bundles(*bundles: CloudBundle) -> CloudBundle:
    """Concatenate window-schema-identical bundles into one training set.

    Subject ids must already be globally unique across bundles (SmartFall ids
    are offset by the loader) — asserted, because a collision would silently
    leak one dataset's subject into another's fold.
    """
    seen: set[int] = set()
    for b in bundles:
        subs = {int(s) for s in b.groups}
        clash = seen & subs
        assert not clash, f"subject-id collision across bundles: {sorted(clash)}"
        seen |= subs
    assert len({b.X_raw.shape[1:] for b in bundles}) == 1, "window shapes differ"
    assert len({b.feats.shape[1] for b in bundles}) == 1, "feature dims differ"

    return CloudBundle(
        X_raw=np.concatenate([b.X_raw for b in bundles]),
        feats=np.concatenate([b.feats for b in bundles]),
        y=np.concatenate([b.y for b in bundles]),
        groups=np.concatenate([b.groups for b in bundles]),
        is_adl=np.concatenate([b.is_adl for b in bundles]),
        severity=np.concatenate([b.severity for b in bundles]),
        phase=np.concatenate([b.phase for b in bundles]),
        movement=np.concatenate([b.movement.astype(str) for b in bundles]),
        meta={
            "source": "+".join(str(b.meta.get("source")) for b in bundles),
            "components": [b.meta for b in bundles],
        },
    )


def _fpr_by_source(bundle: CloudBundle, mask: np.ndarray, preds: np.ndarray) -> dict[str, float]:
    """ADL false-positive rate split by dataset (SmartFall movements are 'SF-*')."""
    movement = bundle.movement[mask].astype(str)
    is_adl = bundle.is_adl[mask]
    sf = np.char.startswith(movement, "SF-")
    out: dict[str, float] = {}
    for name, sel in (("WEDA-FALL", is_adl & ~sf), ("SmartFallMM", is_adl & sf)):
        n = int(sel.sum())
        out[name] = float(preds[sel].mean()) if n else float("nan")
    return out


# ─── ONNX export (backend serving artifact) ──────────────────────────────────


def export_onnx(ckpt_path: Path, out_dir: Path, cv_meta: dict) -> Path:
    """Export the checkpoint to backend/app/model/ (mirrors scripts/export_cloud_onnx.py)
    with the CV threshold provenance embedded in the serving meta sidecar."""
    import onnx
    import torch

    from fall_guardian_ml.models.transformer_detector import (
        TransformerDetectorConfig,
        build_model,
    )

    class _ExportWrap(torch.nn.Module):
        """ONNX needs tensor outputs, not the DetectorOutput dataclass."""

        def __init__(self, model: torch.nn.Module) -> None:
            super().__init__()
            self.model = model

        def forward(self, raw, feats):
            out = self.model(raw, feats)
            return out.fall_logit, out.severity

    ck = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    cfg = TransformerDetectorConfig(**ck["model_config"])
    model = build_model(cfg)
    model.load_state_dict(ck["state_dict"])
    model.eval()

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = out_dir / "cloud_detector.onnx"
    torch.onnx.export(
        _ExportWrap(model).eval(),
        (torch.randn(1, cfg.window_samples, cfg.n_channels), torch.randn(1, cfg.n_features)),
        str(onnx_path),
        input_names=["raw", "feats"], output_names=["fall_logit", "severity"],
        dynamic_axes={"raw": {0: "b"}, "feats": {0: "b"},
                      "fall_logit": {0: "b"}, "severity": {0: "b"}},
        opset_version=18,
    )
    # Consolidate any external-data sidecar into one self-contained .onnx.
    onnx.save_model(onnx.load(str(onnx_path)), str(onnx_path), save_as_external_data=False)
    sidecar = onnx_path.with_suffix(".onnx.data")
    if sidecar.exists():
        sidecar.unlink()

    meta = {
        "model_version": ck["model_version"],
        "threshold": float(ck["threshold"]),
        "platt": ck["platt"],
        "channel_stats": ck["channel_stats"],
        "feature_norm": ck["feature_norm_global"],
        "severity_scaler": ck["severity_scaler"],
        "severity_cuts_ms2": ck["severity_cuts_ms2"],
        "n_channels": cfg.n_channels,
        "n_features": cfg.n_features,
        "window_samples": cfg.window_samples,
        "cv": cv_meta,  # Phase 30 threshold provenance (backend ignores extra keys)
    }
    (out_dir / "cloud_detector.meta.json").write_text(json.dumps(meta, indent=2))
    print(f"[export] wrote {onnx_path} ({onnx_path.stat().st_size / 1024:.0f} KB)")
    print(f"[export] wrote {out_dir / 'cloud_detector.meta.json'} "
          f"(version {meta['model_version']}, threshold {meta['threshold']:.4f})")
    return onnx_path


# ─── Orchestration ───────────────────────────────────────────────────────────


def run_cross_validation(
    cfg: TrainConfig,
    weda_root: Path = DEFAULT_WEDA_ROOT,
    smartfall_root: Path = DEFAULT_SMARTFALL_ROOT,
    artifact_dir: Path = DEFAULT_ARTIFACT_DIR,
    backend_model_dir: Path = DEFAULT_BACKEND_MODEL_DIR,
    use_smartfall: bool = True,
    export: bool = True,
) -> dict:
    """5-fold subject CV → fold-averaged threshold → final model → ONNX export."""
    import torch

    tc._seed_everything(cfg.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    mlflow = _maybe_mlflow()

    # ── Assemble: WEDA-FALL (positives + ADL) ++ SmartFall (hard ADL negatives)
    weda = build_cloud_bundle(weda_root)
    print(f"[data] WEDA-FALL : {weda.summary()}")
    if use_smartfall:
        smartfall = build_smartfall_adl_bundle(smartfall_root)
        print(f"[data] SmartFall : {smartfall.summary()}")
        bundle = merge_bundles(weda, smartfall)
    else:
        bundle = weda
    print(f"[data] combined  : {bundle.summary()}")

    folds = tc._subject_kfold(bundle, cfg)
    rng = np.random.default_rng(cfg.seed + 1)

    # ── CV loop: every subject scored once, by a model that never saw it.
    oof_logits = np.full(len(bundle), np.nan, dtype=np.float64)
    oof_sev_ms2 = np.full(len(bundle), np.nan, dtype=np.float64)
    fold_hold_idx: list[np.ndarray] = []

    for k, holdout in enumerate(folds):
        trainpool = [s for j, f in enumerate(folds) if j != k for s in f]
        val_subs, train_subs = tc._stratified_carve(bundle, trainpool, cfg.val_fraction, rng)
        train_idx = tc._idx_for(bundle, train_subs)
        val_idx = tc._idx_for(bundle, val_subs)
        hold_idx = tc._idx_for(bundle, holdout)
        fold_hold_idx.append(hold_idx)

        norms = tc._fit_norms(bundle, train_idx, cfg)
        Xn, Fn, sev_z = tc._apply_norms(bundle, norms, cfg)
        model, _model_cfg, _hist = tc._build_and_train(
            bundle, Xn, Fn, sev_z, train_idx, val_idx, cfg, device)
        logits, sev_pred_z, _ = tc._infer(model, tc._make_loader(
            Xn[hold_idx], Fn[hold_idx], bundle.y[hold_idx], sev_z[hold_idx],
            cfg.batch_size, False), device)
        oof_logits[hold_idx] = logits
        oof_sev_ms2[hold_idx] = sev_pred_z * norms.sev_std + norms.sev_mean
        fm = compute_metrics(bundle.y[hold_idx], 1.0 / (1.0 + np.exp(-logits)),
                             bundle.is_adl[hold_idx], 0.5)
        print(f"[cv {k + 1}/{cfg.cv_folds}] holdout n_subjects={len(holdout)} "
              f"n={len(hold_idx)} pos={int(bundle.y[hold_idx].sum())} "
              f"recall@0.5={fm.recall:.3f} fpr_adl@0.5={fm.fpr_adl:.3f}")

    # ── Calibrate once on pooled OOF (leakage-free), then threshold per fold.
    m = ~np.isnan(oof_logits)
    platt = tc._fit_platt(oof_logits[m], bundle.y[m])

    fold_ops = []
    for hold_idx in fold_hold_idx:
        probs_k = tc._apply_platt(oof_logits[hold_idx], platt)
        fold_ops.append(pick_threshold_for_recall(
            bundle.y[hold_idx], probs_k, bundle.is_adl[hold_idx], cfg.target_recall))

    threshold_fold_avg = float(np.mean([op.threshold for op in fold_ops]))
    oof_probs = tc._apply_platt(oof_logits[m], platt)
    pooled_op = pick_threshold_for_recall(bundle.y[m], oof_probs, bundle.is_adl[m],
                                          cfg.target_recall)

    # The SERVED threshold is the fold-averaged one: each fold's recall-floor
    # operating point is an independent cross-subject estimate, so their mean is
    # more stable than one number read off a single pooled curve.
    metrics_avg = compute_metrics(bundle.y[m], oof_probs, bundle.is_adl[m], threshold_fold_avg)
    metrics_pooled = compute_metrics(bundle.y[m], oof_probs, bundle.is_adl[m], pooled_op.threshold)
    severity_mae = float(np.mean(np.abs(oof_sev_ms2[m] - bundle.severity[m])))
    fpr_by_source = _fpr_by_source(bundle, m, oof_probs >= threshold_fold_avg)

    tc._print_fp_breakdown(bundle.y[m], oof_probs >= threshold_fold_avg,
                           bundle.movement[m], bundle.groups[m])
    tc._save_predictions(artifact_dir / "cv_oof_predictions.npz", oof_probs, bundle.y[m],
                         bundle.is_adl[m], bundle.movement[m], bundle.groups[m],
                         oof_sev_ms2[m], bundle.severity[m], threshold_fold_avg)

    cv_meta = {
        "cv_folds": cfg.cv_folds,
        "target_recall": cfg.target_recall,
        "seed": cfg.seed,
        "datasets": str(bundle.meta.get("source")),
        "n_windows": int(len(bundle)),
        "n_positive": int(bundle.n_positive),
        "n_subjects": len({int(s) for s in bundle.groups}),
        "fold_thresholds": [float(op.threshold) for op in fold_ops],
        "fold_recalls": [float(op.recall) for op in fold_ops],
        "fold_fpr_adl": [float(op.fpr_adl) for op in fold_ops],
        "threshold_fold_averaged": threshold_fold_avg,
        "threshold_pooled_oof": float(pooled_op.threshold),
        "oof_metrics_at_fold_averaged": metrics_avg.as_flat_dict(),
        "oof_metrics_at_pooled": metrics_pooled.as_flat_dict(),
        "oof_fpr_adl_by_source": fpr_by_source,
        "oof_severity_mae_ms2": severity_mae,
    }
    (artifact_dir / "cv_threshold_meta.json").write_text(json.dumps(cv_meta, indent=2))

    # ── Final deployment model: trained on ALL subjects (small internal val carve),
    # served at the fold-averaged threshold picked above.
    all_subjects = sorted({int(s) for s in bundle.groups})
    val_subs, train_subs = tc._stratified_carve(bundle, all_subjects, cfg.val_fraction, rng)
    norms = tc._fit_norms(bundle, tc._idx_for(bundle, train_subs), cfg)
    Xn, Fn, sev_z = tc._apply_norms(bundle, norms, cfg)
    model, model_cfg, _history = tc._build_and_train(
        bundle, Xn, Fn, sev_z, tc._idx_for(bundle, train_subs),
        tc._idx_for(bundle, val_subs), cfg, device)
    n_params = tc.count_parameters(model)

    # _persist_checkpoint stamps tc.MODEL_VERSION into the checkpoint; swap in the
    # Phase 30 version for the call (restored after) rather than forking the helper.
    _orig_version = tc.MODEL_VERSION
    tc.MODEL_VERSION = MODEL_VERSION
    try:
        ckpt_path = tc._persist_checkpoint(
            artifact_dir, model, model_cfg, norms, threshold_fold_avg, platt, cfg)
    finally:
        tc.MODEL_VERSION = _orig_version

    if mlflow is not None:
        mlflow.set_experiment(tc.EXPERIMENT_NAME)
        with mlflow.start_run(run_name="transformer-detector-cv-smartfall"):
            mlflow.log_params(cfg.flat_params())
            mlflow.log_params({
                "mode": f"{cfg.cv_folds}-fold-cv+smartfall" if use_smartfall
                        else f"{cfg.cv_folds}-fold-cv",
                "data_source": bundle.meta.get("source"), "n_windows": len(bundle),
                "n_positive": bundle.n_positive, "n_params": n_params,
                "model_version": MODEL_VERSION, "device": str(device),
            })
            mlflow.log_metrics(metrics_avg.as_flat_dict(prefix="oof_"))
            mlflow.log_metrics({
                "threshold_fold_averaged": threshold_fold_avg,
                "threshold_pooled_oof": float(pooled_op.threshold),
                "oof_severity_mae_ms2": severity_mae,
                "oof_fpr_adl_weda": fpr_by_source.get("WEDA-FALL", float("nan")),
                "oof_fpr_adl_smartfall": fpr_by_source.get("SmartFallMM", float("nan")),
            })
            for name in ("cv_threshold_meta.json", "cv_oof_predictions.npz"):
                mlflow.log_artifact(str(artifact_dir / name))

    onnx_path = export_onnx(ckpt_path, backend_model_dir, cv_meta) if export else None

    # ── Report against the Phase 30 gates (recall ≥97%, FPR-on-ADL ≤2%).
    tick = lambda ok: "[PASS]" if ok else "[FAIL]"  # noqa: E731
    print("\n" + "=" * 64)
    print(f"  CLOUD DETECTOR — {cfg.cv_folds}-fold subject CV "
          f"({'WEDA + SmartFall negatives' if use_smartfall else 'WEDA only'})")
    print("=" * 64)
    print(f"  OOF recall        : {metrics_avg.recall:6.3f}   target >={tc.TARGET_RECALL:.2f} "
          f"{tick(metrics_avg.recall >= tc.TARGET_RECALL)}")
    print(f"  OOF FPR on ADL    : {metrics_avg.fpr_adl:6.3f}   target <={tc.TARGET_FPR_ADL:.2f} "
          f"{tick(metrics_avg.fpr_adl <= tc.TARGET_FPR_ADL)}")
    print(f"    by source       : " + "  ".join(
        f"{k}={v:.3f}" for k, v in fpr_by_source.items() if not np.isnan(v)))
    print(f"  Precision / F1    : {metrics_avg.precision:6.3f} / {metrics_avg.f1:.3f}")
    print(f"  Severity MAE      : {severity_mae:6.2f} m/s^2")
    print(f"  Threshold (served): {threshold_fold_avg:.4f}  fold-averaged over "
          f"{[round(op.threshold, 4) for op in fold_ops]}")
    print(f"  Threshold (pooled): {pooled_op.threshold:.4f}  "
          f"(recall {metrics_pooled.recall:.3f}, fpr_adl {metrics_pooled.fpr_adl:.3f})")
    print(f"  Params            : {n_params}   version {MODEL_VERSION}")
    print("=" * 64 + "\n")

    return {
        "cv_meta": cv_meta,
        "checkpoint": str(ckpt_path),
        "onnx": str(onnx_path) if onnx_path else None,
        "model_version": MODEL_VERSION,
    }


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Phase 30: 5-fold subject CV with SmartFall hard ADL negatives.")
    p.add_argument("--epochs", type=int, default=TrainConfig.epochs)
    p.add_argument("--batch-size", type=int, default=TrainConfig.batch_size)
    p.add_argument("--lr", type=float, default=TrainConfig.lr)
    p.add_argument("--seed", type=int, default=TrainConfig.seed)
    p.add_argument("--cv-folds", type=int, default=TrainConfig.cv_folds)
    p.add_argument("--target-recall", type=float, default=TrainConfig.target_recall)
    p.add_argument("--loss", choices=["focal", "bce"], default=TrainConfig.loss)
    p.add_argument("--feature-norm", choices=["per_user", "global"],
                   default=TrainConfig.feature_norm)
    p.add_argument("--no-smartfall", action="store_true",
                   help="WEDA-only baseline (isolate the SmartFall-negatives effect)")
    p.add_argument("--no-export", action="store_true",
                   help="skip the backend ONNX export (metrics-only run)")
    p.add_argument("--weda-root", type=Path, default=DEFAULT_WEDA_ROOT)
    p.add_argument("--smartfall-root", type=Path, default=DEFAULT_SMARTFALL_ROOT)
    p.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    p.add_argument("--backend-model-dir", type=Path, default=DEFAULT_BACKEND_MODEL_DIR)
    args = p.parse_args(argv)

    cfg = TrainConfig(
        epochs=args.epochs, batch_size=args.batch_size, lr=args.lr, seed=args.seed,
        cv_folds=args.cv_folds, target_recall=args.target_recall, loss=args.loss,
        feature_norm=args.feature_norm,
    )
    if cfg.cv_folds < 2:
        raise SystemExit("--cv-folds must be >= 2 (this is the k-fold CV wrapper)")
    run_cross_validation(
        cfg,
        weda_root=args.weda_root,
        smartfall_root=args.smartfall_root,
        artifact_dir=args.artifact_dir,
        backend_model_dir=args.backend_model_dir,
        use_smartfall=not args.no_smartfall,
        export=not args.no_export,
    )


if __name__ == "__main__":
    main()
