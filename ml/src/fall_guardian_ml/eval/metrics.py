"""Honest metrics for the edge pre-impact model.

The two numbers that decide whether this model is usable:

  • Recall  — of all real pre-impact windows, how many did we catch? Missing a
              fall is the worst failure. Target ≥ 95%.
  • FPR on ADL — of all everyday-activity windows, how many did we wrongly flag?
              This is what drives alert fatigue in daily wear. Target ≤ 5%.
              Measured over ADL-sourced windows specifically, not all negatives.

Everything here is plain NumPy so it has no heavy dependency and is trivially
unit-testable. A threshold sweep is provided so we can pick the operating point
that hits the recall target at the lowest possible ADL false-positive rate,
rather than blindly using 0.5.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass
class EdgeMetrics:
    """Classification metrics at one decision threshold."""

    threshold: float
    recall: float            # TP / (TP + FN)  over pre-impact positives
    precision: float         # TP / (TP + FP)
    f1: float
    fpr: float               # FP / (FP + TN)  over ALL negatives
    fpr_adl: float           # FP / N          over ADL-sourced windows only
    specificity: float       # TN / (TN + FP)
    tp: int
    fp: int
    tn: int
    fn: int
    n_positive: int
    n_adl: int

    def as_flat_dict(self, prefix: str = "") -> dict[str, float]:
        """Flatten for MLflow `log_metrics` (which wants str→float)."""
        return {f"{prefix}{k}": float(v) for k, v in asdict(self).items()}


def compute_metrics(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    is_adl: np.ndarray,
    threshold: float = 0.5,
) -> EdgeMetrics:
    """Compute edge metrics at `threshold`.

    Parameters
    ----------
    y_true : (N,) {0,1} — 1 = pre-impact.
    y_prob : (N,) sigmoid probabilities.
    is_adl : (N,) bool — window came from an ADL recording.
    """
    y_true = np.asarray(y_true).astype(bool)
    y_pred = np.asarray(y_prob) >= threshold
    is_adl = np.asarray(is_adl).astype(bool)

    tp = int(np.sum(y_pred & y_true))
    fp = int(np.sum(y_pred & ~y_true))
    tn = int(np.sum(~y_pred & ~y_true))
    fn = int(np.sum(~y_pred & y_true))

    recall = tp / (tp + fn) if (tp + fn) else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    specificity = tn / (tn + fp) if (tn + fp) else 0.0

    # FPR specifically over ADL windows (all ADL windows are true negatives).
    adl_pred_pos = int(np.sum(y_pred & is_adl))
    n_adl = int(np.sum(is_adl))
    fpr_adl = adl_pred_pos / n_adl if n_adl else 0.0

    return EdgeMetrics(
        threshold=float(threshold),
        recall=recall,
        precision=precision,
        f1=f1,
        fpr=fpr,
        fpr_adl=fpr_adl,
        specificity=specificity,
        tp=tp,
        fp=fp,
        tn=tn,
        fn=fn,
        n_positive=int(np.sum(y_true)),
        n_adl=n_adl,
    )


def pick_threshold_for_recall(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    is_adl: np.ndarray,
    target_recall: float = 0.95,
) -> EdgeMetrics:
    """Choose the operating threshold that meets `target_recall` at the lowest ADL FPR.

    Sweeps thresholds over the observed probability values, keeps those whose
    recall ≥ target, and returns the one with the smallest `fpr_adl`. Falls back
    to the max-recall threshold if the target is unreachable.
    """
    y_prob = np.asarray(y_prob)
    candidates = np.unique(np.concatenate([[0.0], y_prob, [1.0]]))
    swept = [compute_metrics(y_true, y_prob, is_adl, t) for t in candidates]

    meeting = [m for m in swept if m.recall >= target_recall]
    if meeting:
        return min(meeting, key=lambda m: (m.fpr_adl, -m.threshold))
    # Target unreachable on this set → return the highest-recall point.
    return max(swept, key=lambda m: (m.recall, -m.fpr_adl))


@dataclass
class LeadTimeStats:
    """Lead-time summary for correctly-predicted pre-impact windows (in ms)."""

    n: int
    mean_ms: float
    median_ms: float
    p10_ms: float
    p90_ms: float
    histogram_counts: list[int]
    histogram_edges_ms: list[float]


def lead_time_stats(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    t_to_impact_s: np.ndarray,
    threshold: float,
    n_bins: int = 10,
) -> LeadTimeStats:
    """How early did we fire on the falls we caught?

    Uses only TRUE POSITIVES (real pre-impact windows we predicted positive).
    `t_to_impact_s` is the time from window end to the impact peak. Target mean
    ≥ 300 ms — enough warning for a haptic brace cue.
    """
    y_true = np.asarray(y_true).astype(bool)
    y_pred = np.asarray(y_prob) >= threshold
    caught = y_true & y_pred
    leads = np.asarray(t_to_impact_s)[caught]
    leads = leads[np.isfinite(leads)] * 1000.0  # → ms

    if leads.size == 0:
        return LeadTimeStats(0, 0.0, 0.0, 0.0, 0.0, [], [])

    counts, edges = np.histogram(leads, bins=n_bins)
    return LeadTimeStats(
        n=int(leads.size),
        mean_ms=float(np.mean(leads)),
        median_ms=float(np.median(leads)),
        p10_ms=float(np.percentile(leads, 10)),
        p90_ms=float(np.percentile(leads, 90)),
        histogram_counts=[int(c) for c in counts],
        histogram_edges_ms=[float(e) for e in edges],
    )
