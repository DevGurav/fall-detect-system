# backend — Fall Guardian cloud gateway

FastAPI service that receives 2.5 s IMU windows from devices (real ESP32-S3 or
the virtual device), runs the **cloud detection model** to confirm/suppress the
edge's pre-impact trigger, and (later) persists events + notifies caregivers.

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
│   ├── routers/           health, inference (/v1/inference), retraining (/v1/retraining)
│   └── services/
│       ├── detector.py          CloudDetector (ONNX Transformer; heuristic stub fallback)
│       └── retraining_store.py  RetrainingStore (Postgres write; stub when DB-less)
├── alembic/               migrations (versions/0001 = initial schema) + async env.py
├── alembic.ini
├── tests/                 TestClient smoke + contract tests + offline schema guards
└── pyproject.toml
```

## Run it

```bash
cd backend
uv venv && uv pip install -e ".[dev]"
uv run uvicorn app.main:app --reload      # http://127.0.0.1:8000/docs
uv run pytest                             # smoke + contract + schema tests (no DB needed)

# Persistence is optional for local dev: with no FG_DATABASE_URL the gateway runs
# DB-less (stub stores). To enable Postgres, point at a DSN and apply migrations:
export FG_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/fall_guardian
uv run alembic upgrade head
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
  user when the device is paired (stub fallback when DB-less).
- ⏭ Next: persist confirmed falls on `/v1/inference` (`events`), device heartbeat + `GET /v1/events` /
  `GET /v1/devices` + acknowledge, the per-user normalization/threshold seam in `detector.py`, then real
  per-device JWT + pairing + Postgres RLS.
