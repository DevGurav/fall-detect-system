"""Cloud detection service — the precision gate behind the recall-first edge model.

Architecture role (docs/ARCHITECTURE.md §2.3): the edge model is recall-first and
fires often (high FPR by design); this cloud detector confirms or suppresses each
edge trigger before a caregiver is alerted. Validated end-to-end: in the
edge→cloud cascade the joint ADL false-positive rate is ~0.7% (29× below the edge
alone) — see BUILD_LOG Phase 20.

The trained **Transformer detector** is served as a portable ONNX artifact
(`app/model/cloud_detector.onnx` + `.meta.json`) so this gateway stays torch-free:
onnxruntime runs the graph, numpy does the preprocessing. The model takes two
fused inputs — the raw 125×6 window and the 43-d engineered feature vector
(`services/features.py`, computed identically to training) — and emits a fall
logit + a (standardized) severity scalar.

If the model artifact is absent, the detector falls back to **stub mode**: a
transparent peak-acceleration heuristic, so the backend stays end-to-end testable
without the model file. Responses always carry `model_version` so a stub
("stub-0.0") is never mistaken for the real model ("cloud-transformer-v0.x").
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from app.config import Settings
from app.schemas import InferenceRequest, InferenceResponse, Severity
from app.services.features import extract_features

# Default location of the exported model + its serving metadata (committed).
_MODEL_DIR = Path(__file__).resolve().parent.parent / "model"
_DEFAULT_MODEL_PATH = _MODEL_DIR / "cloud_detector.onnx"

# Stub heuristic thresholds (matches the ML pipeline's fall sanity threshold).
_IMPACT_MS2 = 20.0
_HIGH_MS2 = 30.0


class CloudDetector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._session = None
        self._meta: dict | None = None
        self._load_model()

    def _resolve_model_path(self) -> Path:
        return Path(self.settings.model_path) if self.settings.model_path else _DEFAULT_MODEL_PATH

    def _load_model(self) -> None:
        """Load the exported ONNX detector if present; otherwise stay in stub mode."""
        path = self._resolve_model_path()
        meta_path = path.with_suffix(".meta.json")
        if not (path.exists() and meta_path.exists()):
            return  # stub mode
        import onnxruntime as ort

        self._session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self._meta = json.loads(meta_path.read_text())

    @property
    def is_stub(self) -> bool:
        return self._session is None

    @property
    def model_version(self) -> str:
        return self._meta["model_version"] if self._meta else self.settings.model_version

    def predict(self, req: InferenceRequest) -> InferenceResponse:
        if self._session is None:
            return self._stub_predict(req)
        return self._model_predict(req)

    # ─── Real model (ONNX) ───────────────────────────────────────────────────

    def _model_predict(self, req: InferenceRequest) -> InferenceResponse:
        meta = self._meta
        window = np.array(
            [[s.ax, s.ay, s.az, s.wx, s.wy, s.wz] for s in req.samples], dtype=np.float32
        )  # (125, 6) — schema guarantees exactly 125 samples

        raw = self._standardize(window, meta["channel_stats"])[None, :, :]          # (1, 125, 6)
        feats = extract_features(window, sample_rate=req.sample_rate_hz)
        feat = self._standardize(feats, meta["feature_norm"])[None, :]              # (1, 43)

        logit, severity_std = self._session.run(None, {"raw": raw, "feats": feat})
        prob = self._calibrated_prob(float(logit[0]))
        is_fall = prob >= float(meta["threshold"])
        peak_ms2 = float(severity_std[0]) * meta["severity_scaler"]["std"] + meta["severity_scaler"]["mean"]

        return InferenceResponse(
            is_fall=is_fall,
            confidence=max(0.0, min(1.0, prob)),
            severity=self._severity(is_fall, peak_ms2),
            action="alert_caregiver" if is_fall else "suppress",
            lead_time_ms=None,
            model_version=meta["model_version"],
        )

    @staticmethod
    def _standardize(x: np.ndarray, stats: dict) -> np.ndarray:
        mean = np.asarray(stats["mean"], dtype=np.float32)
        std = np.asarray(stats["std"], dtype=np.float32)
        return ((x - mean) / np.where(std > 0, std, 1.0)).astype(np.float32)

    def _calibrated_prob(self, logit: float) -> float:
        platt = self._meta.get("platt")
        z = (platt["coef"] * logit + platt["intercept"]) if platt else logit
        return 1.0 / (1.0 + math.exp(-z))

    def _severity(self, is_fall: bool, peak_ms2: float) -> Severity:
        cuts = self._meta["severity_cuts_ms2"]
        if not is_fall:
            return Severity.none
        if peak_ms2 >= cuts["high"]:
            return Severity.high
        if peak_ms2 >= cuts["medium"]:
            return Severity.medium
        return Severity.low

    # ─── Stub fallback (no model artifact present) ────────────────────────────

    def _stub_predict(self, req: InferenceRequest) -> InferenceResponse:
        """Peak-acceleration-magnitude heuristic. Used only when no model is loaded."""
        peak = max(
            math.sqrt(s.ax * s.ax + s.ay * s.ay + s.az * s.az) for s in req.samples
        )
        is_fall = peak >= _IMPACT_MS2
        confidence = max(0.0, min(1.0, (peak - _IMPACT_MS2) / (_HIGH_MS2 - _IMPACT_MS2)))
        if not is_fall:
            severity = Severity.none
        elif peak >= _HIGH_MS2:
            severity = Severity.high
        else:
            severity = Severity.medium
        return InferenceResponse(
            is_fall=is_fall,
            confidence=confidence if is_fall else 0.0,
            severity=severity,
            action="alert_caregiver" if is_fall else "suppress",
            lead_time_ms=None,
            model_version=self.settings.model_version,
        )
