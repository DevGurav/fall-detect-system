# Fall Guardian v3 — Run Journal

*What actually happens, phase by phase, the first time you boot and demo this system.*

This is not a how-to guide — RUN.md is that. This is a journal: what you observe, what
the system is doing under the hood, and why each step matters. Written as one complete
pass through the system, from cold laptop to a fall alert appearing on a physical phone.

---

## Before you start — the lay of the land

Open the repo root. Five subdirectories matter for a live run:

```text
fall-detect-system/
├── backend/            ← FastAPI gateway, ONNX detector, Postgres/Redis wiring
├── mobile/             ← Flutter caregiver app
├── virtual_device/     ← Python simulator that stands in for the real ESP32 watch
├── ml/                 ← training code + the WEDA-FALL raw dataset
└── docker-compose.yml  ← brings up Postgres 16 + Redis 7
```

The real ESP32-S3 wristband would POST sensor windows directly to the backend. Since
we don't have the hardware on the bench, `virtual_device.py` plays that role — it reads
actual recorded WEDA-FALL trials, resamples them to true 50 Hz, and fires the same JSON
envelope the firmware would send. The backend cannot tell the difference.

The backend never runs in a cloud. It runs on your laptop. A physical phone reaches it
through an ngrok tunnel. That tunnel is this project's "production-testing environment".

---

## Phase 1 — Docker boot: Postgres 16 + Redis 7

```powershell
docker compose up -d --wait
```

Docker pulls `postgres:16` and `redis:7-alpine` if they aren't cached, then starts both
containers. The `--wait` flag makes the command block until both containers pass their
health checks, so when the prompt returns you know the database is accepting connections,
not just that the container started.

What's inside the Postgres container right now: an empty `fall_guardian` database owned
by the `fall` superuser. No tables yet. The schema is managed by Alembic, not baked
into the image, which means you can wipe and recreate the DB (`docker compose down -v`)
without touching the codebase.

Redis starts with default config. It will hold two things: a per-user rate-limit counter
(a sliding window of recent requests, keyed by user id), and a per-user pub/sub channel
for the SSE caregiver feed. Neither of those keys exist yet — they'll appear when the
first requests come in.

---

## Phase 2 — Alembic migrations: building the schema

```powershell
cd backend
$env:FG_DATABASE_URL = "postgresql+asyncpg://fall:fall@localhost:5432/fall_guardian"
uv run alembic upgrade head
```

You run this as the `fall` superuser, not as `fall_app`. That distinction matters — the
migrations create the `fall_app` role itself. If you ran the migrations as `fall_app`,
the role wouldn't exist yet and Alembic would fail.

Alembic reads `alembic.ini`, finds the `env.py` in `alembic/`, and runs migrations in
order. The first few migrations create tables: `users`, `devices`, `events`,
`retraining_samples`, `calibration_profiles`, `audit_log`, `pairing_codes`. Then a
later migration creates the `fall_app` role and grants it the minimum permissions it
needs — `SELECT`, `INSERT`, `UPDATE` on specific tables. Then it enables Row Level
Security (RLS) on the sensitive tables (`events`, `retraining_samples`, `calibration_profiles`).

The RLS policies are the interesting part. On the `events` table, for example, there's
a policy that says: `USING (user_id = current_setting('app.user_id')::uuid)`. Every
time the gateway opens a DB transaction, the very first thing it does is
`SET LOCAL app.user_id = '<uuid of the authenticated user>'`. That GUC setting means
Postgres will automatically filter all queries on `fall_app`'s connection to show only
that user's rows. Even if the code somehow queried without a `WHERE user_id =` clause,
Postgres would enforce it. The superuser `fall` bypasses RLS, which is why migrations
run as `fall` but the live app runs as `fall_app`.

Alembic outputs something like:

```text
INFO  [alembic.runtime.migration] Running upgrade  -> 0001, initial schema
INFO  [alembic.runtime.migration] Running upgrade 0001 -> 0002, add RLS policies
INFO  [alembic.runtime.migration] Running upgrade 0002 -> 0003, create fall_app role
...
INFO  [alembic.runtime.migration] Running upgrade 0005 -> 0006, add pairing_codes ttl index
```

When it finishes, `alembic_version` holds the head revision. Schema is done.

---

## Phase 3 — Uvicorn boot: the gateway wakes up

```powershell
$env:FG_DATABASE_URL = "postgresql+asyncpg://fall_app:fall_app@localhost:5432/fall_guardian"
$env:FG_REDIS_URL    = "redis://localhost:6379/0"
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

This time you connect as `fall_app`, not `fall`. From this point forward, every DB
connection in the running app is bound by RLS.

Watch the startup log. Several things happen in sequence inside `app/main.py`'s
`lifespan` context manager:

**structlog wires up first.** Every subsequent log line will be a single JSON object:
`{"event": "...", "level": "info", "timestamp": "2026-06-15T...", "trace_id": null}`.
The `trace_id` is `null` at boot because no request has come in yet — it'll be
populated per-request by `TraceIDMiddleware`.

**CloudDetector loads the ONNX model.** It opens
`backend/app/model/cloud_detector.onnx` and `cloud_detector.meta.json`. The meta JSON
tells it the Platt scaling coefficients, the feature-extraction parameters, and the
decision threshold (`0.04997`). onnxruntime creates an `InferenceSession` in-process —
no subprocess, no model server, no RPC. The ONNX runtime is just a library call. The
log line looks like:

```json
{"event": "cloud_detector_loaded", "model_version": "cloud-transformer-v0.1",
 "threshold": 0.04997, "cv_status": "recovered", "level": "info"}
```

If the file were missing, the detector would fall back to a heuristic stub and log
`cloud_detector_using_stub`. The app still boots and serves requests — it just uses
a simple peak-threshold rule instead of the trained model.

**DB pool opens.** asyncpg creates a connection pool to Postgres. If the DB isn't up,
the pool creation fails and the app refuses to start. With the Docker healthcheck, this
hasn't been an issue in practice.

**Redis client connects.** A non-blocking ping confirms Redis is reachable. Rate
limiting and SSE pub/sub are both ready.

**FCM service initialises.** `FG_FIREBASE_CREDENTIALS` is set in `backend/.env` — the
service-account JSON downloaded from Firebase Console → Project settings → Service
accounts → Generate new private key, pasted as a single line. On boot the gateway logs
`FCM service initialised for project fall-guardian-v3`. The full killed-app alert path
is live alongside SSE.

**Uvicorn binds `0.0.0.0:8000`.** The `--host 0.0.0.0` is essential — `127.0.0.1`
would only accept connections from the laptop itself. With `0.0.0.0`, a physical phone
on the same Wi-Fi (or through the ngrok tunnel) can reach the gateway.

The final line:

```text
INFO:     Application startup complete.
```

Open `http://127.0.0.1:8000/health` in a browser:

```json
{
  "status": "ok",
  "version": "0.2.0",
  "model_version": "cloud-transformer-v0.1",
  "environment": "local"
}
```

The `model_version` confirms the 5-fold CV ONNX is loaded (not the stub). Open
`http://127.0.0.1:8000/health/ready` to see the full dependency report: database
`"ok"`, redis `"ok"`, model `"ok"`. The gateway is ready.

---

## Phase 4 — ngrok: giving the phone a door

```powershell
ngrok http 8000
```

ngrok opens a long-lived TCP connection to the ngrok edge network and prints something
like:

```text
Forwarding   https://a1b2-203-0-113-9.ngrok-free.app -> http://localhost:8000
```

From this point, any HTTPS request to `https://a1b2-203-0-113-9.ngrok-free.app` is
forwarded — with TLS terminated by ngrok's edge, then tunnelled over the encrypted
ngrok pipe — to your local backend at port 8000. The backend sees it as a plain HTTP
request from `127.0.0.1`; from the phone's perspective it's a proper HTTPS endpoint
with a valid certificate.

Why not just use the LAN IP? Two reasons. First, the killed-app FCM path (when FCM
credentials are set) requires TLS — FCM won't send a notification that routes back to a
plaintext endpoint. Second, the ngrok URL works from any network — not just the same
Wi-Fi. The phone can be on cellular and still reach the backend. The backend, model, and
data never leave the laptop.

The free-tier URL changes every time you restart ngrok. Make a note of it.

Sanity-check from the phone browser: open `https://a1b2-...ngrok-free.app/health`.
It should return the same health JSON you saw on the laptop. If it does, the tunnel is
working and the phone can see the backend.

---

## Phase 5 — Flutter app: first boot on the phone

```powershell
cd mobile
flutter run --dart-define=FG_BASE_URL=https://a1b2-203-0-113-9.ngrok-free.app
```

Flutter compiles the Dart code, bundles the assets, and installs the debug APK over
ADB. The first build takes a couple of minutes; subsequent runs with `r` (hot-reload)
are instantaneous.

The `--dart-define=FG_BASE_URL=...` injects the backend URL at compile time. The app
reads it with `String.fromEnvironment('FG_BASE_URL')`. This is how you switch between
physical phone (ngrok), same-Wi-Fi (LAN IP), and emulator (`http://10.0.2.2:8000`)
without touching source code.

On first launch, the app lands on the login screen. There's no "skip" — all meaningful
features require an authenticated user because the event feed is per-user.

---

## Phase 6 — Registration and the SSE connection

In the app, tap **Register**, enter an email and password, tap Submit.

The app calls `POST /v1/auth/register`. The backend hashes the password with bcrypt
(cost factor 12 — about 250ms on the backend, deliberately slow to resist brute force),
writes a row to `users`, and returns an `access_token` (15-minute HS256 JWT) plus a
`refresh_token` (longer-lived, stored in `refresh_tokens` table). The app stores the
access token in Flutter's `flutter_secure_storage` — the platform's hardware-backed
keychain.

After login, the app starts the SSE listener. It opens a long-lived HTTP GET request
to:

```http
GET /v1/events/stream
Authorization: Bearer <access_token>
Accept: text/event-stream
```

The backend verifies the JWT, extracts the user's UUID, and subscribes to a Redis
pub/sub channel keyed by that UUID: `events:user:<user_uuid>`. Then it sits in an async
loop, waiting. Every 15 seconds it sends a keepalive comment (`: keepalive\n\n`) to
keep the connection alive through proxies and NAT. The Flutter app ignores keepalive
lines — it only acts on `event: fall` frames.

On the **Live** tab you should see a "connected" status badge. That badge going green
is the SSE socket being open and healthy. If the token expires (15 min), the app's
Riverpod stream provider detects the disconnection, refreshes the token via
`POST /v1/auth/refresh`, and reconnects. This is invisible to the user.

---

## Phase 7 — Virtual device wakes up: trial discovery

Open a new terminal in `virtual_device/`. Run:

```powershell
python virtual_device.py --kind fall --list
```

The script scans `ml/data/raw/WEDA-FALL-main/dataset/50Hz/`. Each subdirectory is an
activity code (`F01`, `F02`, ..., `D01`, `D02`, ...). Inside each activity dir are
CSV pairs: `U01_R01_accel.csv` + `U01_R01_gyro.csv` for each subject-run combination.
The script builds a `Trial` dataclass for each matched pair, then prints them:

```text
142 trials under .../50Hz:
  F01/U01_R01  (fall)
  F01/U01_R02  (fall)
  ...
  D04/U23_R01  (adl)
```

142 trials — that's the full WEDA-FALL dataset. The script knows which are falls and
which are ADLs from the directory name prefix: `F*` = fall, `D*` = ADL.

---

## Phase 8 — The pairing handshake: a three-step auth dance

```powershell
python virtual_device.py \
  --pair --email YOUR_APP_EMAIL --password YOUR_APP_PASSWORD \
  --kind fall --count 1
```

The `--pair` flag triggers a real server-side pairing, not the local-mint shortcut.
Three HTTP calls happen before a single window is sent:

**Step 1 — Register or login.**
The script posts to `POST /v1/auth/register` with the same email+password you used in
the app. The backend sees the email is already registered (the app did it in Phase 6)
and returns HTTP 409 Conflict. The script catches 409 and falls back to
`POST /v1/auth/login`. Login succeeds — the script now holds a 15-minute access token
for your user account. This is the "the watch belongs to this user" handshake.

**Step 2 — Mint a pairing code.**
The script posts to `POST /v1/devices/pairing-codes` with the user's access token.
The backend generates an 8-character Crockford base32 code (e.g., `7YH3MQ42`), stores
it in the `pairing_codes` table with a 5-minute TTL and a 5-attempt limit, and returns
it. In a real-world flow, the user would see this code in the mobile app and type it
onto the watch's tiny display. Here the script gets both the user and device side, so
it just uses the code immediately.

**Step 3 — Redeem the code, get a device token.**
The script posts to `POST /v1/devices/pair` with the code and the device's logical ID
(`sim-watch-01` by default). No user auth header — the code itself is the credential.
The backend verifies the code (exists, not expired, not burned through), writes a row
to `devices`, marks the code as redeemed, and issues a **per-device JWT** (365-day
expiry, contains `typ: "device"`, `did: "sim-watch-01"`, `uid: <user_uuid>`). This
device token is what the script will use for all subsequent inference requests — it
proves "this device belongs to this user" without needing the user's password again.

The script prints: `paired — received a device token`.

---

## Phase 9 — Building the fall window: from CSV to the §8 envelope

Now the script picks a random fall trial (e.g., `F01/U01_R01`) and builds the window.

**Reading the CSVs.** `F01/U01_R01_accel.csv` has 4 columns: time, ax, ay, az. The
Fitbit Sense that recorded WEDA-FALL batched samples over Bluetooth — delivery was
bursty. The timestamps are real but non-uniform: samples might land at 17ms, 22ms,
19ms, 20ms intervals rather than exactly 20ms (50 Hz). The script reads the raw times
and values separately.

**Centering on the impact.** For fall trials, the script loads `fall_timestamps.csv`,
which has the labeled start+end of each fall event. It resamples onto a dense 50 Hz
grid over that interval and finds the peak acceleration magnitude — that's the impact
instant. The 2.5-second window is placed so that 1.0 second of pre-impact data is
included. This matters: the edge model (ConvLSTM-tiny on the real watch) is trained to
detect the pre-impact signature, the characteristic arm-throw and body tilt that
precedes impact. Without the pre-impact lead, the edge model wouldn't have anything to
trigger on.

**Uniform 50 Hz via np.interp.** The script creates a uniform time grid of 125 points
(0ms, 20ms, 40ms, ..., 2480ms) starting from the window_start time, then calls
`np.interp(grid, raw_times, raw_values)` for each of the 6 channels (ax, ay, az, wx,
wy, wz). This is a linear interpolation — for each uniform grid point, it finds the two
nearest raw samples and linearly interpolates between them. The result is a perfectly
uniform 50 Hz signal, which is what the cloud model expects.

**Assembling the §8 envelope.** The 125 sample dicts are packed into:

```json
{
  "device_id": "sim-watch-01",
  "ts_start_unix_ms": 1750012345678,
  "sample_rate_hz": 50,
  "payload_type": "emergency",
  "edge_prediction": {"p_pre_impact": 0.9, "model_version": "edge-sim-0.1"},
  "samples": [
    {"ax": -0.2341, "ay": 9.7812, "az": 0.1023, "wx": 0.0034, "wy": -0.0121, "wz": 0.0089},
    ...
    /* 125 samples total */
  ]
}
```

`payload_type: "emergency"` is what routes this to `/v1/inference` (the detection
path). The `edge_prediction` field simulates what the real ESP32 would attach — the
probability the on-device ConvLSTM-tiny assigned to "pre-impact detected". The cloud
model doesn't use this for its own prediction, but it's logged for analysis.

---

## Phase 10 — POST /v1/inference: the detection pipeline

The script posts the envelope to `POST /v1/inference` with the device JWT.

On the backend, `TraceIDMiddleware` mints a `trace_id` for this request (a UUID hex
string) and binds it to structlog's context. Every log line for this request will carry
that trace_id, so you can grep the log for a single request's full story.

The router at `routers/inference.py` validates the JWT — it's a device token
(`typ: "device"`), the device exists in `devices`, it's linked to a user. The device's
last-heartbeat timestamp is updated.

The router hands the 125×6 payload to `CloudDetector.predict()`. Here's what happens
inside:

1. **Feature extraction.** The 125×6 array is passed through the same feature pipeline
   used during training: statistical features per channel (mean, std, min, max,
   percentiles), cross-channel correlations, spectral features (FFT-based energy in
   frequency bands). This produces a 43-dimensional feature vector.

2. **Per-user calibration.** If a `CalibrationProfile` exists for this user, the
   channel means/stds and feature means/stds are used to standardize the input. On a
   fresh account (no calibration yet), the global training statistics from the meta JSON
   are used.

3. **ONNX inference.** The 43-feature vector is passed to onnxruntime's
   `InferenceSession.run()`. The Transformer encoder processes it and outputs a raw
   logit. The Platt scaling layer (coefficients from meta.json: `coef=1.898`) converts
   the logit to a calibrated probability. This is the model's confidence that this
   window represents a fall.

4. **Threshold decision.** If `p_fall >= 0.04997` (the threshold from 5-fold CV), the
   model confirms the fall. This threshold is set deliberately low — it prioritizes
   recall. The cascade logic (edge pre-screen → cloud gate) handles precision; the cloud
   model's job is to not miss real falls.

For a real WEDA-FALL trial window, the confidence might be something like `0.823`. The
model confirms it.

---

## Phase 11 — EventStore: persistence, pub/sub, and FCM

The confirmed fall reaches `EventStore.record_fall()`. Three things happen, in order:

**1. Postgres write.** An `events` row is inserted:

```sql
INSERT INTO events (id, user_id, device_id, severity, confidence, is_fall, ts, ...)
VALUES (gen_random_uuid(), '<user_uuid>', 'sim-watch-01', 'high', 0.823, true, now(), ...);
```

Because the connection is `fall_app` and `SET LOCAL app.user_id = '<user_uuid>'` was
called at the start of the transaction, RLS is active. If you were somehow in the wrong
user's session, Postgres would reject the insert. The row is now in the system of record.

**2. Redis pub/sub publish.** `redis.publish(f"events:user:{user_uuid}", json_payload)`
pushes the fall event to the channel the SSE endpoint is subscribed to. Redis delivery
is in-memory and sub-millisecond — the message is in the channel almost
instantaneously.

**3. FCM push.** `FcmService.send_fall_notification()` calls the FCM HTTP v1 API via
`httpx`, using the service-account credentials in `backend/.env`. It fires a push to
the device token the phone registered at sign-in (`PUT /v1/users/me/push-token`). The
call is non-fatal by design — a transient FCM error is logged but never blocks the SSE
delivery above. This is what covers a *killed* app: when the phone is fully swiped
away, the SSE socket isn't running, but the OS delivers the FCM notification to wake it.

The structured log for this whole operation:

```json
{"event": "fall_event_recorded", "user_id": "...", "device_id": "sim-watch-01",
 "confidence": 0.823, "severity": "high", "db": "ok", "redis": "published",
 "fcm": "sent", "trace_id": "a3f2...", "level": "info"}
```

---

## Phase 12 — The phone gets the alert

The Redis publish from Phase 11 is received by the backend's SSE loop — the one that's
been sitting in `await redis.listen()` since Phase 6. It formats an SSE frame:

```text
event: fall
data: {"event_id": "...", "device_id": "sim-watch-01", "severity": "high",
       "confidence": 0.823, "ts": "2026-06-15T10:23:44Z"}

```

That frame is flushed down the open HTTP/1.1 chunked response to the phone. From the
phone's perspective, data just appeared on a connection it's been holding open.

In the Flutter app, `SseClient` (a hand-rolled client on top of `http` + `dart:io`)
receives the chunk, parses the `event: fall` line, decodes the JSON data, and posts it
to a Riverpod `StateNotifierProvider`. Riverpod rebuilds the widgets that depend on
that provider.

**If the Live tab is on screen:** an animated red alert card slides down. It shows the
device ID, severity, confidence score, and a timestamp. There's an Acknowledge button.

**If you're on another tab or the app is backgrounded:** the app's notification layer
(wired in `alert_providers.dart`) detects that the Live tab isn't the current route and
fires a local OS notification via `flutter_local_notifications`. On Android, this is
a full-screen intent — the system overlays the notification even over other apps, and
a sound plays. The phone lights up.

The timing from `POST /v1/inference` to the alert appearing on screen is typically
under 200ms on a local network — the ONNX inference is ~5ms, the DB write is ~10ms,
the Redis publish is ~1ms, and the SSE flush is network-speed. On ngrok, add the round
trip through the tunnel (~30ms typical). Either way, it's fast enough that it feels
instantaneous.

---

## Phase 13 — Acknowledging the fall

Tap the alert card on the phone (or navigate to the **Timeline** tab). The Timeline
calls `GET /v1/events` — returns the last N fall events for the authenticated user,
filtered by RLS to only this user's events. The unacknowledged fall appears at the top.

Tap **Acknowledge**. The app calls `PATCH /v1/events/<event_id>/acknowledge`. The
backend sets `acknowledged_at = now()` on the row. The event disappears from the
"active alerts" view and moves to history. In a real deployment this would trigger a
follow-up check or log the caregiver's response time.

---

## Phase 14 — ADL suppression: when the model says "not a fall"

Run:

```powershell
python virtual_device.py --pair --email YOU --password PW --kind adl --count 3
```

The script picks 3 ADL trials (e.g., `D04/U07_R01` — sitting down fast). Each goes
through the same `POST /v1/inference` pipeline as a fall. Feature extraction runs.
ONNX inference runs. But the output probability is, say, `0.011` — well below the
`0.04997` threshold. The CloudDetector returns `is_fall=False`.

The response back to the virtual device is:

```json
{"is_fall": false, "confidence": 0.011, "severity": null, "action": "suppressed"}
```

`EventStore.record_fall()` is never called. Nothing is written to Postgres. Nothing is
published to Redis. The phone is silent. The virtual device prints:

```text
1/3 [D04/U07_R01] HTTP 200 is_fall=False confidence=0.011 severity=None action=suppressed
```

This is the cascade working: the edge model's pre-screen triggers on aggressive motion
(the device sends it as an emergency), but the cloud gate's precision check determines
it's not a fall. The ADL false alarm is suppressed before it ever becomes an alert.

Run `--kind both --count 10` to see a mix: some confirmed, some suppressed, interspersed.

---

## Phase 15 — The false alarm / retraining path

```powershell
python virtual_device.py --pair --email YOU --password PW --kind adl --count 1 --false-alarm
```

This is a different scenario. The real watch has a button the elderly person can press
within a few seconds of an alert: "no, I'm fine, that was a false alarm." When that
happens, the firmware packages the same window that triggered the alert and sends it
with `payload_type: "retraining_data"` to `POST /v1/retraining`. The `--false-alarm`
flag simulates this.

The backend routes `payload_type: "retraining_data"` to the retraining endpoint
instead of inference. No CloudDetector is invoked. The window is stored in
`retraining_samples` with `label: "adl"` and the user_id. No event is recorded. No
alert fires.

The virtual device prints:

```text
1/1 [D04/U07_R01] HTTP 200 stored=True label=adl
```

These labeled samples accumulate over time. Periodically (or on demand), the ML
pipeline can pull them from the DB and fine-tune the model with the user's own
correction signal. That's the per-user fit-at-first vision from ADR-013 — the
Indian-ADL collection was dropped, but this path seeds the same idea through real usage.

---

## Phase 16 — Checking what landed in Postgres

```powershell
cd backend
uv run python scripts/integration_smoke.py
```

The smoke script drives a full programmatic pass: register → pair → heartbeat →
inference (fall) → events → acknowledge. Each step asserts the expected HTTP status and
checks that the event actually landed in the DB. This is the best end-to-end sanity
check short of running the full app — no phone required.

Or connect directly to Postgres:

```powershell
docker exec -it fall-guardian-postgres psql -U fall fall_guardian
```

```sql
SELECT device_id, severity, confidence, acknowledged_at, ts
FROM events
ORDER BY ts DESC
LIMIT 5;
```

You'll see the rows written during this session. `acknowledged_at` is non-null for the
event you acknowledged through the app.

---

## Phase 17 — Teardown

```powershell
# Keep the data (Postgres volume persists):
docker compose down

# Full wipe (next run starts from an empty DB):
docker compose down -v
```

The ngrok tunnel closes when you `Ctrl-C` its terminal. The backend closes cleanly when
you `Ctrl-C` uvicorn — the lifespan handler runs `on_shutdown`, closes the DB pool and
Redis client, and logs `application_shutdown`.

---

## What this one pass proves

- **The sequential pipeline works end-to-end.** A real WEDA-FALL recording is loaded,
  resampled to true 50 Hz, packed into the firmware's wire format, and classified by
  the 5-fold cross-validated Transformer ONNX model. The model's prediction (trained
  in Google Colab, exported to ONNX, committed to the repo) runs in-process in the
  FastAPI gateway with zero external dependencies.

- **The alert routing is correct.** SSE delivers foreground alerts in under 200ms. FCM
  fires additively for the killed-app case — credentials are set, both paths are live,
  and the app ignores foreground FCM so a single fall produces exactly one alert.

- **Security holds.** RLS means one user can never see another's events. The pairing
  handshake uses short-lived codes. Device tokens are long-lived but scoped to one
  device. The app refuses to boot with the dev JWT secret outside `local`.

- **The virtual device is a faithful stand-in.** The backend has no idea it's talking
  to a Python script rather than an ESP32. The envelope, auth, and routing are
  identical. When real hardware ships, only the firmware changes — the backend and app
  stay the same.

---

*Written as a live-run journal, 2026-06-16. System state: Phase 32 complete, all
backend weeks A–F shipped, active model cloud-transformer-v0.1 (5-fold CV).*
