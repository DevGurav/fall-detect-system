# 🚀 Fall Guardian v3: Complete System Architecture & Tech Stack

> **As-built note.** This overview reflects the **shipped** system: a **local-first**
> deployment (Docker Compose + an ngrok HTTPS tunnel, not a managed cloud), the cloud
> model served **in-process as ONNX**, the **Flutter app as the sole caregiver client**
> (the Next.js web dashboard was dropped), and **SSE + additive FCM** alert routing.
> See `docs/ARCHITECTURE.md` and `docs/DECISIONS.md` ADR-013–018.

## 1. Edge Hardware & TinyML (The On-Device Brain)



**ESP32-S3 Microcontroller:** The hardware core of the wrist-worn device, selected for its high processing power, deep-sleep energy efficiency (targeting >24h battery life), and native BLE (Bluetooth Low Energy) networking capabilities.



**TensorFlow Lite for Microcontrollers (TFLite Micro):** A specialized execution framework utilized to run neural networks directly on the microcontroller under extreme memory constraints.



**ConvLSTM-tiny (INT8 Quantized):** The custom Edge ML model. It processes 50 Hz IMU (accelerometer/gyro) data using 1D-Convolutional layers for spatial features and LSTM units for sequential time-series prediction. It is quantized to INT8 (8-bit integers) to shrink the model to **~46 KB** and achieve sub-80 ms inference latency, predicting falls **~256 ms before impact** (measured mean lead time, 96.5% recall). It is **recall-first by design** — it fires often and lets the cloud model suppress the false alarms.




## 2. Core Backend API (The Traffic Controller)



**FastAPI (Python 3):** An asynchronous web framework used to build the REST API. Selected for its ultra-fast performance and native async capabilities, allowing the server to handle high-throughput telemetry streams without blocking.



**Pydantic v2:** Used for strict data validation and serialization. It ensures that incoming JSON payloads from the watch strictly match the required schema before they can touch the database or ML model, preventing runtime crashes.



**Server-Sent Events (SSE):** A unidirectional real-time protocol. It establishes a live text-event stream (`GET /v1/events/stream`) to instantly push fall alerts from the backend to the caregiver's **Flutter app while it is in the foreground**, avoiding the heavy overhead of WebSockets. For a backgrounded/killed app, **Firebase Cloud Messaging (FCM)** is the **additive** wake path — never duplicated, because the app ignores foreground FCM (ADR-016).




## 3. Authentication & Security Perimeter (The Shield)



**JWT (JSON Web Tokens) via PyJWT + bcrypt:** Implements stateless authentication, issuing short-lived per-user access tokens (with refresh-token rotation) and separate per-device JWTs that are securely stored in the ESP32's encrypted NVS (Non-Volatile Storage) partition. Passwords are bcrypt-hashed.



**Crockford Base32 Pairing Codes:** An 8-character, human-readable code generation system (excluding ambiguous characters like 'O' and '0') used to securely bind physical watches to user accounts, fortified with a 5-minute TTL (Time To Live).



**Postgres Row-Level Security (RLS):** Enterprise-grade database security. The API connects using a least-privilege fall_app role, and RLS policies guarantee that authenticated devices and users can only query rows explicitly tied to their specific user_id, physically preventing data leaks.




## 4. Data Persistence (The System of Record)



**PostgreSQL 16 (local Docker):** The primary relational database, run from `docker-compose.yml` on the host. It is the permanent system of record for users, devices, calibration profiles, FCM push tokens, retraining windows, the audit log, and historical event timelines. (The original Supabase plan was dropped with the move to local-first — ADR-017.)



**Alembic:** An Infrastructure-as-Code database migration tool. It allows for programmatic, version-controlled upgrades to the database schema without destroying existing production data.




## 5. In-Memory Caching & Real-Time Messaging (The Accelerator)



**Redis 7:** An ultra-fast, in-memory datastore used to manage high-speed state operations.



**Fixed-Window Rate Limiting:** Custom logic written over Redis to throttle endpoints (e.g., 10 pairing attempts/hour per IP), serving as a firewall against brute-force cyber attacks.



**Redis Pub/Sub:** A message broker pattern that decouples event ingestion from event broadcasting. When a fall occurs, the API publishes to a Redis channel, which the SSE endpoint subscribes to, ensuring the API doesn't bottleneck.




## 6. Cloud AI & MLOps Pipeline (The Core Brain)



**PyTorch:** The primary ML framework used to train the heavy Cloud Transformer model using subject-stratified cross-validation on the WEDA-FALL dataset.



**ONNX Runtime (Open Neural Network Exchange):** Used to export the trained PyTorch model into a portable format served **in-process inside the FastAPI gateway** (CPU provider) — fast, torch-free CPU inference with no separate model service (ADR-015). The active artifact is the **5-fold cross-validated** export; the prior Phase-20 baseline is preserved at `backend/app/model_old/` for one-line rollback/A-B via `FG_MODEL_PATH` (ADR-018).



**Transformer Encoder Architecture:** The cloud-side AI that receives the 2.5-second telemetry window triggered by the edge device, fused with a 43-dim engineered feature vector. It confirms true falls and suppresses false alarms (ADLs): standalone cloud recall ≈ 0.97 (5-fold OOF), and the **edge→cloud cascade drives the joint ADL false-positive rate to ≈ 0.7%**. The ≤ 0.5-alarms/day continuous-wear figure is scripted and still owed a literal pass.



**MLflow (+ Google Colab for GPU):** MLOps "digital lab notebook" tracking experiment iterations, hyperparameters, and metrics. Heavy training / 5-fold cross-validation scripts are written locally and executed on Colab's GPU ("write now, run later").




## 7. Mobile Application (The User Interface)



**Flutter 3.35 / Dart 3.9:** A cross-platform UI toolkit used to build the Android (and iOS-capable) companion app from a single codebase. This is the **sole caregiver client** — it covers login/registration, device pairing, calibration onboarding, the live alert feed, the event timeline + acknowledge, and the manual emergency SOS.

**Riverpod 3:** Manages complex, reactive application state (the SSE connection status, the live event feed, auth). Navigation is plain Flutter routing — a declarative router and an offline DB (Drift) were evaluated and **deprioritised** as résumé-driven over-engineering; the SSE reconnect loop + pull-to-refresh cover the real cases.

**Hand-rolled SSE consumer + FCM (`firebase_messaging`):** A ~150-line `FallEventService` over `package:http` owns reconnect/backoff/jitter and a keepalive **watchdog** for half-open sockets (ADR-012). **FCM** is wired as the **additive** background/killed-app wake path (ADR-016); `flutter_secure_storage` holds the JWTs.




## 8. Caregiver Web Dashboard — DROPPED (ADR-014)

The planned Next.js 16 + TypeScript + Tailwind v4 web dashboard was **not built**.
For an emergency alert the right form factor is a phone in a pocket, not a browser
tab, and the **Flutter app (§7) covers the caregiver completely** — so that build
time went to the on-device firmware (the actual differentiator) instead. The
gateway's SSE endpoint is transport-agnostic, so a web dashboard remains a clean
future add-on (open the same `GET /v1/events/stream` with a user JWT). There is no
`dashboard/` directory in the repo.




## 9. DevOps & CI/CD (The Automation Engine)



**Docker & Docker Compose:** `docker-compose.yml` brings up Postgres 16 + Redis 7 locally; the FastAPI app runs on the host and is exposed to a physical phone through a **secure ngrok HTTPS tunnel** to port 8000 — a **zero-cost, zero-latency** production-testing path (ADR-017). A multi-stage `backend/Dockerfile` is kept for reproducible builds and as the seam for a future managed re-deploy; the `FG_ENVIRONMENT=production` validator refuses to boot with the dev JWT secret.

**GitHub Actions:** The CI pipeline (`.github/workflows/ci.yml`) triggers on every push to run **ruff** lint + **pytest** (backend) + an **Alembic migration check** + **flutter test** (mobile). It gates PRs on green checks. There is **no auto-deploy** — the system is local-first by design.




## 10. Observability & Telemetry (The Monitoring Stack)



**Structured JSON logging + per-request trace IDs:** Every request's log lines share a trace ID for correlation (`backend/app/observability.py`).

**Better Stack (optional log drain):** When `FG_BETTER_STACK_TOKEN` is set, the JSON logs ship to Better Stack in addition to stdout; unset → stdout-only.

**Health + readiness probes:** `GET /health` (liveness, no I/O) and `GET /health/ready` (pings Postgres + Redis, reports the loaded `model_version`, 503 when degraded).

> OpenTelemetry/Tempo distributed tracing and Sentry were **deprioritised** for a
> local, single-operator deployment — the JSON logs + trace IDs + readiness probe
> are sufficient (see PLAN "Locked Tradeoffs").