"""Tests for the cloud detector's dataset assembly (synthetic bundle + contract).

Numpy-only (no torch) so they stay fast. The real WEDA-FALL path shares the same
windowing + labeling code already covered by test_pre_impact_labels / test_features.
"""
from __future__ import annotations

import numpy as np

from fall_guardian_ml.datasets.cloud_dataset import (
    N_CHANNELS,
    N_FEATURES,
    CloudBundle,
    make_synthetic_cloud_bundle,
)
from fall_guardian_ml.datasets.pre_impact_labels import Phase
from fall_guardian_ml.features.windowing import WINDOW_SAMPLES


def _bundle() -> CloudBundle:
    return make_synthetic_cloud_bundle(n_subjects=6, falls_per_subject=15, adls_per_subject=30, seed=0)


def test_synthetic_bundle_shapes_and_contract():
    b = _bundle()
    n = len(b)
    assert b.X_raw.shape == (n, WINDOW_SAMPLES, N_CHANNELS)
    assert b.feats.shape == (n, N_FEATURES)        # the 43-d engineered vector
    assert b.y.shape == (n,) and set(np.unique(b.y)).issubset({0, 1})
    assert b.groups.shape == (n,)
    assert b.is_adl.shape == (n,) and b.is_adl.dtype == bool
    assert b.severity.shape == (n,)
    assert b.X_raw.dtype == np.float32 and b.feats.dtype == np.float32
    assert not np.isnan(b.feats).any()


def test_both_classes_present_and_per_subject():
    """Every subject must carry both classes so the subject-split can measure recall."""
    b = _bundle()
    assert 0 < b.n_positive < len(b)
    for s in np.unique(b.groups):
        ys = b.y[b.groups == s]
        assert (ys == 1).any() and (ys == 0).any()


def test_positive_class_is_impact_post_impact_semantics():
    """Positive (fall) windows carry a much larger peak |a| than ADL negatives."""
    b = _bundle()
    assert b.severity[b.y == 1].mean() > b.severity[b.y == 0].mean()
    assert b.meta["positive_class"] == "IMPACT+POST_IMPACT"


def test_synthetic_is_adl_aligns_with_negatives():
    """In the synthetic bundle, ADL windows are exactly the negatives."""
    b = _bundle()
    assert np.array_equal(b.is_adl, b.y == 0)


def test_phase_aligns_with_label():
    """phase ∈ {IMPACT, POST_IMPACT} exactly when y == 1 (the detection positive)."""
    b = _bundle()
    assert b.phase.shape == (len(b),)
    is_pos_phase = np.isin(b.phase, [Phase.IMPACT.value, Phase.POST_IMPACT.value])
    assert np.array_equal(is_pos_phase, b.y == 1)


def test_pos_weight_and_summary():
    b = _bundle()
    assert b.pos_weight > 0
    assert "fall" in b.summary() and "subjects" in b.summary()
