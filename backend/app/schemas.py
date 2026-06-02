"""Pydantic v2 schemas for the cloud gateway.

These mirror the single hardware-agnostic ingestion contract in
docs/ARCHITECTURE.md §8 — the exact JSON both the real ESP32-S3 firmware and the
Python virtual device POST to /v1/inference. Strict validation here is a
deliberate fix for v1/v2, which accepted unvalidated input with silent
`.get('x', 0)` defaults.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator

WINDOW_SAMPLES = 125  # 2.5 s @ 50 Hz — the locked window length


class IMUSample(BaseModel):
    """One IMU reading: 3-axis accel (m/s²) + 3-axis gyro (rad/s)."""

    ax: float
    ay: float
    az: float
    wx: float
    wy: float
    wz: float


class EdgePrediction(BaseModel):
    """The edge model's trigger, included only when the edge fired."""

    p_pre_impact: float = Field(ge=0.0, le=1.0)
    model_version: str


class InferenceRequest(BaseModel):
    """A 2.5 s window streamed to the cloud when the edge model fires."""

    device_id: str = Field(min_length=1)
    ts_start_unix_ms: int = Field(ge=0)
    sample_rate_hz: int = 50
    samples: list[IMUSample]
    edge_prediction: EdgePrediction | None = None

    @field_validator("samples")
    @classmethod
    def _exactly_one_window(cls, v: list[IMUSample]) -> list[IMUSample]:
        if len(v) != WINDOW_SAMPLES:
            raise ValueError(
                f"expected exactly {WINDOW_SAMPLES} samples (2.5 s @ 50 Hz), got {len(v)}"
            )
        return v


class Severity(str, Enum):
    none = "none"
    low = "low"
    medium = "medium"
    high = "high"


class InferenceResponse(BaseModel):
    """The cloud detector's verdict on a window."""

    is_fall: bool
    confidence: float = Field(ge=0.0, le=1.0)
    severity: Severity
    action: str                       # e.g. "alert_caregiver" | "suppress"
    lead_time_ms: float | None = None
    model_version: str


class HealthResponse(BaseModel):
    status: str
    version: str
    model_version: str
    environment: str
