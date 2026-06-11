"""Phase 31: round-trip validation — INT8 .tflite vs the FP32 PyTorch checkpoint.

Before the model goes anywhere near hardware, prove the quantized artifact still
behaves like the model we measured (96.5% recall, BUILD_LOG Phase 14). Both
models score the SAME standardized windows; we compare:

  • probability agreement — max / mean |sigmoid_fp32 − sigmoid_int8|;
  • DECISION agreement at the served threshold — the metric that actually
    matters: an INT8 wobble of 0.03 in probability is irrelevant unless it
    flips a window across FG_EDGE_THRESHOLD.

Gates (override via flags): decision agreement ≥ 99%, mean |Δp| ≤ 0.02,
max |Δp| ≤ --tol (default 0.05). The PLAN's "±0.01" aspiration is checked and
reported but not gated — full-INT8 PTQ on an LSTM rarely holds 0.01 worst-case,
and chasing it would mean dropping to dynamic-range quant (bigger + slower).

Runs under Linux/WSL/Docker/Colab (interpreter: ai-edge-litert, falling back to
tensorflow.lite). This validates against TFLite's REFERENCE kernels — the same
kernels TFLite Micro uses — so on-device output should match bit-for-bit; the
final hardware check happens once the device exists (PLAN Phase 31 DoD).

    python scripts/validate_tflite.py                 # real WEDA windows
    python scripts/validate_tflite.py --synthetic     # no dataset needed
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

ML_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EDGE_DIR = Path(os.environ.get("FG_EDGE_ARTIFACT_DIR") or ML_ROOT / "artifacts" / "edge")
DEFAULT_WEDA_ROOT = Path(os.environ.get("FG_WEDA_ROOT")
                         or ML_ROOT / "data" / "raw" / "WEDA-FALL-main")


def _make_interpreter(tflite_path: Path):
    """ai-edge-litert is the supported 2026 runtime; tensorflow.lite as fallback."""
    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:
        try:
            from tensorflow.lite import Interpreter  # type: ignore[no-redef]
        except ImportError:
            raise SystemExit(
                "Neither ai-edge-litert nor tensorflow is installed (and neither has "
                "a native-Windows wheel) — run under Linux/WSL/Docker/Colab."
            )
    interp = Interpreter(model_path=str(tflite_path))
    interp.allocate_tensors()
    return interp


def _tflite_probs(interp, X_std: np.ndarray) -> np.ndarray:
    """Run standardized windows through the INT8 graph; return sigmoid probabilities.

    Handles the INT8 input/output quantization explicitly: standardize → quantize
    with the input tensor's (scale, zero_point) — exactly what inference.cpp does
    on-device — then dequantize the logit and sigmoid it.
    """
    inp = interp.get_input_details()[0]
    out = interp.get_output_details()[0]
    probs = np.empty(len(X_std), dtype=np.float64)
    for i in range(len(X_std)):
        x = X_std[i: i + 1].astype(np.float32)
        if inp["dtype"] == np.int8:
            scale, zp = inp["quantization"]
            x = np.clip(np.round(x / scale + zp), -128, 127).astype(np.int8)
        interp.set_tensor(inp["index"], x)
        interp.invoke()
        y = interp.get_tensor(out["index"]).astype(np.float64).reshape(-1)[0]
        if out["dtype"] == np.int8:
            scale, zp = out["quantization"]
            y = (y - zp) * scale
        probs[i] = 1.0 / (1.0 + np.exp(-y))
    return probs


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Validate the INT8 .tflite against the FP32 checkpoint.")
    p.add_argument("--tflite", type=Path, default=DEFAULT_EDGE_DIR / "convlstm_tiny_int8.tflite")
    p.add_argument("--ckpt", type=Path, default=DEFAULT_EDGE_DIR / "convlstm_tiny_fp32.pt")
    p.add_argument("--channel-stats", type=Path, default=DEFAULT_EDGE_DIR / "channel_stats.json")
    p.add_argument("--weda-root", type=Path, default=DEFAULT_WEDA_ROOT)
    p.add_argument("--synthetic", action="store_true", help="validate on synthetic windows")
    p.add_argument("--n-windows", type=int, default=500)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--tol", type=float, default=0.05, help="max |Δprob| gate")
    p.add_argument("--mean-tol", type=float, default=0.02, help="mean |Δprob| gate")
    p.add_argument("--min-agreement", type=float, default=0.99,
                   help="decision-agreement gate at the served threshold")
    args = p.parse_args(argv)

    import torch

    from fall_guardian_ml.datasets.edge_dataset import (
        ChannelStats,
        build_edge_bundle,
        make_synthetic_bundle,
    )
    from fall_guardian_ml.models.convlstm_tiny import ConvLSTMTinyConfig, build_model

    # The exact windows + standardization the model serves on.
    bundle = make_synthetic_bundle() if args.synthetic else build_edge_bundle(args.weda_root)
    stats_d = json.loads(args.channel_stats.read_text())
    stats = ChannelStats(mean=np.asarray(stats_d["mean"], np.float32),
                         std=np.asarray(stats_d["std"], np.float32))
    rng = np.random.default_rng(args.seed)
    idx = rng.choice(len(bundle.X), size=min(args.n_windows, len(bundle.X)), replace=False)
    X_std = stats.apply(bundle.X[idx])
    y = bundle.y[idx]

    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    threshold = float(ck["threshold"])
    model = build_model(ConvLSTMTinyConfig(**ck["model_config"]))
    model.load_state_dict(ck["state_dict"])
    model.eval()
    with torch.no_grad():
        p_fp32 = torch.sigmoid(model(torch.from_numpy(X_std).float())).numpy().astype(np.float64)

    p_int8 = _tflite_probs(_make_interpreter(args.tflite), X_std)

    delta = np.abs(p_fp32 - p_int8)
    agree = float(np.mean((p_fp32 >= threshold) == (p_int8 >= threshold)))
    pos = y.astype(bool)
    recall_fp32 = float(np.mean(p_fp32[pos] >= threshold)) if pos.any() else float("nan")
    recall_int8 = float(np.mean(p_int8[pos] >= threshold)) if pos.any() else float("nan")

    gates = {
        f"max |dp| <= {args.tol}": float(delta.max()) <= args.tol,
        f"mean |dp| <= {args.mean_tol}": float(delta.mean()) <= args.mean_tol,
        f"decision agreement >= {args.min_agreement}": agree >= args.min_agreement,
    }
    ok = all(gates.values())

    print("\n" + "=" * 64)
    print(f"  TFLITE ROUND-TRIP VALIDATION  ({len(X_std)} windows, "
          f"{'SYNTHETIC' if args.synthetic else 'WEDA-FALL'})")
    print("=" * 64)
    print(f"  threshold (served)  : {threshold:.4f}")
    print(f"  |dp|  max / mean    : {delta.max():.4f} / {delta.mean():.4f}   "
          f"(within ±0.01: {float(np.mean(delta <= 0.01)) * 100:.1f}% of windows)")
    print(f"  decision agreement  : {agree * 100:.2f}%")
    if pos.any():
        print(f"  recall fp32 / int8  : {recall_fp32:.3f} / {recall_int8:.3f}  "
              f"(drop {100 * (recall_fp32 - recall_int8):.1f} pp)")
    for name, passed in gates.items():
        print(f"  {'[PASS]' if passed else '[FAIL]'} {name}")
    print("=" * 64 + "\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
