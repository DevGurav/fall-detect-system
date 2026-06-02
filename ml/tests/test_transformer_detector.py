"""Tests for the Transformer cloud detector model (shapes, params, fused head)."""
from __future__ import annotations

import numpy as np
import torch

from fall_guardian_ml.models.transformer_detector import (
    TransformerDetectorConfig,
    build_model,
    count_parameters,
)


def _inputs(batch: int, cfg: TransformerDetectorConfig):
    rng = np.random.default_rng(0)
    x = torch.from_numpy(
        rng.standard_normal((batch, cfg.window_samples, cfg.n_channels)).astype(np.float32)
    )
    f = torch.from_numpy(rng.standard_normal((batch, cfg.n_features)).astype(np.float32))
    return x, f


def test_forward_output_shapes():
    cfg = TransformerDetectorConfig()
    model = build_model(cfg)
    x, f = _inputs(8, cfg)
    out = model(x, f)
    assert out.fall_logit.shape == (8,)
    assert out.severity.shape == (8,)


def test_predict_probabilities_in_range():
    cfg = TransformerDetectorConfig()
    model = build_model(cfg)
    x, f = _inputs(8, cfg)
    probs, _sev = model.predict(x, f)
    assert probs.shape == (8,)
    assert float(probs.min()) >= 0.0 and float(probs.max()) <= 1.0


def test_count_parameters_positive():
    assert count_parameters(build_model()) > 0


def test_config_sizes_to_channels_and_features():
    """6 channels + 43 features is the served contract; the model builds to it."""
    cfg = TransformerDetectorConfig(n_channels=6, n_features=43)
    model = build_model(cfg)
    x, f = _inputs(4, cfg)
    out = model(x, f)
    assert out.fall_logit.shape == (4,)


def test_severity_head_responds_to_features():
    """The fused engineered-feature input actually reaches the heads (grad flows)."""
    cfg = TransformerDetectorConfig()
    model = build_model(cfg)
    x, f = _inputs(4, cfg)
    f.requires_grad_(True)
    out = model(x, f)
    out.fall_logit.sum().backward()
    assert f.grad is not None and torch.any(f.grad != 0)
