# Architecture — Fall Guardian v3

Technical reference for the Fall Guardian v3 system **as built**. The original
locked design (2026-05-31) targeted a managed cloud deployment (Fly.io + Supabase
+ Upstash) and a separate Next.js caregiver dashboard; the shipped system pivoted
to a **local-first deployment** (Docker Compose + an ngrok tunnel) with the
**Flutter app as the sole caregiver client**. This document reflects the shipped
system; the deltas are called out inline. For the engineering reasoning behind
each choice, see [`DECISIONS.md`](DECISIONS.md). For chronological build history,
see [`BUILD_LOG.md`](BUILD_LOG.md). For the audit of the v1/v2 prototypes this
replaces, see [`AUDIT_v1_v2.md`](AUDIT_v1_v2.md).

> **What changed between "locked design" and "as built"** (see §7 and §2.5):
> - **Deployment:** managed cloud (Fly.io/Render) → **local Docker Compose + a secure ngrok tunnel** (port 8000). Zero-cost, zero-latency, and good enough to test a *physical* phone against the real backend.
> - **Cloud model serving:** separate PyTorch RPC service → **ONNX served in-process** inside the FastAPI gateway (torch-free).
> - **Caregiver dashboard:** the Next.js web dashboard was **dropped**; the **Flutter app is the caregiver client** (live alerts over SSE + FCM).
> - **Model versioning:** the active model is the **5-fold cross-validated** export; the Phase-20 baseline is preserved under [`backend/app/model_old/`](../backend/app/model_old/).

---

## 1. High-level system

```text
┌────────────────────────────────────────────────────────────────────┐
│              FALL GUARDIAN v3  (wrist-worn · elderly · India)      │
└────────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────┐
   │  ESP32-S3 wrist wearable        │
   │  • MPU6050 / BMI270 IMU @ 50 Hz │
   │  • TFLite Micro INT8 (~80 KB)   │ ── EDGE MODEL: ConvLSTM-tiny
   │  • BLE provisioning             │     predicts pre-impact in <80 ms
   │  • Deep sleep                   │     fires "fall imminent" + uplink
   │  • Battery >24 h target         │     (~300–500 ms before impact)
   └──────────┬──────────────────────┘
              │  HTTPS + cert-pinned + device JWT
              │  (sends 2–3 s window ONLY when edge model fires)
              ↓
   ┌────────────────────────────────────────────────────────────────┐
   │  FastAPI Gateway  (local host :8000, exposed via ngrok tunnel)  │
   │  /v1/inference   ← emergency window → cloud detector → alert    │
   │  /v1/retraining  ← canceled false alarm → stored for MLOps      │
   │  /v1/devices     ← user + device routes (JWT)                   │
   │  /v1/events      ← timeline · acknowledge · SSE stream          │
   │  /v1/emergency   ← manual SOS                                   │
   └────────┬───────────────────────────────────────────────────────┘
            │
            ├──► PostgreSQL 16 (local Docker) — users, devices, events…
            ├──► Redis 7      (local Docker) — rate-limit, pub/sub for SSE
            ├──► CLOUD MODEL — Transformer encoder, served IN-PROCESS as ONNX
            │     • runs full sliding-window classification
            │     • CONFIRMS or SUPPRESSES the edge prediction
            │     • outputs: {is_fall, confidence, severity, action}
            │     • active = 5-fold CV export; baseline kept in model_old/
            └──► Notifier — SSE (foreground) + FCM (background/killed app)

   ┌──────────────────────┐         ┌───────────────────────────────┐
   │  Flutter App         │         │  ngrok                        │
   │  (caregiver client)  │ ◄─SSE──┤  https://<sub>.ngrok-free.app │
   │  Riverpod 3 · feeds  │ ◄─FCM──┤  → forwards to host :8000     │
   │  live alert + timeline│        │  (zero-cost public HTTPS for  │
   │  ⚠ Emergency SOS     │         │   testing a *physical* phone) │
   │  pairing + calibration│        └───────────────────────────────┘
   └──────────────────────┘
   (The Next.js web dashboard from the locked design was dropped — see §2.5.)

   Observability: structured JSON logs (+ optional Better Stack drain),
                  per-request trace IDs, /health + /health/ready probes.
```

---

## 2. Components

### 2.1 Edge — ESP32-S3 wrist wearable

**Hardware**: ESP32-S3 SoC (Tensilica Xtensa LX7 dual-core @ 240 MHz with vector instructions for AI inference, 512 KB SRAM, optional PSRAM expansion) + MPU6050 6-axis IMU (or BMI270 as an upgrade path) over I²C.

**Software**: ESP-IDF firmware. TensorFlow Lite Micro hosts the **ConvLSTM-tiny INT8** edge model in flash. The firmware runs an event loop that:

1. Samples the IMU at 50 Hz into a 125-sample circular buffer.
2. Every N samples (configurable; default = every new sample after initial fill), runs the model on the current window in <80 ms.
3. If `P(pre-impact fall) > threshold`, fires an interrupt that:
   - Vibrates the device (haptic warning to the user — gives them a chance to brace).
   - Opens an HTTPS connection to the cloud gateway.
   - Streams the 2.5 s window that triggered + the next ~1 s post-impact buffer.
4. Otherwise continues sampling. Periodically (every ~5 min) sends a heartbeat with battery + signal status.

**Network**: WiFi only in v3.0; BLE provisioning UI for the initial network credentials. Each device holds a per-device JWT issued at pairing time, stored in the ESP32 NVS encrypted partition.

**Power**: aggressive duty cycling. Deep-sleep between IMU reads when not actively analysing. Target ≥24 h continuous wear (charge once daily, like Apple Watch).

### 2.2 Cloud gateway — FastAPI

**Stack**: FastAPI 0.115+, Python 3.11+, Pydantic v2 for schema validation, Uvicorn for ASGI. **Deployment is local-first** (`uvicorn app.main:app --host 0.0.0.0 --port 8000`, with Postgres + Redis from `docker-compose.yml`); a physical phone reaches it either over the LAN or, preferably, through a **secure ngrok tunnel** that gives the host a public HTTPS URL at zero cost and zero added latency. The managed-cloud deploy (Fly.io/Render) from the locked design was dropped — see §7. A multi-stage `backend/Dockerfile` is kept for reproducible builds and as a future re-deploy seam.

**Endpoints** (all under `/v1/`):

- `POST /v1/inference` — receives a 2.5 s window from a device (`payload_type=emergency`), runs the cloud model, returns `{is_fall, confidence, severity, action}`. JWT-gated (device tokens). Pydantic schema strictly validated.
- `POST /v1/retraining` — receives a 2.5 s window the user **canceled** during the local grace period (`payload_type=retraining_data`). Skips the cloud model entirely and stores it labeled `CANCELED_FALSE_ALARM` for later fine-tuning / per-user threshold tuning. Same window validation as `/v1/inference` (shared schema). See §3.2.
- `POST /v1/devices/pair` — pair a device to a user account. Takes an 8-character Crockford base32 pairing code. Rate-limited (5 attempts → exponential backoff).
- `GET /v1/devices` — list a user's paired devices with live status (online/offline, last-seen, battery).
- `GET /v1/events` — fall-event timeline for a user, paginated.
- `POST /v1/events/{event_id}/acknowledge` — caregiver acks an alert.
- `GET /v1/events/stream` — Server-Sent Events feed of the caller's confirmed falls (per-user Redis channel).
- `POST /v1/emergency` — caregiver-initiated manual SOS (fans out to SSE + FCM like a confirmed fall).
- `PUT /v1/users/me/push-token` — registers the app's FCM token so the gateway can push to a killed app.

**Auth**: JWT (PyJWT). Per-user access tokens 15 min + refresh tokens with rotation; per-device JWTs (issued at pairing, 365-day TTL) are separate from per-user JWTs. bcrypt password hashing.

**Storage**:

- **PostgreSQL 16** (local Docker) — system of record for users, devices, events, retraining samples, calibration, audit log. Row-level security ties every user-scoped row to `user_id` (enforced by connecting as the non-superuser `fall_app` role). Alembic for migrations.
- **Redis 7** (local Docker) — rate limiting (public auth + pairing surface) and the per-user pub/sub channel (`events:user:{user_id}`) that drives the SSE feed. No-op when `FG_REDIS_URL` is unset.
- **Firebase Cloud Messaging** — push to a backgrounded/killed app only (**additive** to SSE — see §2.4 and §3.2). Gated on the service-account JSON (`FG_FIREBASE_CREDENTIALS`); a no-op when unset, so the SSE path still fires.

**Cloud detection model — versioning & preservation**: the gateway serves the
trained Transformer detector **in-process as a portable ONNX artifact** (no torch
dependency — onnxruntime runs the graph, numpy does the preprocessing). The active
path loads [`backend/app/model/cloud_detector.onnx`](../backend/app/model/) + its
`.meta.json` (decision threshold, Platt scaling, per-channel/feature normalisers,
severity scaler). That active artifact is the **5-fold subject-stratified
cross-validated** export (Phase 30). The earlier **Phase-20 baseline** is preserved
verbatim under [`backend/app/model_old/`](../backend/app/model_old/) so the
pre-CV model can be diffed or rolled back without git archaeology. If no artifact
is present the detector falls back to a transparent peak-acceleration **stub**, and
every response carries `model_version` so a stub is never mistaken for the real
model. See [`detector.py`](../backend/app/services/detector.py).

### 2.3 Cloud ML — in-process ONNX detector

**As built** (delta from the locked design): rather than a separate PyTorch RPC
service, the trained detector is exported to **ONNX and served in-process** inside
the gateway via onnxruntime (CPU provider). This keeps the backend torch-free,
removes a network hop and a second container, and makes the model a committed,
diffable artifact. The model is still MLflow-tracked during training (run-id +
metrics + artifacts), and the in-repo `model/` ⇄ `model_old/` split (see §2.2)
gives a one-line rollback. A separate scale-out service remains the upgrade path if
load ever demands it.

**Model**: Transformer encoder over the raw 2.5 s window (125×6) with the 43-dim engineered feature vector fused at the pooled head. The active export was trained with **5-fold subject-stratified cross-validation** (Phase 30). Outputs:

- Binary `P(fall)` — IMPACT+POST_IMPACT vs not. Matches the edge's PRE_IMPACT mirror (`Phase.is_positive_for_detection`) and the `is_fall` ingestion contract; the earlier 3-class `{ADL, near-fall, true-fall}` draft is superseded (see ADR-011 / MODEL_CARD §1.3).
- Regression head for severity (predicted peak acceleration magnitude).
- Calibrated probability via Platt-scaling or isotonic regression so the confidence number is trustworthy.

### 2.4 Mobile companion — Flutter

**Stack**: Flutter 3.35 / Dart 3.9, Riverpod 3 for state, `http` for the SSE transport (full control over reconnect/backoff/watchdog), `flutter_local_notifications` for OS alerts, `flutter_secure_storage` for JWTs at rest, `firebase_core` + `firebase_messaging` for push. **The Flutter app is the caregiver client** — the Next.js web dashboard from the locked design was dropped (§2.5).

**Hybrid alert routing — SSE + additive FCM** (the rule that prevents duplicate alerts):

- **Foreground (app open)** → the **SSE feed** (`GET /v1/events/stream`) is the source of truth. The app holds the open stream and raises the in-app alert / local notification itself. FCM messages that arrive in the foreground are deliberately **ignored** so the same fall is never shown twice.
- **Background / killed app** → the SSE socket can't run, so **FCM is the wake path**. The backend sends an FCM `notification` block, the OS renders the tray entry, and a tap routes into the timeline. FCM is therefore strictly **additive** — it covers only the states SSE cannot.
- If Firebase is unconfigured the app **degrades cleanly to SSE-only** (push token stays null). See [`messaging_service.dart`](../mobile/lib/core/notifications/messaging_service.dart) and §3.2.

**Key features**:

- Email/password + (later) biometric auth.
- 8-character alphanumeric pairing flow (replaces v2's brute-forceable 6-digit code).
- Live device status (online/offline, battery %, last-seen) — not just fall events.
- Fall-event timeline, per-device filtering.
- **Emergency button — actually built**: one-tap dial of the registered emergency contact + simultaneous SMS with the current GPS location.
- Acknowledgement flow: caregiver explicitly acks each alert; unacked alerts escalate after 60 s (SMS to caregiver, then to secondary contact).
- Accessibility: large-text + high-contrast modes, screen-reader labels (real elderly users need these).
- Offline: actions queued in Drift when offline, synced on reconnect.

### 2.5 Caregiver web dashboard — Next.js (DROPPED)

The locked design called for a separate Next.js web dashboard. It was **not
built**: the Flutter app (§2.4) covers the caregiver surface — live alerts,
timeline, acknowledge, manual SOS — over the same SSE feed the dashboard would
have consumed, so a second client added cost without adding capability for the
single-caregiver target use case. The gateway's `GET /v1/events/stream` is
transport-agnostic, so a web dashboard remains a clean future add-on (it would
just open the same SSE endpoint with a user JWT). There is no `dashboard/`
directory in the repo.

### 2.6 Virtual device — WEDA-FALL replay simulator

A standalone tool in [`virtual_device/`](../virtual_device/) that stands in for the
ESP32-S3 + MPU6050 wristband when no hardware is on the bench. **As built it
replays real recorded WEDA-FALL trials** rather than synthesising signals — the
backend is then exercised against the exact distribution the model was trained on:

1. **Read** a trial's `*_accel.csv` + `*_gyro.csv` straight from `ml/data/raw/WEDA-FALL-main/dataset/50Hz/`.
2. **Resample to a uniform 50 Hz grid.** The Fitbit Sense's BLE-batched timestamps are non-uniform, so each channel is linearly interpolated (`np.interp`) onto a true 50 Hz time base before slicing — yielding exactly the **125-sample (2.5 s)** window the firmware would emit. Falls are centred on the impact instant (carrying ~1 s of pre-impact lead); ADLs are taken from the middle of the recording.
3. **Pack** it into the exact §8 `WindowEnvelope` JSON and **POST** it — `emergency` → `POST /v1/inference` (detector confirms/suppresses); `--false-alarm` → `POST /v1/retraining` (canceled false alarm, stored, never detected).

Device auth mirrors the firmware: paste a `--device-token`, run the real `--pair`
handshake against a DB-backed server, or local-mint with the dev secret for a
bare `uvicorn`. Because it speaks the identical envelope and two-path routing,
**anything the simulator can drive, the real watch can drive too** — it lets the
whole gateway → SSE/FCM → phone path be tested end-to-end with zero hardware, and
gives the demo a reliable "fall trigger" button.

---

## 3. Data flow

### 3.1 Steady-state (no fall)

```text
IMU samples at 50 Hz → ESP32 circular buffer (125 samples = 2.5 s window)
                    → Edge model runs on the latest window
                    → P(pre-impact) < threshold → discard, continue
                    → (no network traffic)
```

Bandwidth in steady state = **zero**. Privacy-sensitive raw IMU data never leaves the device.

### 3.2 Fall detected by edge

```text
Edge model: P(pre-impact) > threshold
  ↓
ESP32 vibrates (haptic warning, ~0 ms)
  ↓
ESP32 opens HTTPS, streams the triggering window + ~1 s post-impact buffer
  ↓
FastAPI gateway: validate JWT, validate schema, rate-limit
  ↓
Gateway runs the cloud model IN-PROCESS (ONNX) on the full window
  ↓
Cloud confirms (is_fall=true, severity=high) OR cancels (is_fall=false)
  ↓
If confirmed (EventStore.record_fall):
   ├─ Insert into Postgres `events` (severity, confidence, peak, model_version)
   ├─ Publish on Redis channel `events:user:<id>`
   │    → app's open SSE stream raises the live alert (FOREGROUND path)
   └─ Send FCM push to the owner's registered token
        → OS wakes a BACKGROUND/KILLED app (additive; foreground ignores it)
```

**SSE and FCM are additive, never duplicated.** SSE handles the real-time
foreground stream; FCM handles only the background/killed-app states the SSE
socket cannot cover, and the app suppresses foreground FCM messages so a single
fall yields a single alert. Persistence is DB-gated, but **SSE + FCM fire even
DB-less** — a caregiver must hear about a fall whether or not the row was written.
(The 60 s ack-escalation / SMS retry queue from the locked design is **not built**;
acknowledgement clears the alert via §3.3.)

End-to-end latency budget: edge inference <80 ms, network round-trip <500 ms, cloud inference <500 ms, notification fan-out <1 s. From impact peak to caregiver phone notification = **~2 s** when network is good.

**Local grace period (false-alarm capture).** Before the watch streams anything, the haptic warning opens a ~10 s grace window. If the user presses **Cancel** (it wasn't a fall), the watch does *not* send an emergency: it silently uploads that same 2.5 s window to `POST /v1/retraining` (`payload_type=retraining_data`). The gateway **skips the cloud model** and stores the window labeled `CANCELED_FALSE_ALARM` for later fine-tuning / per-user threshold tuning — the user is ground truth for their own false alarms. This data-collection path is deliberately separate from the alerting path so a canceled trigger can never page a caregiver (see ADR-011). If the user does *not* cancel within the grace window, the flow above proceeds (`payload_type=emergency` → `/v1/inference`).

### 3.3 Acknowledgement

Caregiver taps "I've responded" in the mobile app → `POST /v1/events/{id}/acknowledge` → the event is marked acknowledged in Postgres and the audit log records it. The ack clears the alert on the caregiver's timeline.

---

## 4. The ML pipeline in detail

### 4.1 Two models, one shared pipeline

| Stage | Model | Job | Where it runs | Input | Constraints |
|---|---|---|---|---|---|
| Edge | ConvLSTM-tiny (INT8) | Pre-impact prediction (300–500 ms before ground impact) | TFLite Micro on ESP32-S3 | Raw 6-channel 125-sample window | ≤100 KB · <80 ms · INT8 |
| Cloud | Transformer encoder | Post-impact confirmation + severity | In-process ONNX in the FastAPI gateway (local) | Raw 125×6 window + 43-dim engineered feature vector | FP32 · 5-fold CV · MLflow-tracked |

### 4.2 Datasets

| Dataset | Used for | Why |
|---|---|---|
| **WEDA-FALL** | Primary training for both models | Wrist-worn Fitbit Sense, 25 subjects (14 young + 11 elderly aged 77–95), 11 ADL + 8 fall types, 50 Hz, accel + gyro + orientation, manually-labelled fall windows |
| **SmartFall** (Texas State) | Cloud model ADL augmentation | 9 elderly, real-world continuous wear (3 hrs/day × 7 days), accel-only |
| **UP-Fall** wrist channel | Cross-dataset generalization test only | 17 young, 18 Hz, different device — proves the model isn't overfit to Fitbit-specific signal |
| **Indian-ADL supplement** (collected in Week E) | Validation + augmentation | Sukhasana, namaste, floor sit/rise, squat toilet, intentional wrist motions — Indian-context gap fill |

KFall and SisFall were considered and explicitly rejected — they're waist/thigh-mounted; sensor-position transfer is not a credible production approach.

### 4.3 Pre-impact label re-derivation

WEDA-FALL ships per-fall `(start_time, end_time)` covering the full 4-phase fall sequence but not the impact instant. The edge model needs the instant. Algorithm:

1. Resample the recording to uniform 50 Hz (Fitbit Sense's BLE-batched timestamps are non-uniform).
2. Compute `|a|(t) = sqrt(ax² + ay² + az²)` per sample.
3. Within `[start_time, end_time]`, `t_impact = argmax_t |a|(t)`.
4. Sanity check: peak `|a| ≥ 20 m/s²` (~2g).
5. Phase labels around `t_impact`:
   - `PRE_IMPACT` = `[t-500 ms, t-50 ms]` (clamped to `fall_start`)
   - `IMPACT` = `[t-50 ms, t+500 ms]`
   - `POST_IMPACT` = `[t+500 ms, fall_end]`
   - `BACKGROUND` elsewhere
6. Validation: report the `t_impact - start_time` lag distribution. Expected 0.5–1.5 s.

Implementation: [`ml/src/fall_guardian_ml/datasets/pre_impact_labels.py`](../ml/src/fall_guardian_ml/datasets/pre_impact_labels.py). Tests: [`ml/tests/test_pre_impact_labels.py`](../ml/tests/test_pre_impact_labels.py).

### 4.4 Sliding window

2.5-second windows at 50 Hz = **125 samples per window**. Training stride = **62 samples** (50% overlap, doubles positives per recording — important because the PRE_IMPACT phase is only ~450 ms).

For fall recordings, additionally emit one **pre-impact-aligned window** whose end is exactly at `t_impact - guard_s`. This positions the PRE_IMPACT phase at the tail of the window — exactly what the edge model sees in production — guaranteeing the prediction model has positive examples to train on.

Implementation: [`ml/src/fall_guardian_ml/features/windowing.py`](../ml/src/fall_guardian_ml/features/windowing.py).

### 4.5 Feature vector (cloud model input)

43 features per window:

| Group | Count | What |
|---|---|---|
| Per-channel stats × 6 channels | 36 | mean, std, min, max, peak-to-peak, RMS for each of (ax, ay, az, wx, wy, wz) |
| Signal Magnitude Area on accel | 1 | mean(|ax| + |ay| + |az|) |
| Accel magnitude stats | 3 | peak, mean, std of |a| = sqrt(ax² + ay² + az²) |
| Peak jerk on accel magnitude | 1 | max(|d|a|/dt|) — falls have a huge transient |
| FFT — dominant freq + spectral entropy | 2 | walking is narrowband (~1.5–2 Hz, low entropy); falls are broadband (high entropy) |

The edge model uses just the raw 6-channel window — no engineered features — because it must fit ≤80 KB INT8.

Implementation: [`ml/src/fall_guardian_ml/features/extraction.py`](../ml/src/fall_guardian_ml/features/extraction.py).

### 4.6 Per-user z-score normalization

Per-feature `(x - mean) / std`, fit **only on the user's ADL windows** (not fall windows — those would skew the stats). Computed once at pairing time during ~10–15 min of normal wear, then applied to every inference.

Implementation: [`ml/src/fall_guardian_ml/features/normalization.py`](../ml/src/fall_guardian_ml/features/normalization.py).

### 4.7 Validation methodology

- **Subject-stratified k-fold cross-validation** — never train and test on the same subject. The simplest dataset-leakage trap and the one that inflated v1/v2's "100% accuracy" claim.
- **Held-out test subjects** — 20% of subject IDs reserved across the entire experiment.
- **Honest metrics**: precision, recall, F1, **FPR on ADL** (the metric that matters for daily-wear comfort), confusion matrix, **lead-time histogram** for the prediction model, ROC + AUC.
- **Calibration**: Platt-scaling or isotonic so probability outputs are meaningful.
- **MLflow tracking** — every experiment versioned, parameters logged, artifacts (model + confusion matrix + ROC plot) attached. Reproducible from any run-id.

### 4.8 Performance targets

| Metric | Target |
|---|---|
| Edge recall on WEDA-FALL held-out subjects | ≥ 95% |
| Edge FPR on ADL | ≤ 5% |
| Edge mean lead time | ≥ 300 ms |
| Edge model size (INT8) | ≤ 100 KB |
| Edge inference latency | < 80 ms on ESP32-S3 |
| Cloud recall on WEDA-FALL + SmartFall held-out subjects | ≥ 97% |
| Cloud FPR on ADL (including Indian-ADL) | ≤ 2% |
| Cross-dataset generalization (UP-Fall wrist) | recall drop ≤ 10 percentage points from primary |
| End-to-end pipeline | false-positive rate ≤ 0.5 per day in continuous-wear simulation |
| Wearable battery | ≥ 24 h continuous wear |

---

## 5. Security baseline

Replacing v1/v2's effectively-zero auth posture:

- JWT access tokens (15 min) + refresh tokens (30 days, rotation)
- Per-device JWTs issued at pairing; stored in ESP32 NVS encrypted partition; cert-pinned HTTPS
- **8-character alphanumeric pairing code** (Crockford base32, no ambiguous chars) + 5-min TTL + 5 attempts → exponential backoff (replaces v2's brute-forceable 6-digit numeric code)
- Postgres row-level security: every events query is scoped to the requesting `user_id` at the DB level, not just the application layer
- Firestore (FCM only) rules scoped to `request.auth.uid` (replaces v2's `allow read, write: if true;`)
- Rate limits: 60 req/min per device, 10 pairing attempts/hr per IP
- Pydantic schemas on every endpoint; no silent `.get('x', 0)` defaults
- `audit_events` table: every pair, ack, API-key use logged for compliance + debugging
- Secrets in Cloud Secret Manager (never in committed `.env`)
- HTTPS + HSTS + standard security headers via middleware

---

## 6. Observability

As built (Phase 32):

- **Structured JSON logs** to stdout with a **per-request trace ID** (correlation across a request's log lines). See [`observability.py`](../backend/app/observability.py).
- **Optional Better Stack (Logtail) drain** — when `FG_BETTER_STACK_TOKEN` is set (and the `observability` extra installed) logs ship there in addition to stdout; unset → stdout-only.
- **Health probes**: `GET /health` (liveness/startup, no dependency I/O) and `GET /health/ready` (readiness — pings Postgres + Redis, reports the loaded `model_version`, returns 503 + "degraded" when a configured dependency is down).

OpenTelemetry traces, Prometheus/Grafana metrics, and Sentry from the locked
design were **deprioritised** — for a local, single-operator deployment the JSON
logs + trace IDs + readiness probe are sufficient (see PLAN "Locked Tradeoffs").

The single most important metric to watch in production: **false positives per user per day**. Alert fatigue kills caregiver trust in the system, which is what makes the product fail in the real world even when the academic metrics look great.

---

## 7. Deployment topology — local-first + ngrok tunnel

**The locked design's managed cloud (Fly.io gateway + Supabase Postgres + Upstash
Redis + Vercel dashboard) was dropped.** The shipped system runs entirely on the
developer's machine, and a **secure ngrok tunnel** exposes it to a *physical*
phone over public HTTPS. This is the **zero-cost, zero-latency
production-testing environment**: no monthly bill, no cold-start tax, no deploy
round-trip — yet a real Android handset talks to the real backend over a real TLS
URL, which is exactly what FCM and an SSE-over-HTTPS client want.

| Component | Where it runs | Notes |
|---|---|---|
| FastAPI gateway | Host process — `uvicorn … --host 0.0.0.0 --port 8000` | Serves the ONNX detector in-process (§2.3). |
| ngrok tunnel | `ngrok http 8000` | Public HTTPS → host `:8000`. The app points `FG_BASE_URL` at the printed `https://<sub>.ngrok-free.app`. |
| PostgreSQL 16 | Local Docker (`docker-compose.yml`) | System of record; RLS via the `fall_app` role. |
| Redis 7 | Local Docker (`docker-compose.yml`) | Rate-limit + per-user SSE pub/sub. |
| FCM | Firebase project `fall-guardian-v3` | Push to a backgrounded/killed app only; service-account JSON in gitignored `backend/.env`. |
| Cloud detector model | Committed in-repo | Active `model/` + preserved baseline `model_old/` (§2.2). |

**Why ngrok over the LAN-IP path:** a `http://<LAN-IP>:8000` URL works only on the
same Wi-Fi and is plaintext; the ngrok HTTPS URL works from anywhere and satisfies
the platform expectations (TLS) that the eventual cloud deploy would also have to
meet — so the demo path and the production path are the same shape. The reusable
multi-stage `backend/Dockerfile` and `FG_ENVIRONMENT=production` validator (which
refuses to boot with the dev JWT secret) remain the seam for a future managed
re-deploy without re-architecting.

> **Cost expectation: $0/month.** The cold-start concern that crippled v1/v2 on
> the Render free tier is moot — there is no always-on hosted instance to keep warm.

---

## 8. Hardware-agnostic ingestion contract

The single JSON window contract the cloud accepts (same shape for both ingestion endpoints):

```jsonc
{
  "payload_type": "emergency",             // "emergency" | "retraining_data"; default "emergency"
  "device_id": "string",                   // device JWT scopes ownership
  "ts_start_unix_ms": 0,                   // window start, ms since epoch
  "sample_rate_hz": 50,
  "samples": [                             // 125 entries for a 2.5 s window
    {"ax": 0.0, "ay": 0.0, "az": 9.81,
     "wx": 0.0, "wy": 0.0, "wz": 0.0},
    // …
  ],
  "edge_prediction": {                     // included if the edge model fired
    "p_pre_impact": 0.92,
    "model_version": "convlstm-tiny-v0.3"
  }
}
```

Both the real ESP32 firmware and the Python virtual device emit this exact shape. Cloud has no idea (and doesn't care) which one sent it. Lets development proceed against the virtual device until the hardware arrives, then swap with zero backend changes.

**`payload_type` routes the window to one of two endpoints** (same validated envelope, different handling):

| `payload_type` | Endpoint | Handling |
|---|---|---|
| `emergency` (default) | `POST /v1/inference` | Runs the cloud model → `{is_fall, confidence, severity, action}`. |
| `retraining_data` | `POST /v1/retraining` | **Skips the model.** Stores the window labeled `CANCELED_FALSE_ALARM` → `{stored, label, sample_id, message}`. |

The field defaults to `emergency`, so existing clients are unaffected. It is pinned to `retraining_data` on `/v1/retraining` (an `emergency` body there is a 422), so a live trigger can't be diverted into the data-collection path. The 125-sample validation is shared by both endpoints. See §3.2 and ADR-011.

---

## 9. Roadmap snapshot — shipped state

(High-level. Detailed week-by-week build sequence + per-phase status is in [`PLAN.md`](PLAN.md).)

- **Week A** — data foundation: loaders, pre-impact label derivation, windowing, 43-dim features, tests. ✅
- **Week B** — edge ConvLSTM-tiny baseline, INT8 quantize, latency benchmark. ✅ (96.5% recall on held-out subjects, ~46 KB INT8).
- **Week C** — cloud Transformer detector + FastAPI gateway + both ingestion paths (`/v1/inference` emergency, `/v1/retraining` canceled-false-alarm capture). ✅ Detector served in-process as ONNX.
- **Week D** — stateful backend: async SQLAlchemy + Alembic (8 tables), JWT auth + 8-char pairing, Postgres RLS via `fall_app`, Redis rate limiting, the **SSE caregiver feed**. ✅
- **Week E** — Flutter rebuild (Riverpod 3): login/register, pairing, live SSE alerts, timeline + acknowledge, **emergency SOS**, calibration onboarding, and **additive FCM** for background/killed apps. ✅
- **Week F** — ML hardening (**5-fold cross-validated** cloud re-export; baseline kept in `model_old/`), **ESP32-S3 firmware** (TFLite Micro + grace period + BLE pairing), and production-readiness (Docker, GitHub Actions CI, structured logging + readiness probe). ✅
- **Deployment** — pivoted from managed cloud to **local Docker Compose + ngrok tunnel** (§7); the **virtual device** (§2.6) drives the whole path end-to-end with no hardware.

**Personalization is a core feature**: the local grace period + the canceled-false-alarm retraining loop + per-user calibration let the system learn each user's non-falls. Architecture in §3.2/§4.6/§8, rationale in ADR-011.

Beyond v3.0: the dropped Next.js web dashboard (the SSE endpoint is ready for it), a managed cloud re-deploy off the existing `Dockerfile`, edge-only mode, more datasets (Indian-ADL collection, Geriatric Wrist IMU), federated learning, and a custom PCB form factor.
