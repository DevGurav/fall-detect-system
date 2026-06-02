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

    # Cloud detection model. Until the Week-C Transformer is trained + exported,
    # the detector runs in stub mode (see services/detector.py).
    model_path: str | None = None
    model_version: str = "stub-0.0"

    # Confidence threshold above which the cloud CONFIRMS a fall.
    fall_confidence_threshold: float = 0.5

    # Datastore for canceled-false-alarm windows uploaded during the grace period.
    # Until MLOps persistence lands, the store runs in stub mode (see
    # services/retraining_store.py). Set to a Postgres DSN to enable real writes.
    retraining_db_dsn: str | None = None


def get_settings() -> Settings:
    return Settings()
