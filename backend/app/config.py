"""Application settings (12-factor: env-overridable, sane defaults for local dev)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FG_", env_file=".env", extra="ignore")

    app_name: str = "fall-guardian-backend"
    environment: str = "local"  # local | staging | prod

    # The locked window contract (must match the ML pipeline + edge firmware).
    window_samples: int = 125          # 2.5 s @ 50 Hz
    sample_rate_hz: int = 50

    # Cloud detection model. Served from the exported ONNX artifact by default
    # (app/model/cloud_detector.onnx); `model_version` is only the fallback label
    # the stub reports when no artifact is present (see services/detector.py).
    model_path: str | None = None
    model_version: str = "stub-0.0"

    # Confidence threshold above which the cloud CONFIRMS a fall.
    fall_confidence_threshold: float = 0.5

    # A device is reported "offline" if its last heartbeat is older than this.
    # ARCHITECTURE §2.1 sends a heartbeat ~every 5 min, so the default is 2× that.
    device_offline_after_s: int = 600

    # PostgreSQL system of record (users, devices, events, retraining_samples,
    # calibration, audit — ARCHITECTURE §2.2). Async DSN, e.g.
    #   postgresql+asyncpg://user:pass@host:5432/fall_guardian
    # When unset, the gateway runs DB-less and the persistence layers fall back to
    # stub mode (mirrors the detector), so the service stays testable without a DB.
    database_url: str | None = None

    # Deprecated alias for `database_url`; kept so existing FG_RETRAINING_DB_DSN
    # environments keep working. Prefer FG_DATABASE_URL.
    retraining_db_dsn: str | None = None

    @property
    def resolved_database_url(self) -> str | None:
        return self.database_url or self.retraining_db_dsn


def get_settings() -> Settings:
    return Settings()
