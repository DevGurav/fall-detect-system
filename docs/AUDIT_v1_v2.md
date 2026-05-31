# Audit — v1 (`fall-detect-system`) and v2 (`fall-simulated`)

A layer-by-layer technical audit of the two pre-rebuild prototypes. Every defect listed has a concrete file:line citation. This document is the evidence base for the decision to rebuild from scratch ([ADR-001](DECISIONS.md#adr-001--rebuild-from-scratch-instead-of-patching-v1--v2)) and the source material for the v3 architecture choices in [`DECISIONS.md`](DECISIONS.md).

> **Reading this doc.** Each layer has the format: *what v1/v2 has* → *what's wrong with it* → *how v3 fixes it* (with a back-reference to the relevant ADR or file). The intent is for anyone (future-me, recruiter, collaborator) to be able to trace from a specific v1/v2 defect to the corresponding v3 design choice.

---

## Repositories audited

- **`Repos/fall-detect-system/`** — the 2nd-year engineering project. Stack: Flask (Python) backend + ESP32-S3 + MPU6050 wristband + Firebase (Firestore + FCM) + Flutter mobile app. Deployed to Render at `fall-detect-system.onrender.com`.
- **`Repos/fall-simulated/`** — sibling project, software-only. Stack: virtual device (Python) → Flask backend → separate ML API service → Firebase → Flutter app + vanilla-JS web dashboard. ML API deployed to Render at `fall-simulated.onrender.com`; backend ran locally.

Both projects are functional end-to-end. Both are deployed (at least partially). The defects below are not blockers to a demo — they're blockers to a *production-grade, recruiter-credible* portfolio piece.

---

## Layer 1 — Machine learning

### 1.1 Single-sample inference, no temporal window

**v1/v2.** The ML API predicts on a single 6-feature vector (`accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z` at one instant in time). See `fall-simulated/ml_api/app.py:133-137`:

```python
features = np.array([[
    accel.get('x', 0), accel.get('y', 0), accel.get('z', 9.8),
    gyro.get('x', 0), gyro.get('y', 0), gyro.get('z', 0)
]])
```

**What's wrong.** Real falls have a temporal pattern (loss-of-balance → free-fall → impact → stillness) that unfolds over hundreds of milliseconds. A model that sees only a single sample at the impact peak can't distinguish a fall from any other transient high-acceleration event (sitting down hard, dropping the wrist, hitting a table). Recall + FPR both suffer.

**v3 fix.** Sliding-window inference: every prediction sees a 2.5 s × 50 Hz = 125-sample window. See [`features/windowing.py`](../ml/src/fall_guardian_ml/features/windowing.py). [ADR-002](DECISIONS.md#adr-002--sequential-pipeline-edge-prediction--cloud-confirmation).

### 1.2 Training data: 1000 hardcoded synthetic samples

**v1.** `fall-detect-system/backend/create_model.py` generates 800 "normal" samples (low-variance Gaussian noise around `[0, 0, 9.81]`) + 200 "fall" samples (high-variance Gaussian around `[±8, ±8, varied]`).

**v2.** `fall-simulated/ml_api/app.py:22-25` does the same trick at startup if the model file is missing: 500 normal + 500 fall, even more crudely.

**What's wrong.** A RandomForest trained on Gaussian noise has not seen any real human motion. It can't recognise gait patterns, won't know how a real fall trajectory differs from a stumble-and-recover, and will fail catastrophically on the false-positive front in real wear. The "100% / 95% accuracy" the READMEs report is on an internal split of the same Gaussian noise — completely meaningless as a production metric.

**v3 fix.** Train on real wrist-worn fall + ADL datasets: WEDA-FALL primary, SmartFall for ADL augmentation, UP-Fall for cross-dataset generalisation testing. Plus a custom Indian-ADL supplement collected by us. Honest validation with subject-stratified k-fold CV and held-out test subjects. [ADR-006](DECISIONS.md#adr-006--wrist-only-training-datasets-weda-fall-primary-kfall--sisfall-rejected).

### 1.3 README claims features the code doesn't compute

**v2.** `fall-simulated/README.md` (lines 598–618) describes the feature pipeline as computing "accel_magnitude" and "jerk (rate of change)". The actual ML API code at `ml_api/app.py:123-130` only passes raw `(ax, ay, az, gx, gy, gz)` — no magnitude, no jerk, no derived features at all.

**What's wrong.** README → code mismatch is the worst kind of technical-debt because it gaslights anyone trying to understand the system. Documentation rot signals a project that's been abandoned mid-iteration.

**v3 fix.** [`features/extraction.py`](../ml/src/fall_guardian_ml/features/extraction.py) actually computes magnitude, jerk, SMA, per-channel statistics, dominant frequency, and spectral entropy. The README, DATA.md, and ARCHITECTURE.md all describe the same set the code produces. `feature_names()` is the single source of truth.

### 1.4 Wrong model family for the problem

**v1/v2.** `sklearn.ensemble.RandomForestClassifier(n_estimators=100, max_depth=10)`.

**What's wrong.** RandomForest treats each input vector as an independent observation — no notion of sequence. For IMU data (inherently temporal), CNN-LSTM / Transformer architectures are SOTA per the 2024–2026 literature (98%+ accuracy on benchmark datasets when trained correctly).

**v3 fix.** Two-model pipeline: ConvLSTM-tiny on the edge (handles sequence within ≤80 KB INT8), Transformer encoder in the cloud (full receptive field over the 43-dim feature window). [ADR-002](DECISIONS.md#adr-002--sequential-pipeline-edge-prediction--cloud-confirmation).

### 1.5 No model versioning, no retraining pipeline

**v1/v2.** Model is a single `fall_model.pkl` joblib dump committed to disk. No schema, no version, no timestamp, no checksum, no provenance.

**What's wrong.** Can't roll back, can't audit, can't compare experiments. Retraining overwrites silently.

**v3 fix.** MLflow tracks every experiment with parameters, metrics, and artifacts. Models are registered in the MLflow model registry with semantic versioning + checksums. The deployed model is identifiable by run-id; rollback is one CLI call. [ARCHITECTURE.md §4.7](ARCHITECTURE.md#47-validation-methodology).

### 1.6 No model confidence threshold enforcement

**v1.** Backend returns the raw RandomForest probability as-is. If `predict_proba()[1] = 0.51`, the system fires a notification.

**What's wrong.** No tuned threshold means false positives at the cusp class boundary. In a fall-detection product, false positives kill caregiver trust faster than missed events kill the product.

**v3 fix.** Calibrated probabilities (Platt-scaling or isotonic) so the 0.85 confidence number is actually trustworthy. Threshold tuned empirically for the FPR-on-ADL target (≤2% cloud, ≤5% edge). [ARCHITECTURE.md §4.7–4.8](ARCHITECTURE.md#47-validation-methodology).

### 1.7 Hardcoded device-side threshold ungates the cloud

**v1.** ESP32 firmware sends to the cloud only when `totalAccel > 2.5g` OR `gyroRotation > 5.0 rad/s` (see `ESP32_INTEGRATION.md` and the firmware sketch). Falls below those thresholds never reach the model.

**What's wrong.** The hardcoded threshold is the actual classifier in practice — the cloud model only sees pre-filtered cases. Hides the model's true capabilities and makes the system blind to subtle falls.

**v3 fix.** Edge ML on-device IS the first-pass classifier. Continuous inference with a learned threshold instead of a hand-tuned magnitude rule. [ADR-004](DECISIONS.md#adr-004--edge-first-hybrid-inference-tflite-micro-on-esp32-s3).

---

## Layer 2 — Backend

### 2.1 Zero authentication on any endpoint

**v1/v2.** `fall-simulated/backend/app.py:463-656` exposes `/api/sensor-data`, `/api/devices`, `/api/events`, `/predict`, `/register-device` — none with any auth check. Anyone with the URL can POST predictions, register devices, or enumerate events.

**v1.** `fall-detect-system/backend/main.py` is the same — no `@require_auth` decorator anywhere.

**What's wrong.** Catastrophic for a system handling health data. An attacker can spam fake predictions, inject fraudulent events, or learn the home schedules of paired patients.

**v3 fix.** OAuth 2.0 + JWT. Access tokens 15 min, refresh tokens 30 days with rotation. Per-device JWTs separate from per-user JWTs. Pydantic schema validation on every endpoint. [ARCHITECTURE.md §5](ARCHITECTURE.md#5-security-baseline).

### 2.2 Firestore rules: `allow read, write: if true;`

**v1.** Documented at line 156 of `fall-detect-system/README.md`. World-writable Firestore — anyone with knowledge of the project ID can read all fall events, register fake devices, or corrupt the event history.

**What's wrong.** No comment needed.

**v3 fix.** Postgres is the system of record with row-level security. Firestore is retained only for FCM, with rules scoped to `request.auth.uid`. [ARCHITECTURE.md §2.2](ARCHITECTURE.md#22-cloud-gateway--fastapi).

### 2.3 CORS wide open

**v1/v2.** `CORS(app)` with default settings (`CORS(app, resources={r"/api/*": {"origins": "*"}})` in v2's `backend/app.py:34`).

**What's wrong.** Any browser-origin can hit the API. Expands the attack surface unnecessarily.

**v3 fix.** CORS restricted to the dashboard's origin (`https://dashboard.fallguardian.app` or equivalent). All non-browser clients (ESP32, mobile) authenticate via JWT, which CORS doesn't apply to anyway.

### 2.4 Confidence threshold inconsistency

**v2.** `backend/app.py:550` uses `confidence > 0.7` to gate notifications. `config.py:40` defines `ML_CONFIDENCE_THRESHOLD = 0.85`. The config value is read but never used.

**What's wrong.** Magic numbers in code overriding documented config values is a classic source of confusion when the system misbehaves.

**v3 fix.** Single source of truth in `settings.py` (Pydantic-Settings). Code reads from settings; no inline magic numbers.

### 2.5 No input validation

**v2.** `backend/app.py:484-485` unpacks the sensor data with `.get('x', 0)` defaults — accepts any JSON shape, silently fills missing fields with zeros.

**What's wrong.** A malformed client (or attacker) can send `{"accelerometer": {}}` and get a "no fall" prediction without any indication anything went wrong. Garbage in, garbage out.

**v3 fix.** Pydantic v2 schemas on every endpoint with strict typing. Missing fields raise 422 with a clear error message.

### 2.6 No rate limiting

**v1/v2.** No `@limiter.limit()` decorators anywhere; no Redis-backed rate limit middleware.

**What's wrong.** A misbehaving device (or attacker) can hammer `/predict` and either drain the cloud budget or cause Firestore to throttle real events.

**v3 fix.** Redis-backed rate limiter middleware: 60 req/min per device JWT, 10 pairing attempts/hr per source IP. [ARCHITECTURE.md §5](ARCHITECTURE.md#5-security-baseline).

### 2.7 Logging is just `print()`

**v1/v2.** Throughout the backend codebases, error reporting uses `print(f"Error: {e}")`. No structured logging, no correlation IDs, no log level filtering.

**What's wrong.** When something breaks in production, the only debugging signal is the Render log tail — no way to filter, no way to correlate a user's report with the corresponding server-side events.

**v3 fix.** Structured JSON logs with correlation IDs (request_id propagated through gateway → ML service → notifier) shipped to Better Stack for searchable storage. Sentry catches exceptions with full stack traces + breadcrumbs. OpenTelemetry traces for cross-service request paths. [ARCHITECTURE.md §6](ARCHITECTURE.md#6-observability).

### 2.8 Rule-based fallback is a single threshold

**v2.** When the ML API times out (Render free-tier cold start, 30+ s), the backend falls back to `if magnitude > 20: fall_detected = True` at `backend/app.py:535-544`. Confidence is computed as `magnitude / 30.0`.

**What's wrong.** Crude. A single magnitude threshold can't distinguish a fall from any sufficiently energetic activity. The fallback was added because the ML API was unreliable, which is a symptom of choosing the wrong hosting tier.

**v3 fix.** No more cold-start tax (Fly.io min-instances). If the cloud model is unreachable, the edge model's prediction stands on its own — the device has already vibrated and the user has been warned. The cloud confirmation enriches the alert but isn't on the critical path.

---

## Layer 3 — Device pairing

### 3.1 6-digit numeric pairing code, no rate limit

**v2.** `backend/app.py:393-457` generates a 6-digit numeric code. The pairing endpoint compares it against the device record at line 419 without any rate limiting or attempt counter. The README itself flags this as "vulnerable to brute force".

**What's wrong.** 6 digits = 1,000,000 combinations. At typical web latency an attacker can iterate a million attempts in hours and pair any device they want.

**v3 fix.** 8-character alphanumeric code from Crockford base32 (no ambiguous chars like O/0, I/l/1) = ~10¹² combinations. 5-minute TTL. 5 attempts per source IP per hour → exponential backoff. [ARCHITECTURE.md §5](ARCHITECTURE.md#5-security-baseline).

### 3.2 Pairing code TTL not enforced

**v2.** `config.py:34` documents a 15-minute TTL. The actual pairing endpoint never checks the timestamp — codes are valid forever once generated.

**v3 fix.** TTL is enforced in the pairing handler via Postgres timestamp comparison + an `attempts_remaining` counter.

---

## Layer 4 — Mobile (Flutter)

### 4.1 "Emergency button" claimed in README but missing from code

**v2.** `fall-simulated/README.md` line 978 lists "Emergency services quick dial" as a feature. **No Dart code anywhere in `fall-simulated/flutter_app/lib/` implements this** — no button, no service, no `url_launcher` call.

**What's wrong.** The single most safety-critical feature of a fall-detection app for elderly users — gone. A user (or caregiver) reading the README would expect this and find nothing in production.

**v3 fix.** Actually built. One-tap dial of the registered emergency contact + simultaneous SMS with the current GPS location. Behind an accessibility-friendly, high-contrast UI control. [ARCHITECTURE.md §2.4](ARCHITECTURE.md#24-mobile-companion--flutter).

### 4.2 State management = Provider (older pattern)

**v1/v2.** Provider 6.x ChangeNotifier pattern.

**What's wrong.** Provider works but is older than necessary for new code in 2026. Riverpod 2 is the modern Flutter state-management standard — cleaner DI, better testability, less boilerplate.

**v3 fix.** Riverpod 2 throughout. [ARCHITECTURE.md §2.4](ARCHITECTURE.md#24-mobile-companion--flutter).

### 4.3 Hardcoded `localhost:5000` base URL

**v2.** `api_config.dart` defaults the backend URL to `http://localhost:5000`. The user has to edit the file (or the value) before the app can talk to any non-local backend.

**What's wrong.** Onboarding friction. A fresh install can't function without source-level changes.

**v3 fix.** Build-time environment variables (`API_BASE_URL`) with sensible defaults for staging + production builds. Single source of truth in a typed config service.

### 4.4 WebSocket imported but never used

**v2.** `pubspec.yaml` lists `web_socket_channel: ^2.4.0`. The library is imported in `notification_service.dart` but never instantiated — events are polled via HTTP `getFallEvents()` instead.

**What's wrong.** Dead dependency, misleading dependency tree, plus the polling design means stale events (refresh interval >> notification expectation).

**v3 fix.** Server-Sent Events from the backend's Redis pub/sub channel. Real-time event updates, lower battery cost than polling.

### 4.5 No tests

**v1/v2.** `flutter_app/test/` contains only the stub `widget_test.dart` that Flutter scaffolds by default.

**What's wrong.** Zero confidence that changes don't break the app. Refactors are scary, regressions are silent.

**v3 fix.** Unit tests on services + models, widget golden tests for the design-system components, Patrol for end-to-end flows. CI runs them on every PR.

### 4.6 No offline support

**v1/v2.** Every action assumes the network is available. No local DB, no sync queue. If the app is offline when a fall happens, the user sees nothing locally.

**What's wrong.** Elderly users in India often have intermittent connectivity. The app should be usable at least for viewing recent events and triggering the emergency button when offline.

**v3 fix.** Drift (SQLite) for local storage. Sync queue for actions taken offline (replayed on reconnect). Local fall-event cache for review.

### 4.7 Average UI

**v1/v2.** Stock Material Design. No custom theme, no design system, no semantic colour usage, no typography scale.

**What's wrong.** Looks like a tutorial project. Recruiters reading the codebase form impressions from screenshots.

**v3 fix.** Custom design system (`FGButton`, `FGCard`, `FGStatusChip`) with a deliberate palette + typography scale. Accessibility-first (large-text + high-contrast modes — real requirements for elderly users). Bilingual (English + Hindi).

---

## Layer 5 — Web dashboard

### 5.1 Vanilla HTML + JS

**v2.** `fall-simulated/web_dashboard/` is HTML + Socket.IO + Chart.js, served by the Flask backend. Inline styles in HTML.

**What's wrong.** No componentisation, can't reuse pieces, can't deploy independently of the backend.

**v3 fix.** Next.js 16 + TypeScript + Tailwind v4 + Recharts. Deployed independently on Vercel. Real-time via Server-Sent Events from the gateway. [ARCHITECTURE.md §2.5](ARCHITECTURE.md#25-caregiver-web-dashboard--nextjs).

---

## Layer 6 — DevOps

### 6.1 Render free-tier cold starts

**v2.** ML API on Render free tier sleeps after 15 min of inactivity. First request after a cold period takes 30–60 s — long enough that the backend's `requests.Timeout` triggers (default 5 s in `config.py:29`) and falls back to the crude magnitude-only rule.

**What's wrong.** Effectively makes the ML model unavailable during low-traffic periods. The system silently degrades to threshold-based detection at exactly the times when a real fall might happen (overnight when activity is low).

**v3 fix.** Fly.io with `min_instances = 1` on the hobby tier. Permanent warm instance, no cold-start tax. Cost: a few dollars a month — well within the project budget. [ARCHITECTURE.md §7](ARCHITECTURE.md#7-deployment-topology).

### 6.2 No CI/CD

**v1/v2.** No GitHub Actions, no automated test/lint/build pipeline. Deployment is manual `git push` and waiting for Render to redeploy.

**v3 fix.** GitHub Actions matrix: lint (ruff for Python, ESLint for TS, dart format for Flutter) → test (pytest, vitest, flutter test) → build (Docker, Next.js, Flutter) → deploy to staging on every PR; production on tag.

### 6.3 No monitoring / no staging

**v1/v2.** Production = main branch on Render. No staging environment. No metrics dashboards.

**v3 fix.** Staging environment on Fly.io that auto-deploys from `main`; production on tagged releases. Grafana dashboards for p50/p95 latency, error rate, model inference time. Sentry for errors. Better Stack for log search. [ARCHITECTURE.md §6](ARCHITECTURE.md#6-observability).

---

## Closing summary

The audit identified 20+ specific defects across ML, security, mobile UX, and DevOps. Each is real, each has an evidence citation in the existing code, and each is addressed in the v3 design with an explicit ADR or architectural decision. Together they made the case that patching v1/v2 was not the right move and that a from-scratch rebuild — `fall-guardian` — was justified.

The v1/v2 prototypes remain available in this repository's git history (any pre-rebuild commit, or the v1/v2 reference tags) for anyone who wants to see the original implementation that started this work in my 2nd year of engineering.
