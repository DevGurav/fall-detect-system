"""ConvLSTM-tiny — the edge pre-impact prediction model.

This is the model that runs on the ESP32-S3 wrist wearable under TFLite Micro.
Its job: looking at the most recent 2.5 s (125-sample) raw IMU window, decide
whether a fall impact is *about to* happen (the PRE_IMPACT phase is sitting at
the tail of the window). It must fit in ≤80 KB INT8 and run in <80 ms.

Architecture (v2 — Phase 12 capacity + regularisation bump)
-----------------------------------------------------------
The v1 net (~10 k params) hit 95% recall but couldn't push FPR-on-ADL below
~19%: it lacked the representational power to separate a fall run-up from
vigorous everyday motion across subjects. We have ~70 KB of INT8 budget spare,
so v2 spends it on a deeper conv front-end + a wider LSTM, with heavy dropout +
weight decay so the extra capacity doesn't just overfit the small fall pool
(falls come from only 14 young subjects in WEDA-FALL).

    input  (B, 125, 6)                 6 channels: ax,ay,az,wx,wy,wz @ 50 Hz
      │
      ├─ Conv1d(6→24,  k5, s2) → BN → ReLU → Dropout   ↓ time → 63
      ├─ Conv1d(24→48, k3, s2) → BN → ReLU → Dropout   ↓ time → 32
      ├─ Conv1d(48→64, k3, s1) → BN → ReLU → Dropout      time   32   (new block)
      │
      ├─ LSTM(64→64, 1 layer)            temporal dynamics over the 32 steps
      │      └─ last timestep's hidden state (64,)
      │
      ├─ Dropout(head)
      └─ Linear(64→1)                    single logit: P(pre-impact)

Still CNN→LSTM, not a true ConvLSTM2D: the input is a 1D 6-channel series, so a
conv front-end + recurrent head captures the same local-then-sequential
structure at a fraction of the ops. Default config is ~47 k params ≈ ~46 KB at
INT8 — comfortably inside the 80 KB budget with room to spare.

The edge model consumes the RAW 6-channel window (no engineered features) — the
conv front-end learns its own, and feature extraction wouldn't fit the budget.
The 43-dim engineered vector is the *cloud* model's input (features/extraction.py).
"""
from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

# Window contract (must match features/windowing.py).
N_CHANNELS = 6        # ax, ay, az, wx, wy, wz
WINDOW_SAMPLES = 125  # 2.5 s @ 50 Hz


@dataclass(frozen=True)
class ConvLSTMTinyConfig:
    """Hyperparameters for ConvLSTM-tiny. Frozen so a run is reproducible from it."""

    n_channels: int = N_CHANNELS
    window_samples: int = WINDOW_SAMPLES

    conv1_out: int = 24
    conv1_kernel: int = 5
    conv1_stride: int = 2

    conv2_out: int = 48
    conv2_kernel: int = 3
    conv2_stride: int = 2

    conv3_out: int = 64
    conv3_kernel: int = 3
    conv3_stride: int = 1

    lstm_hidden: int = 64
    lstm_layers: int = 1

    # Heavy regularisation — the extra capacity must not overfit the small,
    # 14-subject fall pool. conv_dropout hits every conv block; head_dropout
    # hits the LSTM output before the classifier.
    conv_dropout: float = 0.3
    head_dropout: float = 0.4


class ConvLSTMTiny(nn.Module):
    """Compact CNN→LSTM binary classifier for pre-impact prediction.

    Outputs a single raw logit per window (use BCEWithLogitsLoss in training,
    sigmoid at inference). Static input shape (B, 125, 6) so the graph converts
    cleanly to a fixed-shape TFLite Micro model.
    """

    def __init__(self, config: ConvLSTMTinyConfig | None = None) -> None:
        super().__init__()
        cfg = config or ConvLSTMTinyConfig()
        self.config = cfg

        def conv_block(c_in: int, c_out: int, k: int, s: int) -> nn.Sequential:
            return nn.Sequential(
                nn.Conv1d(c_in, c_out, kernel_size=k, stride=s, padding=k // 2),
                nn.BatchNorm1d(c_out),
                nn.ReLU(inplace=True),
                nn.Dropout(cfg.conv_dropout),
            )

        self.conv = nn.Sequential(
            conv_block(cfg.n_channels, cfg.conv1_out, cfg.conv1_kernel, cfg.conv1_stride),
            conv_block(cfg.conv1_out, cfg.conv2_out, cfg.conv2_kernel, cfg.conv2_stride),
            conv_block(cfg.conv2_out, cfg.conv3_out, cfg.conv3_kernel, cfg.conv3_stride),
        )

        self.lstm = nn.LSTM(
            input_size=cfg.conv3_out,
            hidden_size=cfg.lstm_hidden,
            num_layers=cfg.lstm_layers,
            batch_first=True,
        )

        self.head_dropout = nn.Dropout(cfg.head_dropout)
        self.head = nn.Linear(cfg.lstm_hidden, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (B, window_samples, n_channels) → logits: (B,)."""
        # Conv1d expects (B, C, T); the window arrives as (B, T, C).
        x = x.transpose(1, 2)               # (B, C, T)
        x = self.conv(x)                    # (B, conv3_out, T')
        x = x.transpose(1, 2)               # (B, T', conv3_out) for the LSTM
        out, _ = self.lstm(x)               # (B, T', hidden)
        last = out[:, -1, :]                # last timestep's hidden state
        last = self.head_dropout(last)
        logit = self.head(last)             # (B, 1)
        return logit.squeeze(-1)            # (B,)

    @torch.no_grad()
    def predict_proba(self, x: torch.Tensor) -> torch.Tensor:
        """Sigmoid probabilities, for evaluation / threshold sweeps."""
        return torch.sigmoid(self.forward(x))


def count_parameters(model: nn.Module) -> int:
    """Total trainable parameter count — a proxy for the INT8 footprint (~1 B/param)."""
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def build_model(config: ConvLSTMTinyConfig | None = None) -> ConvLSTMTiny:
    """Factory used by the training script and the quantizer."""
    return ConvLSTMTiny(config)
