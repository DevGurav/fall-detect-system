"""Phase 31: FP32 checkpoint → INT8 .tflite → firmware headers, in one command.

A thin orchestrator over the existing pieces (`fg-train quantize` produces the
same .tflite): `eval.quantize.quantize_to_int8_tflite` (ai-edge-torch PTQ,
calibrated on real WEDA windows) followed by `tflite_to_header` so a single
green run leaves edge/include/model.h + model_meta.h ready to compile.

LINUX-ONLY (write-now-run-later): ai-edge-torch / ai-edge-litert ship no
Windows wheels (see eval/quantize.py docstring). On Windows this script exits
early with the Docker one-liner. Run it from the repo root:

    docker run --rm -v "%cd%":/w -w /w/ml python:3.11-slim bash -c \\
        "pip install -e . ai-edge-torch && python scripts/export_tflite.py"

Colab also works (it's Linux) — same env-var seams as the Phase 30 scripts:

    FG_WEDA_ROOT           extracted WEDA-FALL-main (calibration windows)
    FG_EDGE_ARTIFACT_DIR   checkpoint + channel_stats dir (default ml/artifacts/edge)
    FG_FIRMWARE_INCLUDE_DIR  header output dir (default edge/include)

`--synthetic` calibrates INT8 ranges on synthetic windows — fine for a pipeline
smoke, NOT for the deployable artifact (real activation ranges matter for PTQ).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tflite_to_header import (
    DEFAULT_EDGE_DIR,
    DEFAULT_MODEL_VERSION,
    DEFAULT_OUT_DIR,
    emit_meta_header,
    emit_model_header,
)

ML_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEDA_ROOT = Path(os.environ.get("FG_WEDA_ROOT")
                         or ML_ROOT / "data" / "raw" / "WEDA-FALL-main")

# The INT8 artifact must fit the ESP32-S3 budget with headroom (MODEL_CARD §3.2).
SIZE_BUDGET_KB = 80.0


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Quantize the edge model and emit firmware headers.")
    p.add_argument("--out", type=Path, default=DEFAULT_EDGE_DIR / "convlstm_tiny_int8.tflite")
    p.add_argument("--n-calibration", type=int, default=200)
    p.add_argument("--synthetic", action="store_true",
                   help="calibrate on synthetic windows (pipeline smoke only)")
    p.add_argument("--no-headers", action="store_true",
                   help="produce only the .tflite, skip edge/include generation")
    p.add_argument("--weda-root", type=Path, default=DEFAULT_WEDA_ROOT)
    p.add_argument("--include-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument("--model-version", default=DEFAULT_MODEL_VERSION)
    args = p.parse_args(argv)

    if sys.platform == "win32":
        raise SystemExit(
            "ai-edge-torch has no native-Windows wheel. Run under Linux/WSL/Docker:\n"
            '  docker run --rm -v "%cd%":/w -w /w/ml python:3.11-slim bash -c '
            '"pip install -e . ai-edge-torch && python scripts/export_tflite.py"'
        )

    from fall_guardian_ml.datasets.edge_dataset import build_edge_bundle, make_synthetic_bundle
    from fall_guardian_ml.eval.quantize import quantize_to_int8_tflite

    bundle = make_synthetic_bundle() if args.synthetic else build_edge_bundle(args.weda_root)
    res = quantize_to_int8_tflite(
        ckpt_path=DEFAULT_EDGE_DIR / "convlstm_tiny_fp32.pt",
        channel_stats_path=DEFAULT_EDGE_DIR / "channel_stats.json",
        representative_X=bundle.X,
        out_path=args.out,
        n_calibration=args.n_calibration,
    )
    ok = res.size_kb <= SIZE_BUDGET_KB
    print(f"INT8 .tflite: {res.tflite_path} ({res.size_kb:.1f} KB) "
          f"budget <={SIZE_BUDGET_KB:.0f} KB {'[PASS]' if ok else '[FAIL]'}")

    if not args.no_headers:
        version = args.model_version + ("-synthetic" if args.synthetic else "")
        emit_model_header(res.tflite_path, args.include_dir / "model.h")
        emit_meta_header(DEFAULT_EDGE_DIR / "convlstm_tiny_fp32.pt",
                         DEFAULT_EDGE_DIR / "channel_stats.json",
                         args.include_dir / "model_meta.h", version)
        print(f"firmware headers written to {args.include_dir}")
        print("next: python scripts/validate_tflite.py   # round-trip check before flashing")


if __name__ == "__main__":
    main()
