"""Benchmark an INT8 .tflite edge model: file size + inference latency.

Two numbers we report against the Week-B targets:

  • Size  — the .tflite flatbuffer size on disk (target ≤ 80 KB). This is the
            real number that has to fit ESP32-S3 flash; no estimation needed.
  • Latency — single-window inference time. We measure it with the LiteRT
            interpreter on THIS machine's CPU and clearly label it a desktop
            proxy. The on-device ESP32-S3 figure differs (240 MHz Xtensa LX7
            vs. a desktop core) and is only knowable once flashed to hardware —
            we never pass the desktop number off as the ESP32 number.

The desktop CPU figure is still useful: it catches "this graph is accidentally
huge" regressions early, and gives a same-machine before/after for any future
optimisation.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class BenchmarkResult:
    size_bytes: int
    n_runs: int
    mean_ms: float
    median_ms: float
    p95_ms: float
    min_ms: float
    device: str = "desktop-cpu"

    @property
    def size_kb(self) -> float:
        return self.size_bytes / 1024.0

    def as_flat_dict(self) -> dict[str, float]:
        return {
            "tflite_size_kb": self.size_kb,
            "latency_mean_ms": self.mean_ms,
            "latency_median_ms": self.median_ms,
            "latency_p95_ms": self.p95_ms,
            "latency_min_ms": self.min_ms,
        }


def _load_interpreter(tflite_path: Path):
    """Prefer the standalone LiteRT runtime; fall back to tf.lite."""
    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:  # older stacks
        from tensorflow.lite import Interpreter  # type: ignore
    interp = Interpreter(model_path=str(tflite_path))
    interp.allocate_tensors()
    return interp


def benchmark_tflite(
    tflite_path: Path,
    n_runs: int = 200,
    warmup: int = 20,
    seed: int = 0,
) -> BenchmarkResult:
    """Time single-window inference on the given INT8 .tflite model.

    Builds a random input matching the model's INT8 input quantization so the
    timing reflects the real quantized path, not a float path.
    """
    tflite_path = Path(tflite_path)
    size = tflite_path.stat().st_size

    interp = _load_interpreter(tflite_path)
    in_detail = interp.get_input_details()[0]
    out_index = interp.get_output_details()[0]["index"]
    in_index = in_detail["index"]
    shape = in_detail["shape"]
    dtype = in_detail["dtype"]

    rng = np.random.default_rng(seed)
    if np.issubdtype(dtype, np.integer):
        info = np.iinfo(dtype)
        sample = rng.integers(info.min, info.max + 1, size=shape, dtype=dtype)
    else:
        sample = rng.standard_normal(size=shape).astype(dtype)

    for _ in range(warmup):
        interp.set_tensor(in_index, sample)
        interp.invoke()
        interp.get_tensor(out_index)

    times_ms: list[float] = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        interp.set_tensor(in_index, sample)
        interp.invoke()
        interp.get_tensor(out_index)
        times_ms.append((time.perf_counter() - t0) * 1000.0)

    times_ms.sort()
    return BenchmarkResult(
        size_bytes=size,
        n_runs=n_runs,
        mean_ms=float(statistics.mean(times_ms)),
        median_ms=float(statistics.median(times_ms)),
        p95_ms=float(times_ms[int(0.95 * (n_runs - 1))]),
        min_ms=float(times_ms[0]),
    )


def print_benchmark(result: BenchmarkResult, size_target_kb: float = 80.0) -> None:
    ok = "[PASS]" if result.size_kb <= size_target_kb else "[FAIL]"
    print("\n" + "=" * 60)
    print("  EDGE MODEL BENCHMARK")
    print("=" * 60)
    print(f"  .tflite size : {result.size_kb:6.1f} KB  target <={size_target_kb:.0f} KB {ok}")
    print(f"  Latency mean : {result.mean_ms:6.2f} ms  ({result.device})")
    print(f"  Latency p95  : {result.p95_ms:6.2f} ms")
    print(f"  Latency min  : {result.min_ms:6.2f} ms  over {result.n_runs} runs")
    print("  Note: latency is a DESKTOP-CPU proxy. The ESP32-S3 figure is only")
    print("        knowable once flashed to hardware (Week F).")
    print("=" * 60 + "\n")
