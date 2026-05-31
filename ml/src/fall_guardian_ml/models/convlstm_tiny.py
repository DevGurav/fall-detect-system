"""ConvLSTM-tiny — the edge pre-impact prediction model.

This is the model that runs on the ESP32-S3 wrist wearable under TFLite Micro.
Its job: looking at the most recent 2.5 s (125-sample) raw IMU window, decide
whether a fall impact is *about to* happen (the PRE_IMPACT phase is sitting at
the tail of the window). It must fit in ≤80 KB INT8 and run in <80 ms.

Design — a compact 1D-CNN feature extractor feeding a small LSTM:

    input  (B, 125, 6)            6 channels: ax, ay, az, wx, wy, wz @ 50 Hz
      │
      ├─ Conv1d(6→16, k=5, s=2) + BN + ReLU      local motion features, ↓ time → 61
      ├─ Conv1d(16→32, k=3, s=2) + BN + ReLU     deeper features,        ↓ time → 30
      │
      ├─ LSTM(32→32, 1 layer)                    temporal dynamics over the 30 steps
      │      └─ take the last timestep's hidden state (32,)
      │
      └─ Linear(32→1)                            single logit: P(pre-impact)

Why CNN→LSTM rather than a true ConvLSTM2D (Shi et al. 2015): the input is a 1D
time series of 6 channels, not a spatiotemporal grid, so a 1D conv front-end +
LSTM captures the same "local-then-sequential" structure at a fraction of the
parameter and op cost — which is what "tiny" has to mean to fit TFLite Micro.

Parameter budget (default config): ~10.4 k params ≈ ~10 KB at INT8 — comfortably
under the 80 KB target, leaving headroom to grow capacity if recall demands it.

The edge model deliberately consumes the RAW 6-channel window (no engineered
features) — feature extraction would not fit the flash/latency budget, and the
conv front-end learns its own features anyway. The 43-dim engineered vector is
the *cloud* model's input (see features/extraction.py).
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

    conv1_out: int = 16
    conv1_kernel: int = 5
    conv1_stride: int = 2

    conv2_out: int = 32
    conv2_kernel: int = 3
    conv2_stride: int = 2

    lstm_hidden: int = 32
    lstm_layers: int = 1

    dropout: float = 0.2


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

        self.conv = nn.Sequential(
            nn.Conv1d(
                cfg.n_channels,
                cfg.conv1_out,
                kernel_size=cfg.conv1_kernel,
                stride=cfg.conv1_stride,
                padding=cfg.conv1_kernel // 2,
            ),
            nn.BatchNorm1d(cfg.conv1_out),
            nn.ReLU(inplace=True),
            nn.Conv1d(
                cfg.conv1_out,
                cfg.conv2_out,
                kernel_size=cfg.conv2_kernel,
                stride=cfg.conv2_stride,
                padding=cfg.conv2_kernel // 2,
            ),
            nn.BatchNorm1d(cfg.conv2_out),
            nn.ReLU(inplace=True),
        )

        self.lstm = nn.LSTM(
            input_size=cfg.conv2_out,
            hidden_size=cfg.lstm_hidden,
            num_layers=cfg.lstm_layers,
            batch_first=True,
        )

        self.dropout = nn.Dropout(cfg.dropout)
        self.head = nn.Linear(cfg.lstm_hidden, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (B, window_samples, n_channels) → logits: (B,)."""
        # Conv1d expects (B, C, T); the window arrives as (B, T, C).
        x = x.transpose(1, 2)               # (B, C, T)
        x = self.conv(x)                    # (B, conv2_out, T')
        x = x.transpose(1, 2)               # (B, T', conv2_out) for the LSTM
        out, _ = self.lstm(x)               # (B, T', hidden)
        last = out[:, -1, :]                # last timestep's hidden state
        last = self.dropout(last)
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
