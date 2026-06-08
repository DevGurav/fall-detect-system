# 🚀 Fall Guardian v3: Complete System Architecture & Tech Stack


## 1. Edge Hardware & TinyML (The On-Device Brain)



**ESP32-S3 Microcontroller:** The hardware core of the wrist-worn device, selected for its high processing power, deep-sleep energy efficiency (targeting >24h battery life), and native BLE (Bluetooth Low Energy) networking capabilities.



**TensorFlow Lite for Microcontrollers (TFLite Micro):** A specialized execution framework utilized to run neural networks directly on the microcontroller under extreme memory constraints.



**ConvLSTM-tiny (INT8 Quantized):** The custom Edge ML model. It processes 50 Hz IMU (accelerometer/gyro) data using 1D-Convolutional layers for spatial features and LSTM units for sequential time-series prediction. It is quantized to INT8 (8-bit integers) to shrink the model size to ~80 KB and achieve sub-80ms inference latency, predicting falls before impact.




## 2. Core Backend API (The Traffic Controller)



**FastAPI (Python 3):** An asynchronous web framework used to build the REST API. Selected for its ultra-fast performance and native async capabilities, allowing the server to handle high-throughput telemetry streams without blocking.



**Pydantic v2:** Used for strict data validation and serialization. It ensures that incoming JSON payloads from the watch strictly match the required schema before they can touch the database or ML model, preventing runtime crashes.



**Server-Sent Events (SSE):** A unidirectional real-time protocol. It establishes a live text-event stream to instantly push fall alerts from the backend to the caregiver dashboard, avoiding the heavy overhead and persistent connections required by WebSockets.




## 3. Authentication & Security Perimeter (The Shield)



**JWT (JSON Web Tokens) & Authlib:** Implements stateless authentication, issuing short-lived access tokens and per-device JWTs that are securely stored in the ESP32's encrypted NVS (Non-Volatile Storage) partition.



**Crockford Base32 Pairing Codes:** An 8-character, human-readable code generation system (excluding ambiguous characters like 'O' and '0') used to securely bind physical watches to user accounts, fortified with a 5-minute TTL (Time To Live).



**Postgres Row-Level Security (RLS):** Enterprise-grade database security. The API connects using a least-privilege fall_app role, and RLS policies guarantee that authenticated devices and users can only query rows explicitly tied to their specific user_id, physically preventing data leaks.




## 4. Data Persistence (The System of Record)



**PostgreSQL 16 (via Supabase):** The primary relational database. It acts as the permanent system of record for users, devices, calibration profiles, and historical event timelines.



**Alembic:** An Infrastructure-as-Code database migration tool. It allows for programmatic, version-controlled upgrades to the database schema without destroying existing production data.




## 5. In-Memory Caching & Real-Time Messaging (The Accelerator)



**Redis 7:** An ultra-fast, in-memory datastore used to manage high-speed state operations.



**Fixed-Window Rate Limiting:** Custom logic written over Redis to throttle endpoints (e.g., 10 pairing attempts/hour per IP), serving as a firewall against brute-force cyber attacks.



**Redis Pub/Sub:** A message broker pattern that decouples event ingestion from event broadcasting. When a fall occurs, the API publishes to a Redis channel, which the SSE endpoint subscribes to, ensuring the API doesn't bottleneck.




## 6. Cloud AI & MLOps Pipeline (The Core Brain)



**PyTorch:** The primary ML framework used to train the heavy Cloud Transformer model using subject-stratified cross-validation on the WEDA-FALL dataset.



**ONNX Runtime (Open Neural Network Exchange):** Used to export the heavy PyTorch models into a highly optimized, hardware-agnostic format, allowing for lightning-fast, CPU-bound inference in the FastAPI production cloud gateway.



**Transformer Encoder Architecture:** The cloud-side AI that receives the 2.5-second telemetry window triggered by the edge device. It processes sliding-window classifications to confirm true falls and suppress false alarms (ADLs), targeting a False Positive Rate of ≤ 0.5 per day.



**MLflow & DVC:** MLOps tools used as a "digital lab notebook" to track experiment iterations, log hyperparameter changes, and version datasets.




## 7. Mobile Application (The User Interface)



**Flutter 3.x:** A cross-platform UI toolkit used to build the iOS and Android companion app from a single codebase, featuring full bilingual support (English/Hindi).



**Riverpod 2 & GoRouter:** Riverpod manages complex, reactive application state, while GoRouter handles declarative, deep-linkable navigation screens.



**Drift (SQLite):** An offline-first local database layer. It queues user actions and telemetry locally if the phone loses internet, automatically syncing with the cloud once connectivity is restored.




## 8. Caregiver Web Dashboard (The Command Center)



**Next.js 16 & TypeScript:** A React-based web framework utilizing strict typing to build a multi-device web dashboard. It continuously consumes the SSE feed to display real-time event timelines.



**Tailwind v4:** A utility-first CSS framework used for rapid UI development and ensuring responsive, accessible design across all screen sizes.




## 9. DevOps & CI/CD (The Automation Engine)



**Docker & Docker Compose:** Containerization tools that package the FastAPI app, Postgres DB, and Redis into isolated microservices. This guarantees the application runs identically on a local laptop and the production server.



**GitHub Actions:** The CI/CD (Continuous Integration/Continuous Deployment) pipeline. It automatically triggers on every code push to run linting rules (ruff), security audits (bandit, snyk), and unit tests before deploying the Docker image to the cloud.




## 10. Observability & Telemetry (The Monitoring Stack)



**Better Stack:** Aggregates structured JSON logs, allowing for fast, queryable debugging of the backend in production.



**OpenTelemetry & Tempo:** Implements distributed tracing to measure exactly how many milliseconds a request spends in the database, the rate-limiter, or the ML model.



**Sentry:** Captures unhandled exceptions and application crashes, instantly alerting the developer to production bugs.