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
│   ├── main.py            create_app() factory + lifespan (builds the detector once)
│   ├── config.py          env-overridable settings (FG_* / .env)
│   ├── schemas.py         Pydantic v2 models = the §8 ingestion contract (payload_type-routed)
│   ├── routers/           health, inference (/v1/inference), retraining (/v1/retraining)
│   └── services/
│       ├── detector.py          CloudDetector seam (STUB until the Week-C Transformer)
│       └── retraining_store.py  RetrainingStore seam (STUB until MLOps persistence)
├── tests/                 TestClient smoke + contract tests
└── pyproject.toml
```

## Run it

```bash
cd backend
uv venv && uv pip install -e ".[dev]"
uv run uvicorn app.main:app --reload      # http://127.0.0.1:8000/docs
uv run pytest                             # smoke + contract tests
```

## Ingestion routing (`payload_type`)

The watch tags every uploaded window with `payload_type`:

- **`emergency`** (default) → `POST /v1/inference` → `CloudDetector` confirms/suppresses →
  `{is_fall, confidence, severity, action}`.
- **`retraining_data`** → `POST /v1/retraining` → **skips the detector**, stored as
  `CANCELED_FALSE_ALARM` (a window the user canceled during the local grace period) →
  `{stored, label, sample_id, message}`. Both endpoints share the same 125-sample
  validation. See `../docs/ARCHITECTURE.md` §3.2/§8 and ADR-011.

## Status (Week C kickoff)

- ✅ App skeleton, settings, the validated ingestion contract, health + inference routes.
- ✅ `CloudDetector` runs in **stub mode** (acceleration-magnitude heuristic) so the
  service is end-to-end testable now; responses report `model_version="stub-0.0"`.
- ✅ `payload_type` routing + `/v1/retraining`; `RetrainingStore` runs in **stub mode**
  (logs + returns an ack) until MLOps persistence (`FG_RETRAINING_DB_DSN`) lands.
- ⏭ Next: train the Transformer detector (43-dim features), export it, and load it in
  `detector.py`; then auth (per-device JWT), rate-limiting, Postgres event persistence,
  and the Redis→SSE caregiver feed.
