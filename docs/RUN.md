# 🚀 Fall Guardian — Complete Run & Demo Guide

This is the full, end-to-end runbook for the **local-only** deployment: boot the
backend, run the mobile app, fire a synthetic fall from the virtual device, and
**get notified on your phone**. It also covers exactly **how to record the demo**.

> **Deployment note:** this project runs **entirely on your laptop**. There is no
> Fly.io / Render / cloud deploy — the managed-cloud config was removed on purpose
> (see [`ARCHITECTURE.md`](ARCHITECTURE.md) §7). Postgres + Redis run in Docker;
> the backend + mobile app run on the host. A **physical phone** reaches the
> backend through a **secure [ngrok](https://ngrok.com) tunnel** to port `8000` —
> a public HTTPS URL at **zero cost and zero added latency** that lets a real
> handset talk to the real backend (this is our production-testing environment).
> See [§2a](#2a-expose-the-backend-to-a-physical-phone-ngrok).

---

## 0. The flow you're demonstrating

```
┌────────────────┐   POST /v1/inference    ┌─────────────────────┐
│ virtual_device │ ──────(fall window)───▶ │  Backend (FastAPI)  │
│  (the "watch") │   per-device JWT        │  CloudDetector ONNX │
└────────────────┘                         └─────────┬───────────┘
                                                      │ fall confirmed
                                            ┌─────────▼───────────┐
                                            │ EventStore          │
                                            │  • write to Postgres│
                                            │  • publish to SSE ──┼──▶ ┌───────────────┐
                                            │  • push FCM (opt.)  │    │  Mobile app   │
                                            └─────────────────────┘    │  • live alert │
                                                                       │  • OS notif.  │
                                                                       └───────────────┘
```

**Two ways the phone gets told about a fall:**

1. **SSE live feed (works today).** The app holds an open `GET /v1/events/stream`
   connection. A confirmed fall is pushed down that stream → the app shows an
   in-app red alert (on the **Live** tab) or fires an **OS notification** (when
   you're on another tab or the app is backgrounded). No Firebase required.
2. **FCM push (fully wired — credentials set).** For waking a *fully killed* app,
   the backend pushes via Firebase Cloud Messaging. Both sides are done: the
   Flutter app registers a real token and the backend has `FG_FIREBASE_CREDENTIALS`
   set in `backend/.env`. See [§6](#6-firebase-fcm-the-3rd-step--wakes-a-killed-app).

**Both paths are live.** SSE handles foreground; FCM covers killed-app. Either way
the phone gets the alert — no demo workarounds needed.

> ⚠️ **One account links the watch and the phone.** The SSE feed only notifies the
> *owner* of the device. So the virtual_device must pair under the **same email**
> the mobile app is logged into. The steps below handle that.

---

## 1. Prerequisites (one-time)

- **Docker Desktop** running.
- **Backend deps:** `uv` installed. From `backend/`: `uv sync --extra dev`.
- **virtual_device deps:** from `virtual_device/`: `pip install -r requirements.txt`.
- **Mobile:** Flutter SDK; a phone (USB, *Developer options → USB debugging*) **or**
  an Android emulator. Confirm `flutter devices` lists it.
- **ngrok** (for a **physical phone**): install from [ngrok.com](https://ngrok.com),
  then `ngrok config add-authtoken <token>` once (free account). See [§2a](#2a-expose-the-backend-to-a-physical-phone-ngrok).
- **WEDA-FALL dataset** downloaded to `ml/data/raw/WEDA-FALL-main/dataset/50Hz/`
  (see `ml/DATA.md`). The virtual device replays real trials from here.

> **Address cheat-sheet** — what `FG_BASE_URL` the phone should use:
>
> - **Physical phone (recommended): the ngrok HTTPS URL** — `https://<sub>.ngrok-free.app`
>   (public, TLS, works on any network; required for the killed-app FCM path). See [§2a](#2a-expose-the-backend-to-a-physical-phone-ngrok).
> - **Physical phone (same Wi-Fi only, plaintext fallback):** `http://<your-LAN-IP>:8000`
>   (`ipconfig` → "IPv4 Address" on your Wi-Fi adapter, e.g. `192.168.29.120`).
> - **Android emulator:** `http://10.0.2.2:8000` (the emulator's alias for the host).
>
> The virtual_device runs *on the laptop*, so it always uses `http://127.0.0.1:8000`.

---

## 2. Start the backend (DB-backed)

You need the **DB-backed** mode for the full account → pairing → events → SSE flow.
Use **two roles**: migrate as the owner `fall`, then run as the least-privilege
`fall_app` so Postgres RLS actually enforces.

**Terminal 1** — keep this open the whole demo.

```powershell
# From the repo root: Postgres 16 + Redis 7
docker compose up -d --wait

cd backend

# (a) Migrate as the OWNER — creates schema, RLS policies, and the fall_app role
$env:FG_DATABASE_URL = "postgresql+asyncpg://fall:fall@localhost:5432/fall_guardian"
uv run alembic upgrade head

# (b) Run the gateway as the NON-SUPERUSER fall_app (so RLS binds)
$env:FG_DATABASE_URL = "postgresql+asyncpg://fall_app:fall_app@localhost:5432/fall_guardian"
$env:FG_REDIS_URL    = "redis://localhost:6379/0"     # enables rate limiting
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Wait for **`Application startup complete.`** Sanity-check in a browser:
`http://127.0.0.1:8000/docs` (Swagger) and `http://127.0.0.1:8000/health`.

> `--host 0.0.0.0` is what lets a **physical phone** reach the laptop (over Wi-Fi
> or via the ngrok tunnel below). If Windows Firewall prompts on first run,
> **Allow** it on private networks.

---

## 2a. Expose the backend to a physical phone (ngrok)

**Skip this for the Android emulator** (it uses `http://10.0.2.2:8000` directly).
For a **physical phone**, this is the recommended path — it gives the laptop a
**public HTTPS URL** so the handset can reach the backend from any network, and it
satisfies the TLS expectation of the killed-app FCM push. **Zero cost, zero added
latency** (the tunnel just forwards to the host `:8000` you already started in §2);
nothing is deployed to the cloud.

**Terminal 1b** — leave this open alongside the backend:

```powershell
ngrok http 8000
```

ngrok prints a forwarding line, e.g.:

```text
Forwarding   https://a1b2-203-0-113-9.ngrok-free.app -> http://localhost:8000
```

Copy that `https://…ngrok-free.app` URL — it is the `FG_BASE_URL` the **phone**
uses in §3. Sanity-check it from the phone's browser:
`https://<sub>.ngrok-free.app/health` should return the JSON health payload
(including the live `model_version`).

> **Notes.** The free URL changes each time you restart ngrok — re-run §3 with the
> new URL (or use a reserved domain on a paid plan). ngrok is a *dev/testing*
> tunnel, not a hosting product: the backend, model, and data never leave your
> laptop, and you tear the tunnel down with `Ctrl-C` when you're done.

---

## 3. Run the mobile app

**Terminal 2** — from the repo root, `cd mobile`.

```powershell
# Physical phone (recommended) — paste the ngrok HTTPS URL from §2a:
flutter run --dart-define=FG_BASE_URL=https://a1b2-203-0-113-9.ngrok-free.app

# — or — physical phone on the same Wi-Fi (plaintext fallback, use YOUR LAN IP):
flutter run --dart-define=FG_BASE_URL=http://192.168.29.120:8000

# — or — Android emulator:
flutter run --dart-define=FG_BASE_URL=http://10.0.2.2:8000
```

In the app:
1. **Register** (or log in) — remember this **email + password**; the watch reuses it.
2. Land on the home shell. Open the **Live** tab so the SSE feed connects (you
   should see a "connected" status badge).

> Hot-reload `r`, hot-restart `R`, quit `q` in this terminal.

---

## 4. Fire a fall from the virtual device

**Terminal 3** — `cd virtual_device`. Pair under the **same email** as the app,
then send one real WEDA-FALL **fall** window:

```powershell
python virtual_device.py `
  --pair --email YOUR_APP_EMAIL --password YOUR_APP_PASSWORD `
  --kind fall --count 1
```

What happens:
- `--pair` runs the real handshake: register-or-**login** with that email → mint a
  pairing code → redeem it → receive a **per-device JWT**. (Because the app already
  registered that email, this logs in and pairs a device under *your* account.)
- It POSTs a fall window to `POST /v1/inference`. The **CloudDetector (ONNX)**
  confirms it → the event is persisted → **published to your SSE feed**.

Useful variants:

```powershell
# DEMO MODE (best for a 10-15 s screen recording): a few seconds of silent
# on-wrist monitoring (nothing uploaded), then a fall trips the one upload.
python virtual_device.py --pair --email YOU --password PW --wear

# Tune the lead-in (default 8 s of monitoring before the fall fires)
python virtual_device.py --pair --email YOU --password PW --wear --wear-seconds 6

# Send several falls + ADLs to show confirm-vs-suppress
python virtual_device.py --pair --email YOU --password PW --kind both --count 10

# A canceled false alarm → the retraining path (stored, never alerted)
python virtual_device.py --pair --email YOU --password PW --kind adl --count 1 --false-alarm

# Inspect payloads without sending (no network)
python virtual_device.py --kind fall --count 3 --dry-run

# List the trials available in the dataset
python virtual_device.py --kind fall --list
```

> **Guaranteed-fall fallback:** if a particular real window doesn't cross the
> model threshold, raise `--count`, or restart the backend with
> `$env:FG_MODEL_PATH = "C:\does\not\exist"` to force the deterministic stub
> detector (confirms any high-peak window). Use the real model for the authentic
> demo; the stub only changes the `model_version` label, not the persisted path.

> **Which model is serving?** By default the gateway loads the **active**
> 5-fold cross-validated export at `backend/app/model/cloud_detector.onnx`
> (`/health` reports its `model_version`). The pre-CV **Phase-20 baseline** is
> preserved verbatim under `backend/app/model_old/` — point
> `$env:FG_MODEL_PATH = "app/model_old/cloud_detector.onnx"` at it to A/B the two,
> or roll back, with no code change. See [`ARCHITECTURE.md`](ARCHITECTURE.md) §2.2.

---

## 5. What you'll see on the phone

The instant the backend confirms the fall:

- **On the Live tab:** an in-app red **fall alert** card appears (device id,
  severity, confidence).
- **On another tab / app backgrounded:** an **OS notification** "Fall detected"
  with a full-screen alarm-category intent; tapping it routes to the timeline to
  acknowledge.

> The app deliberately shows the OS notification *only* when the Live tab isn't on
> screen (`alert_providers.dart`). **To capture the OS notification in your video,
> switch to another tab or background the app right before firing the fall.**

The fall also lands on the **Timeline** tab (`GET /v1/events`), where you can
**Acknowledge** it.

---

## 6. Firebase FCM (the "3rd step") — wakes a killed app

**FCM is additive to SSE, never a duplicate.** SSE (§5) owns the **foreground**
real-time alert; FCM covers **only** the **background / killed-app** states the SSE
socket can't run in. The app deliberately ignores FCM messages received in the
foreground, so a single fall produces a single alert — see
[`messaging_service.dart`](../mobile/lib/core/notifications/messaging_service.dart)
and [`ARCHITECTURE.md`](ARCHITECTURE.md) §2.4.

**Both sides are fully wired and done.**

- **Mobile:** `MessagingService` initialises Firebase at boot, requests notification
  permission, and `registerPushToken(...)` sends the real `FirebaseMessaging` token
  to `PUT /v1/users/me/push-token` after each sign-in (re-sends on token rotation).
  Killed-app notification taps route to the timeline. Degrades cleanly to SSE-only if
  Firebase is ever unavailable.

- **Backend:** `FG_FIREBASE_CREDENTIALS` is set in `backend/.env` — the
  service-account JSON from Firebase Console → Project settings → Service accounts →
  Generate new private key, pasted as a single line. On boot the gateway logs
  `FCM service initialised for project fall-guardian-v3`.

> **Testing the killed-app push:** sign into the app, then fully swipe it away. Fire a
> fall from the virtual device (§4) — the OS notification should arrive even with the
> app killed.

---

## 7. How to record the demo (every step)

**Goal:** one screen recording that shows the three terminals **and** the phone,
so the cause (fall sent) and effect (notification) are visible together.

### Tools (Windows)
- **Phone on screen:** [`scrcpy`](https://github.com/Genymotion/scrcpy) mirrors an
  Android phone into a desktop window over USB (`scrcpy` after `adb` sees the
  device). The emulator is already on-screen, so skip scrcpy if using it.
- **Screen recorder:** **OBS Studio** (free) — or Windows **Xbox Game Bar**
  (`Win+G`) for a quick capture. OBS lets you frame terminals + phone in one shot.

### Suggested layout
Arrange on one screen: VS Code with the 3 terminals tiled on the left, the
`scrcpy`/emulator phone window on the right. Record the **whole screen**.

### Storyboard (record in this order)
1. **Intro (5s):** show `docs/ARCHITECTURE.md` or the repo tree — say what it is.
2. **Backend boot:** run §2, show `Application startup complete.` + `/health` JSON.
3. **App login:** run §3, register/login, land on the **Live** tab, show the
   "connected" SSE badge.
4. **Background the app** (or switch off the Live tab) so the OS notification will
   show — narrate this.
5. **Fire the fall:** run the §4 `--pair … --kind fall --count 1` command. Show
   the terminal printing the confirmed-fall response.
   - **For a tight 10-15 s clip, use `--wear` instead** (§4 demo mode): it shows a
     few seconds of silent on-wrist monitoring, then the fall trips the one upload —
     a natural cause→effect arc that fits the recording budget without editing.
6. **The payoff:** cut to the phone — the **OS notification** pops. Tap it →
   timeline → **Acknowledge**.
7. **Bonus:** run `--kind both --count 10` to show ADLs being *suppressed* and
   falls *confirmed*; and `--false-alarm` for the retraining path.
8. **Backend log:** show the `event … recorded` log line in Terminal 1 to prove
   persistence.

### Tips
- Do a silent dry-run of the whole sequence once before recording.
- Increase terminal font size for readability.
- Keep the phone and laptop on the **same Wi-Fi**; confirm reachability by opening
  `http://<LAN-IP>:8000/health` in the phone's browser first.

### "Do I have to re-capture assets if something changes later?"
Only re-record an asset if the thing it depicts changes. A demo recorded **after**
you freeze the system (no more code/model/UI changes) needs no re-capture. If you
later wire FCM (§6) or change the UI, re-record just the affected clip — the
backend-boot and pairing clips stay valid as long as those commands don't change.

---

## 8. Troubleshooting

| Symptom | Fix |
| --- | --- |
| Phone can't reach backend (ngrok) | Is `ngrok http 8000` still running? Did you paste the **current** `https://…ngrok-free.app` into `FG_BASE_URL` (it changes each restart)? Open `https://<sub>.ngrok-free.app/health` in the phone browser. |
| Phone can't reach backend (LAN) | Backend started with `--host 0.0.0.0`? Same Wi-Fi? Open `http://<LAN-IP>:8000/health` in the phone browser. Allow the Windows Firewall prompt (private network). Prefer the ngrok path (§2a). |
| `/health` shows the wrong model | `FG_MODEL_PATH` set to the baseline (`app/model_old/…`) or a bad path? Unset it to load the active 5-fold model (§4). |
| `--pair` fails / 401 | Backend must be **DB-backed** (§2) for pairing. Email/password must match what you registered in the app. |
| Fall not confirmed | Raise `--count`, or force the stub via `FG_MODEL_PATH` (§4 fallback). |
| No OS notification, only in-app | You're on the **Live** tab — switch tabs / background the app before firing (§5). |
| SSE badge stuck "reconnecting" | Token expired → log out/in in the app; check the backend is still running. |
| `docker compose up` hangs | Docker Desktop not running, or port 5432/6379 already in use. |
| Wipe and start clean | `docker compose down -v` (deletes the DB volume), then re-run §2. |

---

## 9. Quick reference (all commands)

```powershell
# 1) Infra
docker compose up -d --wait

# 2) Backend (Terminal 1)
cd backend
$env:FG_DATABASE_URL = "postgresql+asyncpg://fall:fall@localhost:5432/fall_guardian"
uv run alembic upgrade head
$env:FG_DATABASE_URL = "postgresql+asyncpg://fall_app:fall_app@localhost:5432/fall_guardian"
$env:FG_REDIS_URL    = "redis://localhost:6379/0"
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2a) Public HTTPS tunnel for a PHYSICAL phone (Terminal 1b) — skip for the emulator
ngrok http 8000          # copy the printed https://<sub>.ngrok-free.app

# 3) Mobile (Terminal 2)
cd mobile
# physical phone → the ngrok URL; emulator → http://10.0.2.2:8000
flutter run --dart-define=FG_BASE_URL=https://<sub>.ngrok-free.app

# 4) Fall (Terminal 3)
cd virtual_device
python virtual_device.py --pair --email YOU --password PW --kind fall --count 1

# Teardown (keep data: omit -v)
docker compose down -v
```

> **Optional sanity check** (no app/phone needed): from `backend/` run
> `uv run python scripts/integration_smoke.py` — it drives
> register → pair → heartbeat → inference(fall) → events → acknowledge against the
> live DB and asserts each step persisted.
</content>
</invoke>
