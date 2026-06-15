# mobile — Fall Guardian caregiver app (Flutter)

The caregiver-facing app and **sole caregiver client** for the FastAPI gateway
(the locked design's Next.js web dashboard was dropped — see
[`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §2.5). It covers the full
flow: register/login → pair a device → calibrate → receive real-time fall alerts
→ acknowledge from the timeline → trigger a manual SOS.

## Hybrid alert routing — SSE + additive FCM

The app gets told about a fall two ways, and they **never duplicate**:

- **Foreground (app open)** → the **SSE feed** (`GET /v1/events/stream`) is the
  source of truth; `FallEventService` holds the open stream and raises the in-app
  alert / local notification. Foreground FCM messages are **deliberately ignored**.
- **Background / killed** → **FCM** is the wake path: the backend sends a
  `notification` block, the OS renders the tray entry, and a tap routes into the
  timeline. FCM is strictly **additive** — it covers only what SSE can't.
- If Firebase is unconfigured the app **degrades cleanly to SSE-only** (push token
  stays null). See [`lib/core/notifications/messaging_service.dart`](lib/core/notifications/messaging_service.dart).

## Stack

- **Flutter** 3.35 / Dart 3.9
- **flutter_riverpod** 3.x — state management
- **http** — raw SSE transport (full control over reconnect / backoff / watchdog)
- **flutter_local_notifications** — OS alerts (foreground/SSE path)
- **flutter_secure_storage** — per-user + per-device JWTs at rest
- **firebase_core** + **firebase_messaging** — FCM for the background/killed path
- **flutter_foreground_task** — Android foreground-service keep-alive

## Layout

```text
lib/
├─ main.dart                                ProviderScope + app shell; boots notifs, FCM, SSE
├─ core/
│  ├─ app/app_shell_state.dart              foreground/background lifecycle state
│  ├─ config/env.dart                       gateway base URL (--dart-define overridable)
│  ├─ auth/token_store.dart                 secure per-user JWT read/write
│  ├─ auth/device_token_store.dart          secure per-device JWT read/write
│  ├─ network/fall_event_service.dart       SSE consumer: reconnect + backoff + watchdog
│  └─ notifications/
│     ├─ messaging_service.dart             FCM wrapper (background/killed; additive to SSE)
│     └─ notification_service.dart          local-notification surface
└─ features/
   ├─ alerts/        live alert screen + timeline + acknowledge (SSE-fed providers)
   ├─ auth/          login + register screens, auth providers, push-token registration
   ├─ pairing/       8-char pairing flow + calibration (fit-at-first) screens
   └─ emergency/     manual SOS (POST /v1/emergency)
```

## Running

> **Android builds need JDK 17 or 21** (not 22+). If Gradle fails with
> `Unsupported class file major version`, point Flutter at a supported JDK —
> e.g. Android Studio's bundled runtime:
> `flutter config --jdk-dir "<Android Studio>/jbr"`.

The app needs the gateway reachable at `Env.baseUrl` (default `http://10.0.2.2:8000`,
the Android emulator → host loopback). Point it elsewhere with `--dart-define`:

```sh
# Physical phone (recommended): the ngrok HTTPS URL from docs/RUN.md §2a
flutter run --dart-define=FG_BASE_URL=https://<sub>.ngrok-free.app

# — or — physical phone on the same Wi-Fi (plaintext)
flutter run --dart-define=FG_BASE_URL=http://192.168.x.x:8000
```

**Auth is built in** — register or log in from the app and the JWT is minted and
stored automatically; pairing then issues the per-device token, and the FCM
push-token is registered on sign-in. (Until you sign in, the live feed idles in
the `unauthorized` state by design.) For the full backend + phone walkthrough,
see [`../docs/RUN.md`](../docs/RUN.md).

## Tests

`flutter test` — model / contract tests for the SSE payload (including the
DB-less null-`event_id` frame the gateway emits without Postgres). `flutter
analyze` is clean.
