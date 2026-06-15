# backend — Fall Guardian cloud gateway

FastAPI service that receives 2.5 s IMU windows from devices (real ESP32-S3 or
the virtual device), runs the **cloud detection model** to confirm/suppress the
edge's pre-impact trigger, persists confirmed falls, and notifies caregivers over
**SSE (foreground) + additive FCM (background/killed app)**.

The edge model is recall-first and fires often by design (high FPR); this service
is the **precision gate** that decides whether a caregiver is actually alerted.
The detection model is served **in-process as ONNX** (no torch dependency).
See [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §2.2–2.3 and §8.

> **Runs locally.** `uvicorn … --port 8000` on the host + Postgres/Redis from the
> repo-root `docker-compose.yml`; a physical phone reaches it through an **ngrok**
> tunnel (HTTPS → `:8000`). The managed-cloud deploy was dropped — see
> [`../docs/RUN.md`](../docs/RUN.md) §2a and `../docs/ARCHITECTURE.md` §7. The
> `Dockerfile` is kept for reproducible builds / a future re-deploy.

## Layout

```text
backend/
├── app/
│   ├── main.py            create_app() factory + lifespan (builds detector, db, store once)
│   ├── config.py          env-overridable settings (FG_* / .env)
│   ├── schemas.py         Pydantic v2 models = the §8 ingestion contract (payload_type-routed)
│   ├── db.py              async SQLAlchemy engine/session, DSN-gated (None → DB-less mode)
│   ├── models.py          SQLAlchemy 2.0 schema = the §2.2 system of record (8 tables)
│   ├── auth.py             JWT (user + device) + bcrypt + 8-char pairing codes + deps
│   ├── security.py        get_device lookup helper (shared by services)
│   ├── deps.py            request deps (require_db → 503 when DB-less)
│   ├── ratelimit.py       Redis fixed-window rate limiter (no-op without Redis)
│   ├── broker.py          EventBroker (per-user Redis pub/sub for the SSE feed)
│   ├── observability.py   structured JSON logging + per-request trace IDs (Phase 32)
│   ├── model/             ACTIVE detector — 5-fold CV ONNX + .meta.json (loaded by default)
│   ├── model_old/         PRESERVED Phase-20 baseline ONNX (rollback / A-B via FG_MODEL_PATH)
│   ├── routers/           health, auth, users, inference, retraining, events, devices, emergency, contacts
│   └── services/
│       ├── detector.py          CloudDetector (in-process ONNX; per-user calibration; stub fallback)
│       ├── calibration_store.py CalibrationStore (per-device profile read + fit-at-pairing write)
│       ├── retraining_store.py  RetrainingStore (Postgres write; stub when DB-less)
│       ├── event_store.py       EventStore (persist falls → SSE publish → additive FCM push)
│       ├── fcm_service.py       FcmService (FCM HTTP v1; no-op without credentials)
│       ├── device_service.py    DeviceService (heartbeat + live status)
│       ├── user_service.py      UserService (register/login, bcrypt, FCM push-token)
│       ├── refresh_token_service.py  RefreshTokenService (rotation)
│       ├── pairing_service.py   PairingService (pairing-code create/redeem)
│       └── audit_service.py     AuditService (audit_events writes)
├── alembic/               migrations (0001 schema · 0002 RLS · 0003 app role · 0004 FCM push-token · 0005 refresh tokens) + async env.py
├── alembic.ini
├── Dockerfile             multi-stage build (kept for reproducibility / future re-deploy)
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
# DB-less (stub stores). For real persistence: start Postgres, MIGRATE as the owner,
# then RUN as the non-superuser fall_app so Postgres RLS actually enforces.
docker compose up -d --wait                                  # repo root: Postgres 16 + Redis 7
export FG_DATABASE_URL=postgresql+asyncpg://fall:fall@localhost:5432/fall_guardian
uv run alembic upgrade head                                  # schema + RLS + the fall_app role
export FG_DATABASE_URL=postgresql+asyncpg://fall_app:fall_app@localhost:5432/fall_guardian
export FG_REDIS_URL=redis://localhost:6379/0                 # enables rate limiting
uv run uvicorn app.main:app

# End-to-end smoke vs the live DB (register -> pair -> heartbeat -> inference -> events -> ack):
uv run python scripts/integration_smoke.py
```

## Ingestion routing (`payload_type`)

The watch tags every uploaded window with `payload_type`:

- **`emergency`** (default) → `POST /v1/inference` → `CloudDetector` confirms/suppresses →
  `{is_fall, confidence, severity, action}`.
- **`retraining_data`** → `POST /v1/retraining` → **skips the detector**, stored as
  `CANCELED_FALSE_ALARM` (a window the user canceled during the local grace period) →
  `{stored, label, sample_id, message}`. Both endpoints share the same 125-sample
  validation. See `../docs/ARCHITECTURE.md` §3.2/§8 and ADR-011.

## Model versioning & preservation

The detector loads a committed ONNX artifact + its `.meta.json` (decision
threshold, Platt scaling, per-channel/feature z-score stats, severity scaler):

- **`app/model/`** — the **active** model, loaded by default. This is the
  **5-fold subject-stratified cross-validated** export (Phase 30).
- **`app/model_old/`** — the **preserved Phase-20 baseline**, kept verbatim so the
  pre-CV model can be diffed, A/B-tested, or rolled back without git archaeology.

`FG_MODEL_PATH` overrides the path — e.g. point it at
`app/model_old/cloud_detector.onnx` to serve the baseline, or at a missing path to
force the deterministic peak-acceleration **stub**. Every response (and `/health`)
carries `model_version`, so a stub (`stub-0.0`) is never mistaken for the real
model. See [`app/services/detector.py`](app/services/detector.py).

## Alerting — SSE + additive FCM

On a confirmed fall, `EventStore.record_fall` (1) persists the event (DB-gated),
(2) publishes it to the owner's per-user Redis channel for the **SSE** feed
(`GET /v1/events/stream`), and (3) dispatches an **additive FCM** push:

- **SSE** is the real-time **foreground** path — the app holds the open stream.
- **FCM** (`fcm_service.py`, FCM HTTP v1) covers **only background/killed** apps;
  the mobile client ignores foreground FCM so a fall is never alerted twice.
- SSE + FCM both fire **even when DB-less**; FCM is a **no-op** unless
  `FG_FIREBASE_CREDENTIALS` (service-account JSON) is set. The app registers its
  token via `PUT /v1/users/me/push-token`. See `../docs/ARCHITECTURE.md` §2.4/§3.2.

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
- ✅ **Personalization (Week D)**: `/v1/inference` looks up the device's `device_calibration` and applies it
  per request — per-user z-score normalisers + a `threshold_override`, each falling back to the model's global
  stats when absent. Verified end-to-end: a per-device threshold flips the verdict on the same window.
- ✅ **Auth + pairing (Week D)**: per-user access tokens (bcrypt + HS256 JWT) + per-device tokens issued via
  the 8-char pairing-code flow; ingestion/heartbeat require a device token (body `device_id` must match → 403),
  reads require a user token — retiring the `X-User-Id` stub.
- ✅ **Postgres RLS (Week D)**: every user-scoped table has a `FORCE`d policy keyed on a per-transaction
  `app.user_id` GUC; the gateway connects as the non-superuser `fall_app` (migration 0003) so the policies bind
  (superusers bypass RLS). Verified: a role with no `app.user_id` set sees **zero** rows.
- ✅ **Rate limiting (Phase 26)**: Redis fixed-window limiter on the public auth + pairing surface
  (`/v1/auth/*`, `/v1/devices/pair`, `/v1/devices/pairing-codes`) — per (scope, client IP), `429` +
  `Retry-After` over the limit. No-op without `FG_REDIS_URL`. Verified live: 10 logins, then `429`.
- ✅ **SSE caregiver feed (Phase 27)**: a confirmed fall is published to the owner's Redis channel and
  pushed live to `GET /v1/events/stream` (Server-Sent Events, user-token authed) — `event: fall` frames
  with the persisted `event_id`, comment-frame keepalives between alerts. Each caller subscribes only to
  their own channel. Returns 503 without `FG_REDIS_URL`. Verified live: a fall reached the owner's stream
  in real time while a second caregiver's stream stayed silent (per-user isolation).
- ✅ **Emergency SOS + additive FCM (Phase 28b)**: `POST /v1/emergency` fans a manual SOS out to SSE + FCM;
  confirmed falls also push **FCM (HTTP v1)** to the owner's registered token (`PUT /v1/users/me/push-token`,
  migration 0004) — strictly **additive** to SSE (background/killed only). No-op without `FG_FIREBASE_CREDENTIALS`.
- ✅ **Calibration write path + refresh tokens (Phase 29)**: the fit-at-pairing *write* path for
  `device_calibration`, refresh-token rotation (migration 0005), and `audit_events` writes.
- ✅ **5-fold cross-validated model (Phase 30)**: active `app/model/` re-exported from 5-fold CV; the Phase-20
  baseline preserved under `app/model_old/` (see *Model versioning & preservation* above).
- ✅ **Production-readiness (Phase 32)**: multi-stage `Dockerfile`, GitHub Actions CI (pytest + migrations +
  mobile), structured JSON logging with per-request trace IDs, and `/health` + `/health/ready` probes.
- ✅ **Local-first deploy**: runs on the host with Docker Compose (Postgres + Redis); a physical phone reaches
  it through an **ngrok** HTTPS tunnel to `:8000`. See `../docs/RUN.md`.
