"""Cloud detection service — the seam the Week-C Transformer drops into.

Architecture role (docs/ARCHITECTURE.md §2.3): the edge model is recall-first and
fires often (high FPR by design); this cloud detector is the PRECISION gate that
confirms or suppresses each edge trigger before a caregiver is alerted.

Right now the Transformer isn't trained, so `CloudDetector` runs in **stub mode**:
a transparent, clearly-labelled heuristic on the acceleration magnitude so the
backend is end-to-end testable today. When the model lands, `_load_model` reads
it from `settings.model_path` and `predict` swaps the heuristic for a real
forward pass — no API or schema change. The stub is never mistaken for the real
thing: responses carry `model_version` ("stub-0.0") so callers can tell.
"""
from __future__ import annotations

import math

from app.config import Settings
from app.schemas import InferenceRequest, InferenceResponse, Severity

# Impact magnitude heuristic (matches the ML pipeline's fall sanity threshold).
_IMPACT_MS2 = 20.0
_HIGH_MS2 = 30.0


class CloudDetector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model = self._load_model()

    def _load_model(self):
        """Load the trained detector if present; otherwise stay in stub mode."""
        if self.settings.model_path:
            # TODO(Week C): load the exported Transformer (torch / MLflow artifact).
            raise NotImplementedError("real model loading lands with the Week-C Transformer")
        return None  # stub mode

    @property
    def is_stub(self) -> bool:
        return self.model is None

    def predict(self, req: InferenceRequest) -> InferenceResponse:
        if self.model is None:
            return self._stub_predict(req)
        raise NotImplementedError  # real forward pass — Week C

    def _stub_predict(self, req: InferenceRequest) -> InferenceResponse:
        """Peak-acceleration-magnitude heuristic. Placeholder for the Transformer."""
        peak = max(
            math.sqrt(s.ax * s.ax + s.ay * s.ay + s.az * s.az) for s in req.samples
        )
        is_fall = peak >= _IMPACT_MS2
        # Crude confidence: how far past the impact threshold the peak sits.
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
