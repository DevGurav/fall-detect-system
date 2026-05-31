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
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from fall_guardian_ml.datasets.edge_dataset import (
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

    epochs: int = 40
    batch_size: int = 128
    lr: float = 1e-3
    weight_decay: float = 1e-4
    test_fraction: float = 0.2          # fraction of SUBJECTS held out for test
    val_fraction: float = 0.15          # fraction of TRAIN subjects used for val
    seed: int = 42
    target_recall: float = TARGET_RECALL
    # Extra multiplier on the auto neg/pos `pos_weight` in BCE. The staggered
    # window family already lifts the positive rate, but a pre-impact miss is the
    # costliest error, so we bias a little harder toward recall (>1.0).
    pos_weight_scale: float = 1.5
    synthetic: bool = False
    model: ConvLSTMTinyConfig = field(default_factory=ConvLSTMTinyConfig)

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
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    ds = TensorDataset(
        torch.from_numpy(X).float(), torch.from_numpy(y).float()
    )
    return DataLoader(ds, batch_size=batch_size, shuffle=shuffle)


def _train_loop(model, loaders, cfg: TrainConfig, pos_weight: float, device):
    import torch
    from torch import nn

    opt = torch.optim.Adam(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
    loss_fn = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor([pos_weight], device=device)
    )
    train_loader, val_loader = loaders

    best_val_recall = -1.0
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

        # Validation recall @ 0.5 — a cheap epoch signal for checkpoint selection.
        val_probs, val_y = _infer(model, val_loader, device)
        val_pred = val_probs >= 0.5
        tp = float(np.sum(val_pred & (val_y == 1)))
        fn = float(np.sum(~val_pred & (val_y == 1)))
        val_recall = tp / (tp + fn) if (tp + fn) else 0.0

        history.append({"epoch": epoch, "train_loss": epoch_loss, "val_recall": val_recall})
        if val_recall >= best_val_recall:
            best_val_recall = val_recall
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
        bundle = build_edge_bundle(dataset_root)
    print(f"[data] {bundle.summary()}")

    # 2. Subject-stratified split.
    split = subject_split(bundle, cfg)
    if len(split.train_idx) == 0 or len(split.test_idx) == 0:
        raise RuntimeError("Empty train or test split — too few subjects to partition.")

    # 3. Standardize channels using TRAIN windows only (no leakage).
    stats = fit_channel_stats(bundle.X[split.train_idx])
    Xn = stats.apply(bundle.X)

    # 4. Build the model.
    model = build_model(cfg.model).to(device)
    n_params = count_parameters(model)
    print(f"[model] ConvLSTM-tiny - {n_params} params (~{n_params / 1024:.1f} KB at INT8)")

    mlflow.set_experiment(EXPERIMENT_NAME)
    with mlflow.start_run(run_name="convlstm-tiny-baseline") as run:
        mlflow.log_params(cfg.flat_params())
        mlflow.log_params(
            {
                "data_source": bundle.meta.get("source"),
                "n_windows": len(bundle),
                "n_positive": bundle.n_positive,
                "n_params": n_params,
                "test_subjects": split.test_subjects,
                "val_subjects": split.val_subjects,
                "device": str(device),
            }
        )

        # 5. Train.
        train_loader = _make_loader(
            Xn[split.train_idx], bundle.y[split.train_idx], cfg.batch_size, shuffle=True
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
        model, history = _train_loop(model, (train_loader, val_loader), cfg, pos_weight, device)
        for h in history:
            mlflow.log_metrics(
                {"train_loss": h["train_loss"], "val_recall": h["val_recall"]}, step=h["epoch"]
            )

        # 6. Pick threshold on VAL to hit the recall target at lowest ADL FPR.
        val_probs, val_y = _infer(model, val_loader, device)
        val_op = pick_threshold_for_recall(
            val_y, val_probs, bundle.is_adl[split.val_idx], cfg.target_recall
        )
        threshold = val_op.threshold
        print(f"[threshold] picked {threshold:.4f} on val (recall {val_op.recall:.3f})")

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
                "model_config": asdict(cfg.model),
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
    print(f"  Recall      : {test_metrics.recall:6.3f}   target >={cfg.target_recall:.2f} "
          f"{tick(test_metrics.recall >= cfg.target_recall)}")
    print(f"  FPR on ADL  : {test_metrics.fpr_adl:6.3f}   target <={TARGET_FPR_ADL:.2f} "
          f"{tick(test_metrics.fpr_adl <= TARGET_FPR_ADL)}")
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
    p.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    args = p.parse_args(argv)

    cfg = TrainConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        seed=args.seed,
        pos_weight_scale=args.pos_weight_scale,
        synthetic=args.synthetic,
    )
    run_training(cfg, dataset_root=args.dataset_root)


if __name__ == "__main__":
    main()
