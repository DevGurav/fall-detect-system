"""Post-Training Quantization: ConvLSTM-tiny (PyTorch FP32) → INT8 .tflite.

The edge model has to live in ESP32-S3 flash and run under TFLite Micro, which
means full INT8 (weights AND activations) — float models won't fit the size or
latency budget. We do *post-training* quantization (PTQ): train in FP32, then
calibrate the INT8 ranges on a small representative sample of real windows. No
retraining, no labels needed for the calibration pass.

Toolchain — `ai-edge-torch` (Google's PyTorch→LiteRT/TFLite converter, the
supported 2026 path). The recipe:

    1. torch.export the model with a static (1, 125, 6) input signature.
    2. PT2E quantize: insert observers, run the representative data through to
       calibrate min/max, then fold to INT8.
    3. ai_edge_torch.convert(...) with the PT2E QuantConfig → a .tflite flatbuffer.

IMPORTANT — environment requirements (verified 2026-05-31):
  • Python 3.9–3.12 only. TensorFlow ships no wheels for 3.13/3.14.
  • Linux or macOS only. `ai-edge-torch`, `ai-edge-litert` and `onnx2tf` publish
    wheels for manylinux + macOS arm64 ONLY — there is NO native-Windows wheel,
    and onnx2tf hard-imports `ai_edge_litert` at module load. So this `.tflite`
    export step must run under Linux / WSL2 / CI, not native Windows.

On Windows, run `fall_guardian_ml.eval.onnx_export` (the `fg-train export-onnx`
command) instead: it exports the ONNX intermediate and produces a real INT8
size/latency cross-check via ONNX Runtime while the `.tflite` waits for a Linux
run. The ONNX it writes is also step 1 of this pipeline.

A known risk to validate on first run: TFLite Micro's INT8 LSTM support is via
the fused UnidirectionalSequenceLSTM op. If conversion of the LSTM layer is
rejected, the fallback is to (a) keep the LSTM in dynamic-range INT8 while the
conv front-end is full-INT8, or (b) swap the LSTM for a GRU/temporal-conv head.
That decision belongs to the first real conversion run, not to a guess here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from fall_guardian_ml.datasets.edge_dataset import ChannelStats
from fall_guardian_ml.models.convlstm_tiny import ConvLSTMTinyConfig, build_model


@dataclass
class QuantResult:
    tflite_path: Path
    size_bytes: int

    @property
    def size_kb(self) -> float:
        return self.size_bytes / 1024.0


def _load_checkpoint(ckpt_path: Path):
    import torch

    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    cfg = ConvLSTMTinyConfig(**ckpt["model_config"])
    model = build_model(cfg)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model, cfg


def _representative_windows(
    X: np.ndarray, stats: ChannelStats, n: int = 200, seed: int = 0
) -> np.ndarray:
    """Standardized sample of real windows for INT8 range calibration."""
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(X), size=min(n, len(X)), replace=False)
    return stats.apply(X[idx])


def quantize_to_int8_tflite(
    ckpt_path: Path,
    channel_stats_path: Path,
    representative_X: np.ndarray,
    out_path: Path,
    n_calibration: int = 200,
) -> QuantResult:
    """Convert the trained FP32 checkpoint to an INT8 .tflite via ai-edge-torch PTQ.

    Parameters
    ----------
    ckpt_path : the FP32 checkpoint saved by train_edge.py.
    channel_stats_path : channel_stats.json (the standardization the model expects).
    representative_X : RAW (N, 125, 6) windows for calibration (standardized here).
    out_path : where to write the .tflite.
    """
    import torch

    import ai_edge_torch
    from ai_edge_torch.quantize.pt2e_quantizer import (
        PT2EQuantizer,
        get_symmetric_quantization_config,
    )
    from ai_edge_torch.quantize.quant_config import QuantConfig
    from torch.ao.quantization.quantize_pt2e import convert_pt2e, prepare_pt2e

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    model, _ = _load_checkpoint(ckpt_path)
    stats_d = json.loads(Path(channel_stats_path).read_text())
    stats = ChannelStats(
        mean=np.asarray(stats_d["mean"], np.float32),
        std=np.asarray(stats_d["std"], np.float32),
    )
    calib = _representative_windows(representative_X, stats, n_calibration)

    sample_input = (torch.from_numpy(calib[:1]).float(),)

    # 1. Capture a training-style exported graph for PT2E.
    exported = torch.export.export_for_training(model, sample_input).module()

    # 2. Prepare with a symmetric full-INT8 config, calibrate, convert.
    quantizer = PT2EQuantizer().set_global(
        get_symmetric_quantization_config(is_per_channel=True)
    )
    prepared = prepare_pt2e(exported, quantizer)
    with torch.no_grad():
        for i in range(len(calib)):
            prepared(torch.from_numpy(calib[i : i + 1]).float())
    quantized = convert_pt2e(prepared)

    # 3. Convert to a .tflite flatbuffer with the INT8 quant config.
    edge_model = ai_edge_torch.convert(
        quantized,
        sample_input,
        quant_config=QuantConfig(pt2e_quantizer=quantizer),
    )
    edge_model.export(str(out_path))

    size = out_path.stat().st_size
    return QuantResult(tflite_path=out_path, size_bytes=size)
