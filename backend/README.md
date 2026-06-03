# backend — Fall Guardian cloud gateway

FastAPI service that receives 2.5 s IMU windows from devices (real ESP32-S3 or
the virtual device), runs the **cloud detection model** to confirm/suppress the
edge's pre-impact trigger, persists confirmed falls, and (later) notifies caregivers.

The edge model is recall-first and fires often by design (high FPR); this service
is the **precision gate** that decides whether a caregiver is actually alerted.
See [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §2.2–2.3 and §8.

## Layout

```text
backend/
├── app/
│   ├── main.py            create_app() factory + lifespan (builds detector, db, store once)
│   ├── config.py          env-overridable settings (FG_* / .env)
│   ├── schemas.py         Pydantic v2 models = the §8 ingestion contract (payload_type-routed)
│   ├── db.py              async SQLAlchemy engine/session, DSN-gated (None → DB-less mode)
│   ├── models.py          SQLAlchemy 2.0 schema = the §2.2 system of record (8 tables)
│   ├── security.py        identity seam: device→user resolution + trusted-auth stub
│   ├── deps.py            request deps (require_db → 503 when DB-less)
│   ├── routers/           health, inference, retraining, events, devices
│   └── services/
│       ├── detector.py          CloudDetector (ONNX Transformer; heuristic stub fallback)
│       ├── retraining_store.py  RetrainingStore (Postgres write; stub when DB-less)
│       ├── event_store.py       EventStore (persist confirmed falls + timeline/ack)
│       └── device_service.py    DeviceService (heartbeat upsert + live status)
├── alembic/               migrations (versions/0001 = initial schema) + async env.py
├── alembic.ini
├── scripts/               integration_smoke.py (end-to-end smoke vs a live DB)
├── tests/                 TestClient smoke + contract + telemetry tests + offline guards
└── pyproject.toml
```

## Run it

```bash
cd backend
uv venv && uv pip install -e ".[dev]"
uv run uvicorn app.main:app --reload      # http://127.0.0.1:8000/docs
uv run pytest                             # smoke + contract + schema tests (no DB needed)

# Persistence is optional for local dev: with no FG_DATABASE_URL the gateway runs
# DB-less (stub stores). For real persistence, start Postgres and apply migrations:
docker compose up -d --wait                                  # repo root: Postgres 16
export FG_DATABASE_URL=postgresql+asyncpg://fall:fall@localhost:5432/fall_guardian
uv run alembic upgrade head

# End-to-end smoke vs the live DB (heartbeat -> inference -> events -> acknowledge):
uv run uvicorn app.main:app &                                # in one shell
uv run python scripts/integration_smoke.py                   # in another
```

## Ingestion routing (`payload_type`)

The watch tags every uploaded window with `payload_type`:

- **`emergency`** (default) → `POST /v1/inference` → `CloudDetector` confirms/suppresses →
  `{is_fall, confidence, severity, action}`.
- **`retraining_data`** → `POST /v1/retraining` → **skips the detector**, stored as
  `CANCELED_FALSE_ALARM` (a window the user canceled during the local grace period) →
  `{stored, label, sample_id, message}`. Both endpoints share the same 125-sample
  validation. See `../docs/ARCHITECTURE.md` §3.2/§8 and ADR-011.

## Status

- ✅ App skeleton, settings, the validated §8 ingestion contract, health + inference + retraining routes.
- ✅ `CloudDetector` serves the trained **Transformer via ONNX** (onnxruntime + numpy, torch-free); a
  peak-acceleration heuristic remains only as a fallback when no artifact is present. `/health` reports
  the real `model_version`.
- ✅ **Persistence foundation (Week D)**: async SQLAlchemy + Alembic; the §2.2 schema (`users`, `devices`,
  `events`, `retraining_samples`, `device_calibration`, `audit_events`, …) in `models.py` + migration `0001`.
  **DSN-gated** — with no `FG_DATABASE_URL` the gateway runs DB-less and persistence falls back to stub mode,
  so tests run without Postgres.
- ✅ `RetrainingStore` writes canceled-false-alarm windows to `retraining_samples`, scoped to the owning
  device + user when paired (stub fallback when DB-less).
- ✅ **Stateful gateway (Week D)**: `/v1/inference` persists **confirmed falls** to `events` (no-op DB-less);
  `POST /v1/devices/heartbeat` records battery/signal/last-seen (registers the device on first contact);
  `GET /v1/devices` reports live status (online/offline derived from `last_seen_at`); `GET /v1/events` is a
  paginated timeline and `POST /v1/events/{id}/acknowledge` clears an alert. Read/telemetry routes return 503
  when DB-less; results scope to `X-User-Id` when supplied.
- ⏭ Next: the per-user normalization + threshold seam in `detector.py` (reads `device_calibration`), then
  real per-device JWT + pairing-code flow + Postgres RLS to replace the trusted-stub identity.
