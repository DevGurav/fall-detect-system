"""Transformer detector — the cloud post-impact confirmation model.

Runs in the FastAPI service (FP32, unconstrained) as the PRECISION gate behind
the recall-first edge model: it confirms or suppresses each edge trigger and
assigns severity, so a caregiver is only paged on a real fall.

Architecture (reconciles MODEL_CARD §1.1/§1.3 — see ADR-011 personalization work)
---------------------------------------------------------------------------------
A Transformer encoder over the raw 125-step window, with the 43-d engineered
feature vector fused at the pooled head. Two heads:

    raw window (B, 125, 6)
      │  Linear(6 → d_model)  + sinusoidal positional encoding
      ├─ TransformerEncoder × N  (multi-head self-attention, GELU FFN)
      ├─ mean-pool over time                         → (B, d_model)
      │     ⊕ concat  43-d engineered feature vector → (B, d_model + 43)
      ├─ Linear(→ head_hidden) + GELU + Dropout
      ├─ Linear(head_hidden → 1)   detection logit   → P(fall)  (BCE)
      └─ Linear(head_hidden → 1)   severity scalar    → standardized peak |a| (MSE)

Why this shape (not a Transformer on the 43-d vector alone): a single 43-d vector
is one token — self-attention needs a sequence. The encoder learns temporal
structure from the raw window; the engineered vector injects the hand-designed
fall cues (SMA, jerk, spectral entropy) the attention would otherwise have to
rediscover. Both inputs are available at serving time from the same window the
backend already receives, so train and serve agree.

6 input channels (ax, ay, az, wx, wy, wz) — exactly what the API carries; no
orientation, which the device never sends.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import torch
from torch import nn

# Window contract (must match features/windowing.py + the API IMUSample).
N_CHANNELS = 6        # ax, ay, az, wx, wy, wz
WINDOW_SAMPLES = 125  # 2.5 s @ 50 Hz
N_FEATURES = 43       # len(features.extraction.feature_names())


@dataclass(frozen=True)
class TransformerDetectorConfig:
    """Hyperparameters for the cloud Transformer detector. Frozen for reproducibility.

    Defaults match the locked MODEL_CARD §1.3 sizing (d_model 64, 4 layers,
    4 heads, d_ff 128) — small by Transformer standards, which suits a single
    2.5 s window and keeps the FP32 service light.
    """

    n_channels: int = N_CHANNELS
    window_samples: int = WINDOW_SAMPLES
    n_features: int = N_FEATURES

    d_model: int = 64
    n_heads: int = 4
    n_layers: int = 4
    d_ff: int = 128
    encoder_dropout: float = 0.1

    head_hidden: int = 32
    head_dropout: float = 0.3


class _SinusoidalPositionalEncoding(nn.Module):
    """Standard fixed sinusoidal positional encoding (Vaswani et al. 2017).

    Parameter-free (registered buffer), so it adds positional information without
    growing the model or risking overfit on the small fall pool.
    """

    def __init__(self, d_model: int, max_len: int) -> None:
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float32).unsqueeze(1)
        div_term = torch.exp(
            torch.arange(0, d_model, 2, dtype=torch.float32) * (-math.log(10000.0) / d_model)
        )
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer("pe", pe.unsqueeze(0))  # (1, max_len, d_model)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (B, T, d_model) → x + positional encoding for the first T positions."""
        return x + self.pe[:, : x.size(1)]


@dataclass
class DetectorOutput:
    """Forward outputs: the detection logit and the (standardized) severity scalar."""

    fall_logit: torch.Tensor   # (B,) raw logit — BCEWithLogitsLoss in training
    severity: torch.Tensor     # (B,) regressed standardized peak |a|


class TransformerDetector(nn.Module):
    """Transformer-encoder fall detector with a fused engineered-feature head.

    forward(x_raw, feats) → DetectorOutput. Use BCEWithLogitsLoss on `fall_logit`
    (sigmoid at inference) and MSE on `severity` (against a standardized peak-|a|
    target; un-standardize at serving to map onto the Severity enum).
    """

    def __init__(self, config: TransformerDetectorConfig | None = None) -> None:
        super().__init__()
        cfg = config or TransformerDetectorConfig()
        self.config = cfg

        self.input_proj = nn.Linear(cfg.n_channels, cfg.d_model)
        self.pos_enc = _SinusoidalPositionalEncoding(cfg.d_model, cfg.window_samples)

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=cfg.d_model,
            nhead=cfg.n_heads,
            dim_feedforward=cfg.d_ff,
            dropout=cfg.encoder_dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,  # pre-norm: steadier training for a small/deep stack
        )
        # enable_nested_tensor disabled: it's incompatible with norm_first=True
        # (PyTorch would warn and skip it anyway), and our windows are fixed-length
        # with no padding mask, so the nested-tensor fast path buys nothing here.
        self.encoder = nn.TransformerEncoder(
            encoder_layer, num_layers=cfg.n_layers, enable_nested_tensor=False
        )

        # Shared head trunk over [pooled encoder output ⊕ engineered features].
        self.head_trunk = nn.Sequential(
            nn.Linear(cfg.d_model + cfg.n_features, cfg.head_hidden),
            nn.GELU(),
            nn.Dropout(cfg.head_dropout),
        )
        self.fall_head = nn.Linear(cfg.head_hidden, 1)
        self.severity_head = nn.Linear(cfg.head_hidden, 1)

    def forward(self, x_raw: torch.Tensor, feats: torch.Tensor) -> DetectorOutput:
        """x_raw: (B, window_samples, n_channels); feats: (B, n_features)."""
        h = self.input_proj(x_raw)          # (B, T, d_model)
        h = self.pos_enc(h)
        h = self.encoder(h)                 # (B, T, d_model)
        pooled = h.mean(dim=1)              # (B, d_model) — mean-pool over time
        fused = torch.cat([pooled, feats], dim=1)   # (B, d_model + n_features)
        z = self.head_trunk(fused)
        return DetectorOutput(
            fall_logit=self.fall_head(z).squeeze(-1),    # (B,)
            severity=self.severity_head(z).squeeze(-1),  # (B,)
        )

    @torch.no_grad()
    def predict(self, x_raw: torch.Tensor, feats: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """(fall probability, standardized severity) — for evaluation / serving."""
        out = self.forward(x_raw, feats)
        return torch.sigmoid(out.fall_logit), out.severity


def count_parameters(model: nn.Module) -> int:
    """Total trainable parameter count."""
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def build_model(config: TransformerDetectorConfig | None = None) -> TransformerDetector:
    """Factory used by the training script (mirrors models.convlstm_tiny.build_model)."""
    return TransformerDetector(config)
