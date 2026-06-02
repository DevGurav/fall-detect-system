# Architecture — Fall Guardian v3

Technical reference for the current Fall Guardian v3 system. Reflects the locked design as of 2026-05-31. For the engineering reasoning behind each choice, see [`DECISIONS.md`](DECISIONS.md). For chronological build history, see [`BUILD_LOG.md`](BUILD_LOG.md). For the audit of the v1/v2 prototypes this replaces, see [`AUDIT_v1_v2.md`](AUDIT_v1_v2.md).

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
   │  FastAPI Gateway  (Fly.io)                                     │
   │  /v1/inference   ← emergency window → cloud detector → alert    │
   │  /v1/retraining  ← canceled false alarm → stored for MLOps      │
   │  /v1/devices     ← OAuth user routes                           │
   │  /v1/events      ← timeline · acknowledge · escalation         │
   └────────┬───────────────────────────────────────────────────────┘
            │
            ├──► PostgreSQL (Supabase) — users, devices, events
            ├──► Redis        — rate-limit, pub/sub for SSE
            ├──► CLOUD MODEL — Transformer encoder (or 1D-CNN→LSTM)
            │     • runs full sliding-window classification
            │     • CONFIRMS or SUPPRESSES the edge prediction
            │     • outputs: {is_fall, lead_time_ms, confidence, severity}
            │     • MLflow-tracked, semver, rollback-able
            └──► Notifier — FCM + email + retry queue + escalation

   ┌──────────────────────┐         ┌──────────────────────┐
   │  Flutter App         │         │  Next.js Dashboard   │
   │  (wrist companion)   │         │  (caregiver web)     │
   │  Riverpod 2 · GoRouter│        │  TS · Tailwind v4    │
   │  Drift (offline)     │         │  SSE real-time       │
   │  ⚠ Emergency button  │         │  Multi-device view   │
   │  Bilingual (EN/HI)   │         │  Event timeline      │
   └──────────────────────┘         └──────────────────────┘

   Observability: JSON logs → Better Stack · OTel → Tempo
                  Metrics → Prometheus + Grafana · Errors → Sentry
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

**Stack**: FastAPI 0.115+, Python 3.11+, Pydantic v2 for schema validation, Uvicorn for ASGI, deployed in Docker on Fly.io (chosen over Render to avoid cold-start tax on the free tier).

**Endpoints** (all under `/v1/`):

- `POST /v1/inference` — receives a 2.5 s window from a device (`payload_type=emergency`), runs the cloud model, returns `{is_fall, confidence, severity, action}`. JWT-gated (device tokens). Pydantic schema strictly validated.
- `POST /v1/retraining` — receives a 2.5 s window the user **canceled** during the local grace period (`payload_type=retraining_data`). Skips the cloud model entirely and stores it labeled `CANCELED_FALSE_ALARM` for later fine-tuning / per-user threshold tuning. Same window validation as `/v1/inference` (shared schema). See §3.2.
- `POST /v1/devices/pair` — pair a device to a user account. Takes an 8-character Crockford base32 pairing code. Rate-limited (5 attempts → exponential backoff).
- `GET /v1/devices` — list a user's paired devices with live status (online/offline, last-seen, battery).
- `GET /v1/events` — fall-event timeline for a user, paginated.
- `POST /v1/events/{event_id}/acknowledge` — caregiver acks an alert.

**Auth**: OAuth 2.0 + JWT. Access tokens 15 min, refresh tokens 30 days with rotation. Per-device JWTs separate from per-user JWTs.

**Storage**:

- **PostgreSQL 16** (Supabase) — system of record for users, devices, events, audit log. Row-level security ties events to user_id. Alembic for migrations.
- **Redis 7** — rate limiting (60 req/min per device, 10 pairing attempts/hr per IP), session cache, pub/sub channel that drives the Next.js dashboard's Server-Sent Events feed.
- **Firebase** — kept only for FCM push notifications. Firestore rules scoped to `request.auth.uid`. No Firestore as system-of-record.

### 2.3 Cloud ML — separate inference service

**Stack**: Python service, PyTorch model loaded into a long-lived process, exposed as an internal RPC to the gateway. Containerised separately so it can scale independently. MLflow-tracked: every deployed model is identifiable by run-id + semantic version + checksum, rollback-able from the registry.

**Model**: Transformer encoder (or 1D-CNN → LSTM hybrid — pick chosen empirically during training) operating on the 43-dim engineered feature vector for the 2.5 s window. Outputs:

- 3-class softmax over `{ADL, near-fall, true-fall}`.
- Regression head for severity (predicted peak acceleration magnitude).
- Calibrated probability via Platt-scaling or isotonic regression so the confidence number is trustworthy.

### 2.4 Mobile companion — Flutter

**Stack**: Flutter 3.x, Riverpod 2 for state, GoRouter for navigation, Drift (SQLite) for offline storage, `intl` for bilingual (English + Hindi). Material 3 with a custom design system (`FGButton`, `FGCard`, `FGStatusChip`).

**Key features**:

- Email/password + (later) biometric auth.
- 8-character alphanumeric pairing flow (replaces v2's brute-forceable 6-digit code).
- Live device status (online/offline, battery %, last-seen) — not just fall events.
- Fall-event timeline, per-device filtering.
- **Emergency button — actually built**: one-tap dial of the registered emergency contact + simultaneous SMS with the current GPS location.
- Acknowledgement flow: caregiver explicitly acks each alert; unacked alerts escalate after 60 s (SMS to caregiver, then to secondary contact).
- Accessibility: large-text + high-contrast modes, screen-reader labels (real elderly users need these).
- Offline: actions queued in Drift when offline, synced on reconnect.

### 2.5 Caregiver web dashboard — Next.js

**Stack**: Next.js 16 (App Router) + TypeScript + Tailwind v4 + Recharts/Tremor for charts. Real-time via Server-Sent Events from the gateway's Redis pub/sub channel.

**Features**:

- Multi-device view for caregivers responsible for >1 patient.
- Event timeline with severity colour-coding.
- Battery + signal heatmaps.
- Acknowledgement queue (the dashboard equivalent of the mobile ack flow).

### 2.6 Virtual device — Python IMU simulator

A standalone Python script in `virtual_device/` that emits the same JSON payload schema as the real ESP32 firmware sends to `/v1/inference`. Generates realistic IMU patterns:

- **ADL phases**: sine-wave + noise patterns for walking/sitting/standing.
- **Fall events**: 3-phase synthesis — free-fall (reduced gravity ~3–6 m/s²) → impact (transient spike to 20–30 m/s²) → post-impact (lying still at ~9.81 m/s² in a new orientation).

Drops into the same backend with zero changes. Lets development proceed without physical hardware, and gives the demo a reliable "fall trigger" button (vs. having to actually drop the device).

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
FastAPI gateway: validate JWT, validate schema, rate-limit, persist raw
  ↓
Gateway → ML service: run cloud model on full window
  ↓
Cloud confirms (is_fall=true, severity=high) OR cancels (is_fall=false)
  ↓
If confirmed:
   ├─ Insert into Postgres `events` table with severity + lead_time
   ├─ Publish on Redis channel `events:user:<id>`
   │    → Dashboard receives via SSE, alerts caregiver
   ├─ FCM push to all caregivers' mobile apps
   ├─ Schedule escalation job (Celery): if not acked in 60 s, SMS
   └─ Audit log entry
```

End-to-end latency budget: edge inference <80 ms, network round-trip <500 ms, cloud inference <500 ms, notification fan-out <1 s. From impact peak to caregiver phone notification = **~2 s** when network is good.

**Local grace period (false-alarm capture).** Before the watch streams anything, the haptic warning opens a ~10 s grace window. If the user presses **Cancel** (it wasn't a fall), the watch does *not* send an emergency: it silently uploads that same 2.5 s window to `POST /v1/retraining` (`payload_type=retraining_data`). The gateway **skips the cloud model** and stores the window labeled `CANCELED_FALSE_ALARM` for later fine-tuning / per-user threshold tuning — the user is ground truth for their own false alarms. This data-collection path is deliberately separate from the alerting path so a canceled trigger can never page a caregiver (see ADR-011). If the user does *not* cancel within the grace window, the flow above proceeds (`payload_type=emergency` → `/v1/inference`).

### 3.3 Acknowledgement

Caregiver taps "I've responded" in the mobile app or dashboard → `POST /v1/events/{id}/acknowledge` → ack inserted into events table → escalation job cancelled → ack visible to other caregivers in real-time via SSE.

---

## 4. The ML pipeline in detail

### 4.1 Two models, one shared pipeline

| Stage | Model | Job | Where it runs | Input | Constraints |
|---|---|---|---|---|---|
| Edge | ConvLSTM-tiny (INT8) | Pre-impact prediction (300–500 ms before ground impact) | TFLite Micro on ESP32-S3 | Raw 6-channel 125-sample window | ≤100 KB · <80 ms · INT8 |
| Cloud | Transformer encoder (or 1D-CNN→LSTM) | Post-impact confirmation + severity | FastAPI service on Fly.io | 43-dim engineered feature vector | FP32 · MLflow-tracked |

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

- **Structured JSON logs** with correlation IDs → Better Stack (free tier) for searchable storage
- **OpenTelemetry traces** across gateway → ML service → Postgres → notifier → Tempo or Honeycomb
- **Prometheus metrics** scraped by Grafana — p50/p95 latency per endpoint, error rate, model inference time, queue depth
- **Sentry** for errors with stack traces + breadcrumbs

The single most important metric to watch in production: **false positives per user per day**. Alert fatigue kills caregiver trust in the system, which is what makes the product fail in the real world even when the academic metrics look great.

---

## 7. Deployment topology

| Component | Hosting | Cost tier | Cold-start risk |
|---|---|---|---|
| FastAPI gateway | Fly.io (Docker) | Free / hobby | None — Fly keeps a min instance warm |
| Cloud ML service | Fly.io (separate Dockerfile) | Hobby | Mitigated by min instance |
| PostgreSQL | Supabase | Free tier | n/a |
| Redis | Upstash | Free tier | n/a |
| Caregiver dashboard | Vercel | Hobby | None (static-ish Next.js) |
| FCM | Firebase | Free tier | n/a |
| Object storage (for raw windows we keep) | Cloudflare R2 | Free tier (10 GB) | n/a |

Cost expectation at hobby scale (≤100 users, ≤10 fall events/day): **$0–10/month**. The cold-start concern that crippled v1/v2 on Render free tier is structurally avoided here (Fly.io min-instances).

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

## 9. Roadmap snapshot

(High-level. Detailed week-by-week build sequence is in the main plan file.)

- Week A (data foundation) — loader, label derivation, windowing, features, tests. ✅ Done.
- Week B — edge ConvLSTM-tiny baseline, INT8 quantize, latency benchmark. ✅ Done (96.5% recall on held-out subjects, ~46 KB INT8).
- **Now — Week C**: cloud Transformer detector + FastAPI gateway + Fly.io deploy. The gateway skeleton + both ingestion paths (`/v1/inference` emergency, `/v1/retraining` canceled-false-alarm capture) are in; the Transformer is next.
- **Then — Week D**: Flutter rebuild with Riverpod + GoRouter + emergency button + the **local grace period (10 s buzz + Cancel)** + offline.
- **Then — Week E**: Indian-ADL collection + retraining of both models, including **fine-tuning on collected `CANCELED_FALSE_ALARM` windows / per-user thresholds**.
- **Then — Week F**: TFLite-Micro deployment to ESP32-S3 (incl. the on-watch grace-period/Cancel UX), Next.js dashboard, observability stack, CI/CD, demo video.

**Personalization is a core feature, woven across C–E**: the local grace period + the canceled-false-alarm retraining loop let the system learn each user's non-falls. Architecture in §3.2/§8, rationale in ADR-011.

Beyond v3.0: edge-only mode (no cloud dependency), more datasets (Geriatric Wrist IMU), federated learning across users, custom PCB form factor.
