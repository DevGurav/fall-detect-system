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
│   ├── schemas.py         Pydantic v2 models = the §8 ingestion contract
│   ├── routers/           health, inference (/v1/inference)
│   └── services/
│       └── detector.py    CloudDetector seam (STUB until the Week-C Transformer)
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

## Status (Week C kickoff)

- ✅ App skeleton, settings, the validated ingestion contract, health + inference routes.
- ✅ `CloudDetector` runs in **stub mode** (acceleration-magnitude heuristic) so the
  service is end-to-end testable now; responses report `model_version="stub-0.0"`.
- ⏭ Next: train the Transformer detector (43-dim features), export it, and load it in
  `detector.py`; then auth (per-device JWT), rate-limiting, Postgres event persistence,
  and the Redis→SSE caregiver feed.
