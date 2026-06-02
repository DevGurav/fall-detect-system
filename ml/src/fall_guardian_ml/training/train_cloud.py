"""Train the Transformer cloud detector with MLflow tracking.

Week-C precision gate. Mirrors the Week-B edge pipeline (train_edge.py) so the
two share one mental model:

    assemble windows -> subject-stratified split (or subject k-fold CV)
    -> standardize raw + per-user z-score features -> train Transformer
    (focal/BCE detection + MSE severity) -> calibrate (Platt) -> pick the
    threshold for the recall floor on the *calibrated* scale -> evaluate
    (recall, FPR-on-ADL, severity MAE) -> log to MLflow -> save the FP32
    checkpoint + normalisers + threshold + a sample API payload.

Two evaluation modes (`cfg.cv_folds`):
  • cv_folds <= 1 — single subject-stratified train/val/test split (fast; used by
    the synthetic smoke).
  • cv_folds >= 2 — subject k-fold CV: every subject is held out exactly once and
    scored by a model that never saw it. The robust threshold is picked on the
    pooled out-of-fold (OOF) predictions; a final deployment model is then trained
    on all subjects. This sidesteps the high-variance tiny-test-split estimate that
    the first single-split baseline suffered (BUILD_LOG Phase 18).

The cloud is the PRECISION gate: keep recall high (don't drop a real fall the edge
caught) while suppressing the edge's ~20% ADL false positives.

Run it:
    python -m fall_guardian_ml.training.train_cloud                 # real WEDA-FALL, k-fold CV
    python -m fall_guardian_ml.training.train_cloud --synthetic --cv-folds 0   # quick smoke
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
# Severity -> enum cut-points (m/s²), matching the backend stub so train == serve.
SEVERITY_MEDIUM_MS2 = 20.0
SEVERITY_HIGH_MS2 = 30.0


@dataclass
class TrainConfig:
    """All knobs for one cloud training run — logged verbatim to MLflow params."""

    epochs: int = 40
    batch_size: int = 128
    lr: float = 1e-3
    weight_decay: float = 1e-4
    test_fraction: float = 0.2          # fraction of SUBJECTS held out for test (single-split mode)
    val_fraction: float = 0.15          # fraction of TRAIN subjects used for val
    seed: int = 42
    # Recall-first operating point (same rationale as the edge): a missed fall is
    # the fatal error, so guarantee recall >= this floor and take the lowest FPR
    # among thresholds that meet it.
    target_recall: float = TARGET_RECALL
    # Weight on the severity (MSE) head relative to detection. Small: the detection
    # logit is the gate; severity is a secondary, standardized regression.
    severity_loss_weight: float = 0.2
    # Imbalance-aware detection loss. "focal" (default) down-weights easy negatives
    # — good for the ~6% positive rate; "bce" uses pos_weight (auto neg/pos) x scale.
    loss: str = "focal"                 # "focal" | "bce"
    focal_alpha: float = 0.75           # weight on the rare positive class
    focal_gamma: float = 2.0
    pos_weight_scale: float = 1.0       # extra recall bias on BCE pos_weight (bce loss only)
    # Subject k-fold CV (>=2) for a robust threshold + a leakage-free estimate over
    # ALL subjects; <=1 uses a single train/val/test split.
    cv_folds: int = 5
    # Per-user z-score on engineered features (locked pipeline step + personalization)
    # vs a single global normaliser. "per_user" fits each subject's own ADL windows.
    feature_norm: str = "per_user"      # "per_user" | "global"
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


# ─── Subject partitioning ────────────────────────────────────────────────────


@dataclass
class Split:
    train_idx: np.ndarray
    val_idx: np.ndarray
    test_idx: np.ndarray
    test_subjects: list[int]
    val_subjects: list[int]


def _idx_for(bundle: CloudBundle, subjects) -> np.ndarray:
    """Window indices belonging to the given subject ids."""
    want = np.array(sorted({int(s) for s in subjects}), dtype=np.int64)
    return np.flatnonzero(np.isin(bundle.groups, want))


def _stratified_carve(bundle, subjects, frac, rng):
    """Carve `frac` of `subjects` out, keeping fall-subjects in BOTH parts.

    Returns (carved, rest). Used for val carves so the carved set has positives
    to measure recall on.
    """
    pos = {int(s) for s in bundle.groups[bundle.y == 1]}
    p = [int(s) for s in subjects if int(s) in pos]
    n = [int(s) for s in subjects if int(s) not in pos]
    rng.shuffle(p)
    rng.shuffle(n)

    def take(lst):
        k = max(1, int(round(len(lst) * frac))) if lst else 0
        return lst[:k], lst[k:]

    cp, rp = take(p)
    cn, rn = take(n)
    return sorted(cp + cn), sorted(rp + rn)


def subject_split(bundle: CloudBundle, cfg: TrainConfig) -> Split:
    """Single subject-stratified train/val/test split (no subject in two splits)."""
    rng = np.random.default_rng(cfg.seed)
    subjects = sorted({int(s) for s in bundle.groups})
    test_subjects, rest = _stratified_carve(bundle, subjects, cfg.test_fraction, rng)
    val_subjects, train_subjects = _stratified_carve(bundle, rest, cfg.val_fraction, rng)
    return Split(
        train_idx=_idx_for(bundle, train_subjects),
        val_idx=_idx_for(bundle, val_subjects),
        test_idx=_idx_for(bundle, test_subjects),
        test_subjects=test_subjects,
        val_subjects=val_subjects,
    )


def _subject_kfold(bundle: CloudBundle, cfg: TrainConfig) -> list[list[int]]:
    """Partition subjects into `cv_folds` folds, spreading fall-subjects evenly.

    Round-robin assignment (pos subjects first, then neg-only) so each fold gets a
    share of the scarce young fall-subjects — every fold can measure recall.
    """
    rng = np.random.default_rng(cfg.seed)
    subjects = sorted({int(s) for s in bundle.groups})
    pos = {int(s) for s in bundle.groups[bundle.y == 1]}
    p = [s for s in subjects if s in pos]
    n = [s for s in subjects if s not in pos]
    rng.shuffle(p)
    rng.shuffle(n)
    folds: list[list[int]] = [[] for _ in range(cfg.cv_folds)]
    for i, s in enumerate(p):
        folds[i % cfg.cv_folds].append(s)
    for i, s in enumerate(n):
        folds[i % cfg.cv_folds].append(s)
    return [sorted(f) for f in folds]


# ─── Normalisation (raw channels + per-user feature z-score + severity) ──────


@dataclass
class _Norms:
    chan: object                 # ChannelStats (raw 6-ch standardiser)
    feat_global: ZScoreParams    # global feature fallback
    feat_per_user: dict          # subject -> ZScoreParams
    sev_mean: float
    sev_std: float


def _fit_feature_norm(
    bundle: CloudBundle, train_idx: np.ndarray, mode: str
) -> tuple[ZScoreParams, dict[int, ZScoreParams]]:
    """Fit the engineered-feature normaliser.

    Global fallback fit on TRAIN ADL windows (leak-free). For "per_user", each
    subject also gets a normaliser fit on *its own ADL windows* — unsupervised and
    subject-local, exactly the ~10–15 min ADL calibration the watch does at pairing,
    so fitting it for held-out subjects is not label leakage.
    """
    mask = np.zeros(len(bundle), dtype=bool)
    mask[train_idx] = True
    train_adl = bundle.is_adl & mask
    global_params = fit_zscore(bundle.feats[train_adl]) if train_adl.any() \
        else fit_zscore(bundle.feats[train_idx])

    per_user: dict[int, ZScoreParams] = {}
    if mode == "per_user":
        for s in sorted({int(g) for g in bundle.groups}):
            sub_adl = (bundle.groups == s) & bundle.is_adl
            per_user[s] = fit_zscore(bundle.feats[sub_adl]) if sub_adl.any() else global_params
    return global_params, per_user


def _apply_feature_norm(feats, groups, global_params, per_user, mode) -> np.ndarray:
    if mode != "per_user" or not per_user:
        return global_params.transform(feats).astype(np.float32)
    out = np.empty_like(feats, dtype=np.float32)
    for i in range(len(feats)):
        out[i] = per_user.get(int(groups[i]), global_params).transform(feats[i])
    return out


def _fit_norms(bundle: CloudBundle, train_idx: np.ndarray, cfg: TrainConfig) -> _Norms:
    chan = fit_channel_stats(bundle.X_raw[train_idx])
    fg, fpu = _fit_feature_norm(bundle, train_idx, cfg.feature_norm)
    sev_t = bundle.severity[train_idx]
    return _Norms(chan, fg, fpu, float(sev_t.mean()), float(sev_t.std() or 1.0))


def _apply_norms(bundle: CloudBundle, n: _Norms, cfg: TrainConfig):
    Xn = n.chan.apply(bundle.X_raw)
    Fn = _apply_feature_norm(bundle.feats, bundle.groups, n.feat_global, n.feat_per_user, cfg.feature_norm)
    sev_z = ((bundle.severity - n.sev_mean) / n.sev_std).astype(np.float32)
    return Xn, Fn, sev_z


# ─── Training core ───────────────────────────────────────────────────────────


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


def _focal_loss(logits, targets, alpha: float, gamma: float):
    """Binary focal loss (Lin et al. 2017) on raw logits — for the ~6% positive rate."""
    import torch
    from torch.nn import functional as F

    ce = F.binary_cross_entropy_with_logits(logits, targets, reduction="none")
    p = torch.sigmoid(logits)
    p_t = p * targets + (1.0 - p) * (1.0 - targets)
    loss = ce * (1.0 - p_t).pow(gamma)
    if alpha >= 0:
        a_t = alpha * targets + (1.0 - alpha) * (1.0 - targets)
        loss = a_t * loss
    return loss.mean()


def _train_loop(model, loaders, cfg: TrainConfig, pos_weight: float, device, val_is_adl):
    import torch
    from torch import nn

    opt = torch.optim.AdamW(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
    mse = nn.MSELoss()
    use_focal = cfg.loss == "focal"
    bce = None if use_focal else nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor([pos_weight], device=device)
    )
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
            det = (_focal_loss(out.fall_logit, yb, cfg.focal_alpha, cfg.focal_gamma)
                   if use_focal else bce(out.fall_logit, yb))
            loss = det + cfg.severity_loss_weight * mse(out.severity, sb)
            loss.backward()
            opt.step()
            epoch_loss += loss.item() * xb.size(0)
        epoch_loss /= max(len(train_loader.dataset), 1)

        # Recall-first checkpoint selection. The recall/FPR tradeoff is invariant to
        # the monotonic Platt calibration, so ranking epochs on uncalibrated val
        # probs is fine; the served threshold is set later on the calibrated scale.
        val_logits, _val_sev, val_y = _infer(model, val_loader, device)
        val_probs = 1.0 / (1.0 + np.exp(-val_logits))
        op = pick_threshold_for_recall(val_y, val_probs, val_is_adl, cfg.target_recall)
        meets = op.recall >= cfg.target_recall
        score = (1.0, -op.fpr_adl) if meets else (0.0, op.recall)

        history.append({"epoch": epoch, "train_loss": epoch_loss,
                        "val_recall": op.recall, "val_fpr_adl": op.fpr_adl})
        print(f"    epoch {epoch + 1:2d}/{cfg.epochs}: loss={epoch_loss:.4f} "
              f"val_recall={op.recall:.3f} val_fpr_adl={op.fpr_adl:.3f}", flush=True)
        if score > best_score:
            best_score = score
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)
    return model, history


def _build_and_train(bundle, Xn, Fn, sev_z, train_idx, val_idx, cfg, device):
    """Build a model sized to the data and train it on train_idx (val_idx for checkpointing)."""
    n_ch = int(bundle.X_raw.shape[-1])
    n_ft = int(bundle.feats.shape[-1])
    model_cfg = replace(cfg.model, n_channels=n_ch, n_features=n_ft)
    model = build_model(model_cfg).to(device)
    train_loader = _make_loader(Xn[train_idx], Fn[train_idx], bundle.y[train_idx],
                                sev_z[train_idx], cfg.batch_size, True)
    val_loader = _make_loader(Xn[val_idx], Fn[val_idx], bundle.y[val_idx],
                              sev_z[val_idx], cfg.batch_size, False)
    y_tr = bundle.y[train_idx]
    pos_weight = float(len(y_tr) - int(y_tr.sum())) / float(max(int(y_tr.sum()), 1)) * cfg.pos_weight_scale
    model, history = _train_loop(model, (train_loader, val_loader), cfg, pos_weight, device,
                                 bundle.is_adl[val_idx])
    return model, model_cfg, history


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


def _fit_platt(logits: np.ndarray, y: np.ndarray):
    """Platt scaling: 1-D logistic fit on logits. None if single-class."""
    if len(np.unique(y)) < 2:
        return None
    from sklearn.linear_model import LogisticRegression

    lr = LogisticRegression()
    lr.fit(logits.reshape(-1, 1), y.astype(int))
    return {"coef": float(lr.coef_[0][0]), "intercept": float(lr.intercept_[0])}


def _apply_platt(logits: np.ndarray, platt: dict | None) -> np.ndarray:
    if platt is None:
        return 1.0 / (1.0 + np.exp(-logits))
    z = platt["coef"] * logits + platt["intercept"]
    return 1.0 / (1.0 + np.exp(-z))


# ─── Persistence + reporting ─────────────────────────────────────────────────


def _persist_checkpoint(artifact_dir, model, model_cfg, norms: _Norms, threshold, platt, cfg):
    import torch

    ckpt_path = artifact_dir / "transformer_detector_fp32.pt"
    torch.save(
        {
            "state_dict": model.state_dict(),
            "model_config": asdict(model_cfg),
            "threshold": threshold,           # operates on Platt-calibrated probability
            "platt": platt,
            "severity_scaler": {"mean": norms.sev_mean, "std": norms.sev_std},
            "severity_cuts_ms2": {"medium": SEVERITY_MEDIUM_MS2, "high": SEVERITY_HIGH_MS2},
            "channel_stats": {"mean": norms.chan.mean.tolist(), "std": norms.chan.std.tolist()},
            "feature_norm_global": {"mean": norms.feat_global.mean.tolist(),
                                    "std": norms.feat_global.std.tolist()},
            "feature_norm_mode": cfg.feature_norm,
            "model_version": MODEL_VERSION + ("-synthetic" if cfg.synthetic else ""),
        },
        ckpt_path,
    )
    (artifact_dir / "channel_stats.json").write_text(
        json.dumps({"mean": norms.chan.mean.tolist(), "std": norms.chan.std.tolist()}, indent=2)
    )
    (artifact_dir / "feature_norm.json").write_text(
        json.dumps({"mode": cfg.feature_norm,
                    "global": {"mean": norms.feat_global.mean.tolist(),
                               "std": norms.feat_global.std.tolist()}}, indent=2)
    )
    return ckpt_path


def _sample_inference_payload(model, Xn, Fn, bundle, pool_idx, threshold, platt, norms, cfg, device) -> dict:
    """Run the model on one window and shape the output like the API InferenceResponse."""
    import torch

    pool = list(pool_idx)
    positives = [i for i in pool if bundle.y[i] == 1]
    idx = positives[0] if positives else pool[0]
    model.eval()
    with torch.no_grad():
        out = model(
            torch.from_numpy(Xn[idx: idx + 1]).float().to(device),
            torch.from_numpy(Fn[idx: idx + 1]).float().to(device),
        )
    prob = float(_apply_platt(out.fall_logit.cpu().numpy(), platt)[0])
    peak = float(out.severity.cpu().numpy()[0] * norms.sev_std + norms.sev_mean)
    is_fall = bool(prob >= threshold)
    return {
        "is_fall": is_fall,
        "confidence": max(0.0, min(1.0, prob)),
        "severity": _severity_enum_from_peak(is_fall, peak),
        "action": "alert_caregiver" if is_fall else "suppress",
        "lead_time_ms": None,
        "model_version": MODEL_VERSION + ("-synthetic" if cfg.synthetic else ""),
    }


def _print_report(title, metrics, severity_mae, n_params, cfg, source, sample, extra="") -> None:
    tick = lambda ok: "[PASS]" if ok else "[FAIL]"  # noqa: E731
    print("\n" + "=" * 64)
    print(f"  CLOUD DETECTOR - Transformer  ({title}; data: {source})")
    print("=" * 64)
    print(f"  Recall      : {metrics.recall:6.3f}   target >={TARGET_RECALL:.2f} "
          f"{tick(metrics.recall >= TARGET_RECALL)}")
    print(f"  FPR on ADL  : {metrics.fpr_adl:6.3f}   target <={TARGET_FPR_ADL:.2f} "
          f"{tick(metrics.fpr_adl <= TARGET_FPR_ADL)}")
    print(f"  Precision   : {metrics.precision:6.3f}")
    print(f"  F1          : {metrics.f1:6.3f}")
    print(f"  Severity MAE: {severity_mae:6.2f} m/s^2")
    print(f"  Confusion   : TP={metrics.tp} FP={metrics.fp} TN={metrics.tn} FN={metrics.fn}")
    print(f"  Params      : {n_params}")
    if extra:
        print(f"  {extra}")
    print(f"  Sample API  : {sample}")
    print("=" * 64)
    if source == "SYNTHETIC":
        print("  ! SYNTHETIC DATA - pipeline-smoke numbers, NOT WEDA-FALL.")
    print()


def _print_fp_breakdown(y, preds, movement, subject) -> None:
    """Where do the false positives come from? Break FP (among negatives) down by
    source movement code and by subject — directly targets the precision problem."""
    y = np.asarray(y).astype(bool)
    preds = np.asarray(preds).astype(bool)
    neg = ~y
    fp = preds & neg
    print(f"  [FP-by-movement]  {int(fp.sum())} FPs over {int(neg.sum())} negative windows:", flush=True)
    for mv in sorted({str(x) for x in movement}):
        sel = neg & (movement == mv)
        n = int(sel.sum())
        if n:
            f = int((fp & (movement == mv)).sum())
            print(f"      {mv:>5}: {f:5d} / {n:5d}  ({100 * f / n:5.1f}%)", flush=True)
    print("  [FP-by-subject]:", flush=True)
    for s in sorted({int(x) for x in subject[neg]}):
        sel = neg & (subject == s)
        n = int(sel.sum())
        f = int((fp & (subject == s)).sum())
        print(f"      U{s:02d}: {f:5d} / {n:5d}  ({100 * f / max(n, 1):5.1f}%)", flush=True)


def _save_predictions(path, probs, y, is_adl, movement, subject, sev_pred, sev_true, threshold):
    """Persist per-window predictions so FPs can be analysed without re-training."""
    np.savez(path, prob=probs, y=y, is_adl=is_adl, movement=np.asarray(movement).astype(str),
             subject=subject, sev_pred=sev_pred, sev_true=sev_true, threshold=np.array([threshold]))


# ─── Orchestration ───────────────────────────────────────────────────────────


def run_training(cfg: TrainConfig, dataset_root: Path = DEFAULT_DATASET_ROOT,
                 artifact_dir: Path = DEFAULT_ARTIFACT_DIR) -> dict:
    """Single subject-stratified train/val/test run. Logs to MLflow."""
    import mlflow
    import torch

    _seed_everything(cfg.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    bundle = make_synthetic_cloud_bundle(seed=cfg.seed) if cfg.synthetic else build_cloud_bundle(dataset_root)
    print(f"[data] {bundle.summary()}")
    split = subject_split(bundle, cfg)
    if len(split.train_idx) == 0 or len(split.test_idx) == 0:
        raise RuntimeError("Empty train or test split — too few subjects to partition.")

    norms = _fit_norms(bundle, split.train_idx, cfg)
    Xn, Fn, sev_z = _apply_norms(bundle, norms, cfg)

    mlflow.set_experiment(EXPERIMENT_NAME)
    with mlflow.start_run(run_name="transformer-detector-baseline") as run:
        model, model_cfg, history = _build_and_train(
            bundle, Xn, Fn, sev_z, split.train_idx, split.val_idx, cfg, device
        )
        n_params = count_parameters(model)
        print(f"[model] TransformerDetector - {n_params} params, loss={cfg.loss}")
        mlflow.log_params(cfg.flat_params())
        mlflow.log_params({
            "mode": "single-split", "data_source": bundle.meta.get("source"),
            "n_windows": len(bundle), "n_positive": bundle.n_positive, "n_params": n_params,
            "test_subjects": split.test_subjects, "val_subjects": split.val_subjects,
            "device": str(device),
        })
        for h in history:
            mlflow.log_metrics({"train_loss": h["train_loss"], "val_recall": h["val_recall"],
                                "val_fpr_adl": h["val_fpr_adl"]}, step=h["epoch"])

        # Calibrate on val, then pick the threshold on the CALIBRATED scale (so the
        # served decision uses the same probability the threshold was chosen on).
        val_logits, _vs, val_y = _infer(model, _make_loader(
            Xn[split.val_idx], Fn[split.val_idx], bundle.y[split.val_idx],
            sev_z[split.val_idx], cfg.batch_size, False), device)
        platt = _fit_platt(val_logits, val_y)
        val_probs = _apply_platt(val_logits, platt)
        threshold = pick_threshold_for_recall(
            val_y, val_probs, bundle.is_adl[split.val_idx], cfg.target_recall).threshold

        test_logits, test_sev_z, test_y = _infer(model, _make_loader(
            Xn[split.test_idx], Fn[split.test_idx], bundle.y[split.test_idx],
            sev_z[split.test_idx], cfg.batch_size, False), device)
        test_probs = _apply_platt(test_logits, platt)
        metrics = compute_metrics(test_y, test_probs, bundle.is_adl[split.test_idx], threshold)
        severity_mae = float(np.mean(np.abs(
            (test_sev_z * norms.sev_std + norms.sev_mean) - bundle.severity[split.test_idx])))
        brier_raw = _brier(1.0 / (1.0 + np.exp(-test_logits)), test_y)
        brier_cal = _brier(test_probs, test_y)

        # FP diagnosis: where do the false positives come from? Persist for re-analysis.
        _print_fp_breakdown(test_y, test_probs >= threshold,
                            bundle.movement[split.test_idx], bundle.groups[split.test_idx])
        _save_predictions(artifact_dir / "test_predictions.npz", test_probs, test_y,
                          bundle.is_adl[split.test_idx], bundle.movement[split.test_idx],
                          bundle.groups[split.test_idx],
                          test_sev_z * norms.sev_std + norms.sev_mean,
                          bundle.severity[split.test_idx], threshold)
        mlflow.log_artifact(str(artifact_dir / "test_predictions.npz"))

        return _finalize(run, "single-split", model, model_cfg, norms, threshold, platt, cfg,
                         artifact_dir, bundle, Xn, Fn, split.test_idx, metrics, severity_mae,
                         brier_raw, brier_cal, n_params, device, extra="")


def run_cv_training(cfg: TrainConfig, dataset_root: Path = DEFAULT_DATASET_ROOT,
                    artifact_dir: Path = DEFAULT_ARTIFACT_DIR) -> dict:
    """Subject k-fold CV: score every subject out-of-fold, then train a final model."""
    import mlflow
    import torch

    _seed_everything(cfg.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    bundle = make_synthetic_cloud_bundle(seed=cfg.seed) if cfg.synthetic else build_cloud_bundle(dataset_root)
    print(f"[data] {bundle.summary()}")
    folds = _subject_kfold(bundle, cfg)
    rng = np.random.default_rng(cfg.seed + 1)

    mlflow.set_experiment(EXPERIMENT_NAME)
    with mlflow.start_run(run_name="transformer-detector-cv") as run:
        oof_logits = np.full(len(bundle), np.nan, dtype=np.float64)
        oof_sev_ms2 = np.full(len(bundle), np.nan, dtype=np.float64)
        fold_recalls: list[float] = []

        for k, holdout in enumerate(folds):
            trainpool = [s for j, f in enumerate(folds) if j != k for s in f]
            val_subs, train_subs = _stratified_carve(bundle, trainpool, cfg.val_fraction, rng)
            train_idx, val_idx, hold_idx = (_idx_for(bundle, train_subs),
                                            _idx_for(bundle, val_subs),
                                            _idx_for(bundle, holdout))
            norms = _fit_norms(bundle, train_idx, cfg)
            Xn, Fn, sev_z = _apply_norms(bundle, norms, cfg)
            model, _model_cfg, _hist = _build_and_train(
                bundle, Xn, Fn, sev_z, train_idx, val_idx, cfg, device)
            logits, sev_pred_z, _ = _infer(model, _make_loader(
                Xn[hold_idx], Fn[hold_idx], bundle.y[hold_idx], sev_z[hold_idx],
                cfg.batch_size, False), device)
            oof_logits[hold_idx] = logits
            oof_sev_ms2[hold_idx] = sev_pred_z * norms.sev_std + norms.sev_mean
            fm = compute_metrics(bundle.y[hold_idx], 1.0 / (1.0 + np.exp(-logits)),
                                 bundle.is_adl[hold_idx], 0.5)
            fold_recalls.append(fm.recall)
            print(f"[cv {k + 1}/{cfg.cv_folds}] holdout={holdout} "
                  f"n={len(hold_idx)} pos={int(bundle.y[hold_idx].sum())} recall@0.5={fm.recall:.3f}")

        # Pool OOF predictions (every subject scored once by a model that didn't see it).
        m = ~np.isnan(oof_logits)
        oof_y, oof_adl = bundle.y[m], bundle.is_adl[m]
        platt = _fit_platt(oof_logits[m], oof_y)
        oof_probs = _apply_platt(oof_logits[m], platt)
        threshold = pick_threshold_for_recall(oof_y, oof_probs, oof_adl, cfg.target_recall).threshold
        metrics = compute_metrics(oof_y, oof_probs, oof_adl, threshold)
        severity_mae = float(np.mean(np.abs(oof_sev_ms2[m] - bundle.severity[m])))
        brier_raw = _brier(1.0 / (1.0 + np.exp(-oof_logits[m])), oof_y)
        brier_cal = _brier(oof_probs, oof_y)

        # FP diagnosis over the full OOF set (every subject scored once). Persisted
        # so the false positives can be analysed without re-running the 5 folds.
        _print_fp_breakdown(oof_y, oof_probs >= threshold, bundle.movement[m], bundle.groups[m])
        _save_predictions(artifact_dir / "oof_predictions.npz", oof_probs, oof_y, oof_adl,
                          bundle.movement[m], bundle.groups[m], oof_sev_ms2[m],
                          bundle.severity[m], threshold)
        mlflow.log_artifact(str(artifact_dir / "oof_predictions.npz"))

        # Final deployment model trained on ALL subjects (small internal val carve).
        all_subjects = sorted({int(s) for s in bundle.groups})
        val_subs, train_subs = _stratified_carve(bundle, all_subjects, cfg.val_fraction, rng)
        norms = _fit_norms(bundle, _idx_for(bundle, train_subs), cfg)
        Xn, Fn, sev_z = _apply_norms(bundle, norms, cfg)
        model, model_cfg, history = _build_and_train(
            bundle, Xn, Fn, sev_z, _idx_for(bundle, train_subs), _idx_for(bundle, val_subs), cfg, device)
        n_params = count_parameters(model)
        print(f"[model] TransformerDetector - {n_params} params, loss={cfg.loss}, cv_folds={cfg.cv_folds}")

        mlflow.log_params(cfg.flat_params())
        mlflow.log_params({
            "mode": f"{cfg.cv_folds}-fold-cv", "data_source": bundle.meta.get("source"),
            "n_windows": len(bundle), "n_positive": bundle.n_positive, "n_params": n_params,
            "fold_subjects": [f for f in folds], "device": str(device),
        })
        for h in history:
            mlflow.log_metrics({"train_loss": h["train_loss"], "val_recall": h["val_recall"],
                                "val_fpr_adl": h["val_fpr_adl"]}, step=h["epoch"])

        extra = (f"OOF over {int(m.sum())} windows / {len(folds)} folds; "
                 f"fold recall@0.5 mean={np.mean(fold_recalls):.3f}")
        return _finalize(run, f"{cfg.cv_folds}-fold OOF", model, model_cfg, norms, threshold,
                         platt, cfg, artifact_dir, bundle, Xn, Fn, np.flatnonzero(m),
                         metrics, severity_mae, brier_raw, brier_cal, n_params, device, extra)


def _finalize(run, title, model, model_cfg, norms, threshold, platt, cfg, artifact_dir,
              bundle, Xn, Fn, eval_pool_idx, metrics, severity_mae, brier_raw, brier_cal,
              n_params, device, extra) -> dict:
    """Shared metric logging + artifact persistence + report for both run modes."""
    import mlflow

    mlflow.log_metrics(metrics.as_flat_dict(prefix="test_"))
    mlflow.log_metrics({
        "severity_mae_ms2": severity_mae,
        "brier_raw": brier_raw, "brier_calibrated": brier_cal,
        "threshold": float(threshold),
        "meets_recall_target": float(metrics.recall >= TARGET_RECALL),
        "meets_fpr_adl_target": float(metrics.fpr_adl <= TARGET_FPR_ADL),
    })

    ckpt_path = _persist_checkpoint(artifact_dir, model, model_cfg, norms, threshold, platt, cfg)
    sample = _sample_inference_payload(model, Xn, Fn, bundle, eval_pool_idx, threshold, platt, norms, cfg, device)
    (artifact_dir / "sample_inference.json").write_text(json.dumps(sample, indent=2))
    results = {
        "run_id": run.info.run_id, "mode": title, "data_source": bundle.meta.get("source"),
        "n_params": n_params, "threshold": float(threshold),
        "test": metrics.as_flat_dict(), "severity_mae_ms2": severity_mae,
        "calibration": {"brier_raw": brier_raw, "brier_calibrated": brier_cal},
        "targets": {"recall": TARGET_RECALL, "fpr_adl": TARGET_FPR_ADL},
        "sample_inference": sample, "checkpoint": str(ckpt_path),
    }
    (artifact_dir / "cloud_results.json").write_text(json.dumps(results, indent=2, default=str))
    for name in ("transformer_detector_fp32.pt", "channel_stats.json", "feature_norm.json",
                 "sample_inference.json", "cloud_results.json"):
        mlflow.log_artifact(str(artifact_dir / name))

    _print_report(title, metrics, severity_mae, n_params, cfg, bundle.meta.get("source"), sample, extra)
    return results


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
    p.add_argument("--loss", choices=["focal", "bce"], default=TrainConfig.loss)
    p.add_argument("--focal-alpha", type=float, default=TrainConfig.focal_alpha)
    p.add_argument("--focal-gamma", type=float, default=TrainConfig.focal_gamma)
    p.add_argument("--pos-weight-scale", type=float, default=TrainConfig.pos_weight_scale)
    p.add_argument("--cv-folds", type=int, default=TrainConfig.cv_folds,
                   help=">=2 for subject k-fold CV; <=1 for a single train/val/test split")
    p.add_argument("--feature-norm", choices=["per_user", "global"], default=TrainConfig.feature_norm)
    p.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    args = p.parse_args(argv)

    cfg = TrainConfig(
        epochs=args.epochs, batch_size=args.batch_size, lr=args.lr, seed=args.seed,
        target_recall=args.target_recall, loss=args.loss, focal_alpha=args.focal_alpha,
        focal_gamma=args.focal_gamma, pos_weight_scale=args.pos_weight_scale,
        cv_folds=args.cv_folds, feature_norm=args.feature_norm, synthetic=args.synthetic,
    )
    runner = run_cv_training if cfg.cv_folds and cfg.cv_folds >= 2 else run_training
    runner(cfg, dataset_root=args.dataset_root)


if __name__ == "__main__":
    main()
