"""Application settings (12-factor: env-overridable, sane defaults for local dev)."""
from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Dev-only default JWT secret (>= 32 bytes for HS256). The validator on Settings
# refuses to boot with it outside local, so production must set FG_JWT_SECRET.
_DEV_JWT_SECRET = "dev-insecure-change-me-not-for-production-use"


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

    # Redis (rate limiting now; SSE pub/sub later). When unset, rate limiting is a
    # no-op — dev + tests run without Redis, mirroring the DB gate.
    redis_url: str | None = None

    # Firebase service-account JSON (the full JSON string, not a file path) for
    # FCM push notifications.  When unset, the FCM service is a no-op — the fall
    # alert still fires over SSE, but no push is sent to a killed app.
    # Set FG_FIREBASE_CREDENTIALS to the contents of your serviceAccountKey.json.
    firebase_credentials_json: str | None = None

    # Better Stack (Logtail) source token for the log drain (Phase 32 observability).
    # When set, structured JSON logs are shipped to Better Stack in addition to
    # stdout; when unset, stdout-only (the local-first default — read the JSON from
    # the console / `docker compose logs`). Requires the optional `observability`
    # extra (logtail-python) to actually ship.
    better_stack_token: str | None = None

    @property
    def resolved_database_url(self) -> str | None:
        return self.database_url or self.retraining_db_dsn

    # Auth (ARCHITECTURE §5): HS256 JWTs (PyJWT) + bcrypt passwords. The secret has
    # a dev default, but the app refuses to boot with it outside local (validator).
    jwt_secret: str = _DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    user_access_ttl_min: int = 15          # per-user access token lifetime
    device_token_ttl_days: int = 365       # per-device token, issued at pairing
    pairing_code_ttl_min: int = 5          # 8-char pairing code lifetime
    pairing_max_attempts: int = 5          # redeem attempts before a code locks

    @model_validator(mode="after")
    def _require_secret_outside_local(self) -> Settings:
        if self.environment != "local" and self.jwt_secret == _DEV_JWT_SECRET:
            raise ValueError("FG_JWT_SECRET must be set when FG_ENVIRONMENT is not 'local'")
        return self


def get_settings() -> Settings:
    return Settings()
