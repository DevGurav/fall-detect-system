"""Export the trained cloud detector to ONNX + a serving meta sidecar for the backend.

Writes into backend/app/model/:
  • cloud_detector.onnx        — the fused-input Transformer (raw 125x6 + 43-d features)
  • cloud_detector.meta.json   — threshold, Platt calibrator, channel + feature
                                 normalisers, severity scaler/cuts, model_version

This lets the torch-free FastAPI gateway serve the model via onnxruntime + numpy
(ARCHITECTURE §2.3: the model is a portable artifact the gateway loads). Run after
training the cloud detector:

    python scripts/export_cloud_onnx.py
"""
from __future__ import annotations

import json

import torch

from fall_guardian_ml.models.transformer_detector import (
    TransformerDetectorConfig,
    build_model,
)
from fall_guardian_ml.training.train_cloud import DEFAULT_ARTIFACT_DIR

ML_ROOT = DEFAULT_ARTIFACT_DIR.parents[1]
OUT_DIR = ML_ROOT.parent / "backend" / "app" / "model"


class _ExportWrap(torch.nn.Module):
    """ONNX needs tensor outputs, not the DetectorOutput dataclass."""

    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(self, raw, feats):
        out = self.model(raw, feats)
        return out.fall_logit, out.severity


def main() -> None:
    ckpt_path = DEFAULT_ARTIFACT_DIR / "transformer_detector_fp32.pt"
    ck = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    cfg = TransformerDetectorConfig(**ck["model_config"])
    model = build_model(cfg)
    model.load_state_dict(ck["state_dict"])
    model.eval()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    onnx_path = OUT_DIR / "cloud_detector.onnx"
    dummy_raw = torch.randn(1, cfg.window_samples, cfg.n_channels)
    dummy_feats = torch.randn(1, cfg.n_features)
    torch.onnx.export(
        _ExportWrap(model).eval(), (dummy_raw, dummy_feats), str(onnx_path),
        input_names=["raw", "feats"], output_names=["fall_logit", "severity"],
        dynamic_axes={"raw": {0: "b"}, "feats": {0: "b"},
                      "fall_logit": {0: "b"}, "severity": {0: "b"}},
        opset_version=18,
    )

    # The dynamo exporter writes weights to a sidecar .onnx.data file; consolidate
    # into a single self-contained .onnx so the backend ships one artifact.
    import onnx

    onnx.save_model(onnx.load(str(onnx_path)), str(onnx_path), save_as_external_data=False)
    sidecar = onnx_path.with_suffix(".onnx.data")
    if sidecar.exists():
        sidecar.unlink()

    meta = {
        "model_version": ck["model_version"],
        "threshold": float(ck["threshold"]),          # operates on the calibrated probability
        "platt": ck["platt"],                          # {coef, intercept} or null
        "channel_stats": ck["channel_stats"],          # raw 6-ch standardiser (mean/std)
        "feature_norm": ck["feature_norm_global"],     # global 43-d z-score (serving fallback)
        "severity_scaler": ck["severity_scaler"],      # {mean, std} to un-standardize peak |a|
        "severity_cuts_ms2": ck["severity_cuts_ms2"],  # {medium, high}
        "n_channels": cfg.n_channels,
        "n_features": cfg.n_features,
        "window_samples": cfg.window_samples,
    }
    (OUT_DIR / "cloud_detector.meta.json").write_text(json.dumps(meta, indent=2))
    print(f"wrote {onnx_path} ({onnx_path.stat().st_size / 1024:.0f} KB)")
    print(f"wrote {OUT_DIR / 'cloud_detector.meta.json'}  (version {meta['model_version']}, "
          f"threshold {meta['threshold']:.4f})")


if __name__ == "__main__":
    main()
