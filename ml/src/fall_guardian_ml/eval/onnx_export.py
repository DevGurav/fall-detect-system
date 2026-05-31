"""ONNX export + INT8 cross-check — the Windows-runnable edge-export path.

The production deployable artifact is a TFLite-Micro INT8 `.tflite` produced by
ai-edge-torch (see quantize.py) — but that toolchain (ai-edge-torch, onnx2tf,
ai-edge-litert) ships wheels for Linux/macOS ONLY, so it cannot run on native
Windows. This module is the part of edge export that *does* run on Windows:

  • Export the trained PyTorch model to ONNX (legacy exporter → clean static
    LSTM graph that downstream converters and runtimes accept).
  • Dynamic-INT8-quantize it with ONNX Runtime (robust for LSTM; no calibration
    set required) — a real, measured INT8 footprint + latency.

Use it for two things:
  1. A reproducible INT8 size/latency CROSS-CHECK while the .tflite export is
     pending a Linux/WSL/CI run — confirms the INT8 footprint lands in range.
  2. The ONNX intermediate is exactly what the Linux converters consume, so this
     is also step 1 of the production pipeline.

This is a cross-check, NOT the on-device artifact. The ESP32-S3 figure is only
knowable once the .tflite is flashed to hardware (Week F).
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from fall_guardian_ml.models.convlstm_tiny import ConvLSTMTinyConfig, build_model


def export_fp32_onnx(ckpt_path: Path, out_path: Path, opset: int = 17) -> Path:
    """Export the trained FP32 checkpoint to ONNX with a static (1,125,6) input.

    Uses the legacy exporter (`dynamo=False`): it emits a clean, statically-shaped
    LSTM subgraph that ONNX Runtime's quantizer and the Linux TFLite converters
    both accept, whereas the dynamo path can leave a shape-inference snag.
    """
    import torch

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ck = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    model = build_model(ConvLSTMTinyConfig(**ck["model_config"]))
    model.load_state_dict(ck["state_dict"])
    model.eval()

    torch.onnx.export(
        model,
        torch.randn(1, 125, 6),
        str(out_path),
        input_names=["window"],
        output_names=["logit"],
        opset_version=opset,
        dynamo=False,
    )
    return out_path


def quantize_onnx_int8(fp32_onnx: Path, out_path: Path) -> Path:
    """Dynamic-range INT8 quantization of the ONNX model via ONNX Runtime."""
    from onnxruntime.quantization import QuantType, quantize_dynamic

    out_path = Path(out_path)
    quantize_dynamic(
        model_input=str(fp32_onnx),
        model_output=str(out_path),
        weight_type=QuantType.QInt8,
    )
    return out_path


@dataclass
class OnnxBenchResult:
    fp32_size_kb: float
    int8_size_kb: float
    int8_mean_ms: float
    int8_median_ms: float
    int8_p95_ms: float

    def as_flat_dict(self) -> dict[str, float]:
        return {
            "onnx_fp32_size_kb": self.fp32_size_kb,
            "onnx_int8_size_kb": self.int8_size_kb,
            "onnx_int8_latency_mean_ms": self.int8_mean_ms,
            "onnx_int8_latency_p95_ms": self.int8_p95_ms,
        }


def benchmark_onnx_int8(
    int8_onnx: Path, n_runs: int = 200, warmup: int = 20, seed: int = 0
) -> tuple[float, float, float]:
    """Single-window latency of the INT8 ONNX model on CPU (mean, median, p95 ms)."""
    import onnxruntime as ort

    sess = ort.InferenceSession(str(int8_onnx), providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((1, 125, 6)).astype(np.float32)

    for _ in range(warmup):
        sess.run(None, {name: x})
    times = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        sess.run(None, {name: x})
        times.append((time.perf_counter() - t0) * 1000.0)
    times.sort()
    return (
        float(statistics.mean(times)),
        float(statistics.median(times)),
        float(times[int(0.95 * (n_runs - 1))]),
    )


def run_onnx_crosscheck(
    ckpt_path: Path, artifact_dir: Path, n_runs: int = 200
) -> OnnxBenchResult:
    """Export → INT8-quantize → benchmark, all on Windows. Returns the result."""
    artifact_dir = Path(artifact_dir)
    fp32_onnx = export_fp32_onnx(ckpt_path, artifact_dir / "convlstm_tiny.onnx")
    int8_onnx = quantize_onnx_int8(fp32_onnx, artifact_dir / "convlstm_tiny_int8.onnx")
    mean_ms, median_ms, p95_ms = benchmark_onnx_int8(int8_onnx, n_runs=n_runs)
    return OnnxBenchResult(
        fp32_size_kb=fp32_onnx.stat().st_size / 1024,
        int8_size_kb=int8_onnx.stat().st_size / 1024,
        int8_mean_ms=mean_ms,
        int8_median_ms=median_ms,
        int8_p95_ms=p95_ms,
    )


def print_onnx_crosscheck(result: OnnxBenchResult, size_target_kb: float = 80.0) -> None:
    ok = "[PASS]" if result.int8_size_kb <= size_target_kb else "[FAIL]"
    print("\n" + "=" * 60)
    print("  EDGE INT8 CROSS-CHECK  (ONNX Runtime, Windows)")
    print("=" * 60)
    print(f"  FP32 onnx    : {result.fp32_size_kb:6.1f} KB")
    print(f"  INT8 onnx    : {result.int8_size_kb:6.1f} KB  target <={size_target_kb:.0f} KB {ok}")
    print(f"  INT8 latency : {result.int8_mean_ms:6.3f} ms mean (desktop CPU, batch=1)")
    print(f"  INT8 p95     : {result.int8_p95_ms:6.3f} ms")
    print("  Note: cross-check only. Deployable .tflite (ai-edge-torch) runs on")
    print("        Linux/WSL; ESP32-S3 latency is measured on hardware (Week F).")
    print("=" * 60 + "\n")
