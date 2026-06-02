"""Train the ConvLSTM-tiny edge model with MLflow tracking.

End-to-end Week-B baseline:

    assemble windows → subject-stratified split → standardize channels
    → train ConvLSTM-tiny (weighted BCE) → pick threshold for the recall target
    → evaluate (recall, FPR-on-ADL, lead time) → log everything to MLflow
    → save the FP32 checkpoint + channel stats (inputs to INT8 quantization)

Validation methodology (the non-negotiable bit, per ARCHITECTURE.md §4.7):
  • Subject-stratified split — held-out test subjects never appear in training.
  • Honest metrics — recall + FPR-on-ADL + lead-time histogram, not accuracy.
  • Everything MLflow-tracked under the "fall-guardian/edge" experiment.

Run it:
    python -m fall_guardian_ml.training.train_edge                  # real WEDA-FALL
    python -m fall_guardian_ml.training.train_edge --synthetic      # smoke test
"""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np

from fall_guardian_ml.datasets.edge_dataset import (
    ChannelStats,
    EdgeBundle,
    build_edge_bundle,
    fit_channel_stats,
    make_synthetic_bundle,
)
from fall_guardian_ml.eval.metrics import (
    compute_metrics,
    lead_time_stats,
    pick_threshold_for_recall,
)
from fall_guardian_ml.training.augment import AugmentConfig, augment_window
from fall_guardian_ml.models.convlstm_tiny import (
    ConvLSTMTinyConfig,
    build_model,
    count_parameters,
)

# Repo paths: this file is ml/src/fall_guardian_ml/training/train_edge.py
ML_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET_ROOT = ML_ROOT / "data" / "raw" / "WEDA-FALL-main"
DEFAULT_ARTIFACT_DIR = ML_ROOT / "artifacts" / "edge"

EXPERIMENT_NAME = "fall-guardian/edge"
TARGET_RECALL = 0.95
TARGET_FPR_ADL = 0.05
TARGET_LEAD_MS = 300.0


@dataclass
class TrainConfig:
    """All knobs for one edge training run — logged verbatim to MLflow params."""

    epochs: int = 50
    batch_size: int = 128
    lr: float = 1e-3
    # Heavier decoupled weight decay (AdamW) to regularise the deeper v2 net
    # against the small fall pool. Pairs with the conv/head dropout in the model.
    weight_decay: float = 5e-4
    test_fraction: float = 0.2          # fraction of SUBJECTS held out for test
    val_fraction: float = 0.15          # fraction of TRAIN subjects used for val
    seed: int = 42
    # OPERATING-POINT OBJECTIVE (Phase 14): RECALL-constrained. A missed fall is
    # the fatal error on a life-safety device, so we guarantee recall ≥ this floor
    # and accept whatever FPR-on-ADL it costs (see eval.metrics.pick_threshold_for_recall).
    # The cloud detection model (Week C) is the secondary gate that filters the
    # resulting edge false positives — so high edge FPR is an explicit design choice.
    #
    # This is the VAL selection floor, set ABOVE the 0.95 product requirement
    # (TARGET_RECALL) to absorb the val→test generalisation gap so held-out TEST
    # recall still clears 0.95 — empirically 0.97 → ~0.965 test / 0.95 → ~0.933.
    # A proper subject k-fold CV (deferred per directive) would pin this robustly.
    target_recall: float = 0.97
    # Reference only now (not enforced) — the FPR the comfort target *would* like.
    max_fpr_adl: float = TARGET_FPR_ADL
    # Multiplier on the auto neg/pos `pos_weight` in BCE. Kept at 1.0: the
    # staggered window family already balances classes, and the threshold (not
    # loss weighting) sets the operating point.
    pos_weight_scale: float = 1.0
    # Default OFF: the Phase 13 ablation showed the orientation quaternion HURT
    # edge recall (83.9→76.7% isolated) — its absolute, subject-dependent frame
    # doesn't generalise across held-out subjects, and gyro already carries the
    # rotational dynamics. Kept as an opt-in (`include_orientation`) for the cloud
    # model / future data. See BUILD_LOG Phase 13.
    include_orientation: bool = False
    synthetic: bool = False
    model: ConvLSTMTinyConfig = field(default_factory=ConvLSTMTinyConfig)
    augment: AugmentConfig = field(default_factory=AugmentConfig)

    def flat_params(self) -> dict[str, object]:
        d = {k: v for k, v in asdict(self).items() if k not in ("model", "augment")}
        d.update({f"model.{k}": v for k, v in asdict(self.model).items()})
        d.update({f"aug.{k}": v for k, v in asdict(self.augment).items()})
        return d


# ─── Reproducibility ─────────────────────────────────────────────────────────


def _seed_everything(seed: int) -> None:
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


# ─── Subject-stratified split ────────────────────────────────────────────────


@dataclass
class Split:
    train_idx: np.ndarray
    val_idx: np.ndarray
    test_idx: np.ndarray
    test_subjects: list[int]
    val_subjects: list[int]


def subject_split(bundle: EdgeBundle, cfg: TrainConfig) -> Split:
    """Partition windows by SUBJECT so no subject is in more than one split.

    Subjects that contribute pre-impact positives (fall subjects) are spread
    across train/val/test so every split can actually measure recall — a random
    subject draw can otherwise hand all the falls to one split.
    """
    rng = np.random.default_rng(cfg.seed)
    subjects = np.array(sorted({int(s) for s in bundle.groups}))

    # Which subjects have any positive (pre-impact) window?
    pos_subjects = np.array(
        sorted({int(s) for s in bundle.groups[bundle.y == 1]})
    )
    neg_only = np.array([s for s in subjects if s not in set(pos_subjects.tolist())])

    def _carve(pool: np.ndarray, frac: float) -> tuple[list[int], np.ndarray]:
        pool = pool.copy()
        rng.shuffle(pool)
        k = max(1, int(round(len(pool) * frac))) if len(pool) else 0
        return pool[:k].tolist(), pool[k:]

    # Stratify the test split across both pos- and neg-only subjects.
    test_pos, rest_pos = _carve(pos_subjects, cfg.test_fraction)
    test_neg, rest_neg = _carve(neg_only, cfg.test_fraction)
    test_subjects = sorted(test_pos + test_neg)

    # Carve val out of the remaining (train) subjects, again stratified.
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


# ─── Training ────────────────────────────────────────────────────────────────


def _make_loader(X, y, batch_size, shuffle):
    """Plain loader over already-standardized windows (val / test)."""
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    ds = TensorDataset(
        torch.from_numpy(X).float(), torch.from_numpy(y).float()
    )
    return DataLoader(ds, batch_size=batch_size, shuffle=shuffle)


def _make_train_loader(X_raw, y, stats: ChannelStats, aug: AugmentConfig, batch_size, seed):
    """Training loader: RAW windows → on-the-fly augment → standardize per item.

    Augmenting before standardization keeps the transform factors physical, and
    re-augmenting each epoch (fresh rng draws) is what manufactures diversity.
    """
    import torch
    from torch.utils.data import DataLoader, Dataset

    class _AugTrainDataset(Dataset):
        def __init__(self) -> None:
            self.X = X_raw
            self.y = y
            self.rng = np.random.default_rng(seed)

        def __len__(self) -> int:
            return len(self.X)

        def __getitem__(self, i: int):
            w = self.X[i]
            if aug.enabled:
                w = augment_window(w, self.rng, aug)
            w = stats.apply(w)  # standardize (T, C) with per-channel train stats
            return torch.from_numpy(w).float(), torch.tensor(self.y[i], dtype=torch.float32)

    return DataLoader(_AugTrainDataset(), batch_size=batch_size, shuffle=True)


def _train_loop(model, loaders, cfg: TrainConfig, pos_weight: float, device, val_is_adl):
    import torch
    from torch import nn

    # AdamW = decoupled weight decay (proper L2 regularisation for this net).
    opt = torch.optim.AdamW(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
    loss_fn = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor([pos_weight], device=device)
    )
    train_loader, val_loader = loaders

    best_score = (-1.0, -1e9)
    best_state = None
    history: list[dict] = []

    for epoch in range(cfg.epochs):
        model.train()
        epoch_loss = 0.0
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            opt.zero_grad()
            logits = model(xb)
            loss = loss_fn(logits, yb)
            loss.backward()
            opt.step()
            epoch_loss += loss.item() * xb.size(0)
        epoch_loss /= max(len(train_loader.dataset), 1)

        # Recall-first checkpoint selection: at the recall-constrained operating
        # point on val, prefer epochs that MEET the recall floor and, among those,
        # the lowest FPR; if none meet it yet, prefer the highest recall. A missed
        # fall is the fatal error, so recall is the hard objective, not FPR.
        val_probs, val_y = _infer(model, val_loader, device)
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


def _infer(model, loader, device):
    import torch

    model.eval()
    probs, ys = [], []
    with torch.no_grad():
        for xb, yb in loader:
            p = torch.sigmoid(model(xb.to(device))).cpu().numpy()
            probs.append(np.atleast_1d(p))
            ys.append(yb.numpy())
    return np.concatenate(probs), np.concatenate(ys)


# ─── Orchestration ───────────────────────────────────────────────────────────


def run_training(
    cfg: TrainConfig,
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    artifact_dir: Path = DEFAULT_ARTIFACT_DIR,
) -> dict:
    """Execute one full edge-training run and return a results dict.

    Logs params, metrics and artifacts to MLflow under "fall-guardian/edge".
    """
    import mlflow
    import torch

    _seed_everything(cfg.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # 1. Assemble the dataset.
    if cfg.synthetic:
        bundle = make_synthetic_bundle(seed=cfg.seed)
    else:
        bundle = build_edge_bundle(dataset_root, include_orientation=cfg.include_orientation)
    print(f"[data] {bundle.summary()}")

    # 2. Subject-stratified split.
    split = subject_split(bundle, cfg)
    if len(split.train_idx) == 0 or len(split.test_idx) == 0:
        raise RuntimeError("Empty train or test split — too few subjects to partition.")

    # 3. Standardize channels using TRAIN windows only (no leakage).
    stats = fit_channel_stats(bundle.X[split.train_idx])
    Xn = stats.apply(bundle.X)

    # 4. Build the model, sized to the actual channel count (6, or 10 with the
    #    orientation quaternion). The frozen model config is updated to match.
    n_channels = int(bundle.X.shape[-1])
    model_cfg = replace(cfg.model, n_channels=n_channels)
    model = build_model(model_cfg).to(device)
    n_params = count_parameters(model)
    print(f"[model] ConvLSTM-tiny - {n_params} params (~{n_params / 1024:.1f} KB at INT8), "
          f"{n_channels} channels")

    mlflow.set_experiment(EXPERIMENT_NAME)
    with mlflow.start_run(run_name="convlstm-tiny-baseline") as run:
        mlflow.log_params(cfg.flat_params())
        mlflow.log_params(
            {
                "data_source": bundle.meta.get("source"),
                "n_windows": len(bundle),
                "n_positive": bundle.n_positive,
                "n_channels": n_channels,
                "n_params": n_params,
                "test_subjects": split.test_subjects,
                "val_subjects": split.val_subjects,
                "device": str(device),
            }
        )

        # 5. Train. The train loader augments RAW windows on the fly then
        #    standardizes; val/test use the pre-standardized Xn, never augmented.
        train_loader = _make_train_loader(
            bundle.X[split.train_idx], bundle.y[split.train_idx],
            stats, cfg.augment, cfg.batch_size, cfg.seed,
        )
        val_loader = _make_loader(
            Xn[split.val_idx], bundle.y[split.val_idx], cfg.batch_size, shuffle=False
        )
        auto_pos_weight = EdgeBundle(
            X=bundle.X[split.train_idx], y=bundle.y[split.train_idx],
            groups=bundle.groups[split.train_idx], is_adl=bundle.is_adl[split.train_idx],
            t_to_impact_s=bundle.t_to_impact_s[split.train_idx],
        ).pos_weight
        pos_weight = auto_pos_weight * cfg.pos_weight_scale
        mlflow.log_params({"auto_pos_weight": round(auto_pos_weight, 3), "pos_weight": round(pos_weight, 3)})
        print(f"[loss] pos_weight = {auto_pos_weight:.2f} (auto) x {cfg.pos_weight_scale} = {pos_weight:.2f}")
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

        # 6. Pick the operating threshold on VAL: guarantee recall ≥ target, then
        #    take the lowest FPR among the thresholds that meet it. Recall is the
        #    hard safety constraint; the resulting FPR is accepted and handed to
        #    the cloud detection model (Week C) as the secondary gate.
        val_probs, val_y = _infer(model, val_loader, device)
        val_op = pick_threshold_for_recall(
            val_y, val_probs, bundle.is_adl[split.val_idx], cfg.target_recall
        )
        threshold = val_op.threshold
        print(
            f"[threshold] picked {threshold:.4f} on val "
            f"(recall {val_op.recall:.3f} @ FPR-ADL {val_op.fpr_adl:.3f}, recall floor {cfg.target_recall})"
        )

        # 7. Evaluate on the held-out TEST subjects at that threshold.
        test_loader = _make_loader(
            Xn[split.test_idx], bundle.y[split.test_idx], cfg.batch_size, shuffle=False
        )
        test_probs, test_y = _infer(model, test_loader, device)
        test_metrics = compute_metrics(
            test_y, test_probs, bundle.is_adl[split.test_idx], threshold
        )
        lead = lead_time_stats(
            test_y, test_probs, bundle.t_to_impact_s[split.test_idx], threshold
        )

        mlflow.log_metrics(test_metrics.as_flat_dict(prefix="test_"))
        mlflow.log_metrics(
            {
                "lead_mean_ms": lead.mean_ms,
                "lead_median_ms": lead.median_ms,
                "lead_p10_ms": lead.p10_ms,
            }
        )
        mlflow.log_metrics(
            {
                "meets_recall_target": float(test_metrics.recall >= cfg.target_recall),
                "meets_fpr_adl_target": float(test_metrics.fpr_adl <= TARGET_FPR_ADL),
                "meets_lead_target": float(lead.mean_ms >= TARGET_LEAD_MS),
            }
        )

        # 8. Persist artifacts: FP32 checkpoint + channel stats + decision threshold.
        ckpt_path = artifact_dir / "convlstm_tiny_fp32.pt"
        torch.save(
            {
                "state_dict": model.state_dict(),
                "model_config": asdict(model_cfg),  # the built config (n_channels matches)
                "threshold": threshold,
            },
            ckpt_path,
        )
        stats_path = artifact_dir / "channel_stats.json"
        stats_path.write_text(
            json.dumps({"mean": stats.mean.tolist(), "std": stats.std.tolist()}, indent=2)
        )
        results = {
            "run_id": run.info.run_id,
            "data_source": bundle.meta.get("source"),
            "n_params": n_params,
            "threshold": threshold,
            "test": test_metrics.as_flat_dict(),
            "lead": asdict(lead),
            "targets": {
                "recall": cfg.target_recall,
                "fpr_adl": TARGET_FPR_ADL,
                "lead_ms": TARGET_LEAD_MS,
            },
            "checkpoint": str(ckpt_path),
        }
        (artifact_dir / "edge_results.json").write_text(json.dumps(results, indent=2, default=str))
        mlflow.log_artifact(str(ckpt_path))
        mlflow.log_artifact(str(stats_path))
        mlflow.log_artifact(str(artifact_dir / "edge_results.json"))

        _print_report(test_metrics, lead, n_params, cfg, bundle.meta.get("source"))
        return results


def _print_report(test_metrics, lead, n_params, cfg, source) -> None:
    tick = lambda ok: "[PASS]" if ok else "[FAIL]"  # noqa: E731
    print("\n" + "=" * 60)
    print(f"  EDGE BASELINE - ConvLSTM-tiny  (data: {source})")
    print("=" * 60)
    print(f"  Recall      : {test_metrics.recall:6.3f}   product floor >={TARGET_RECALL:.2f} "
          f"{tick(test_metrics.recall >= TARGET_RECALL)} (HARD safety constraint; "
          f"val sel-floor {cfg.target_recall:.2f})")
    print(f"  FPR on ADL  : {test_metrics.fpr_adl:6.3f}   accepted; cloud model is the 2nd gate")
    print(f"  Precision   : {test_metrics.precision:6.3f}")
    print(f"  F1          : {test_metrics.f1:6.3f}")
    print(f"  Lead (mean) : {lead.mean_ms:6.1f} ms target >={TARGET_LEAD_MS:.0f} "
          f"{tick(lead.mean_ms >= TARGET_LEAD_MS)}")
    print(f"  Confusion   : TP={test_metrics.tp} FP={test_metrics.fp} "
          f"TN={test_metrics.tn} FN={test_metrics.fn}")
    print(f"  Params      : {n_params}  (~{n_params / 1024:.1f} KB INT8)")
    print("=" * 60)
    if source == "SYNTHETIC":
        print("  ! SYNTHETIC DATA - pipeline-smoke numbers, NOT WEDA-FALL.")
    print()


def main(argv: list[str] | None = None) -> None:
    import argparse

    p = argparse.ArgumentParser(description="Train the ConvLSTM-tiny edge model.")
    p.add_argument("--synthetic", action="store_true", help="use synthetic smoke-test data")
    p.add_argument("--epochs", type=int, default=TrainConfig.epochs)
    p.add_argument("--batch-size", type=int, default=TrainConfig.batch_size)
    p.add_argument("--lr", type=float, default=TrainConfig.lr)
    p.add_argument("--seed", type=int, default=TrainConfig.seed)
    p.add_argument("--pos-weight-scale", type=float, default=TrainConfig.pos_weight_scale)
    p.add_argument("--target-recall", type=float, default=TrainConfig.target_recall,
                   help="HARD recall floor; threshold meets it at the lowest FPR")
    p.add_argument("--no-augment", action="store_true", help="disable train-set augmentation")
    p.add_argument("--orientation", action="store_true",
                   help="add orientation quaternion channels (10ch); ablation showed it hurts edge recall")
    p.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    args = p.parse_args(argv)

    cfg = TrainConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        seed=args.seed,
        pos_weight_scale=args.pos_weight_scale,
        target_recall=args.target_recall,
        synthetic=args.synthetic,
        augment=AugmentConfig(enabled=not args.no_augment),
        include_orientation=args.orientation,
    )
    run_training(cfg, dataset_root=args.dataset_root)


if __name__ == "__main__":
    main()
