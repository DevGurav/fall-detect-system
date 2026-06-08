# mobile — Fall Guardian caregiver app (Flutter)

The caregiver-facing app: a real-time fall-alert client for the FastAPI gateway.
Phase 28 (Week E) lays the architecture and ships the **live alert screen** that
consumes the backend's SSE feed (`GET /v1/events/stream`, backend Phase 27).

## Stack

- **Flutter** 3.35 / Dart 3.9
- **flutter_riverpod** 3.x — state management
- **http** — raw SSE transport (full control over reconnect / watchdog)
- **flutter_local_notifications** — OS alerts
- **flutter_secure_storage** — JWT at rest
- **flutter_foreground_task** — Android foreground-service keep-alive (wired as a
  dependency; activation lands with the background-delivery slice)

## Layout

```text
lib/
├─ main.dart                           ProviderScope + app shell; boots notifs + SSE
├─ core/
│  ├─ config/env.dart                  gateway base URL (--dart-define overridable)
│  ├─ auth/token_store.dart            secure JWT read/write
│  └─ network/fall_event_service.dart  SSE consumer: reconnect + backoff + watchdog
├─ services/
│  └─ notifications.dart               local-notification surface
└─ features/alerts/
   ├─ data/models/fall_event.dart      fall payload model (mirrors backend _alert_payload)
   ├─ application/alert_providers.dart  Riverpod wiring (service, status, feed)
   └─ presentation/live_alert_screen.dart  the live alert UI
```

## Running

> **Android builds need JDK 17 or 21** (not 22+). If Gradle fails with
> `Unsupported class file major version`, point Flutter at a supported JDK —
> e.g. Android Studio's bundled runtime:
> `flutter config --jdk-dir "<Android Studio>/jbr"`.

The app needs the gateway reachable at `Env.baseUrl` (default `http://10.0.2.2:8000`,
the Android emulator → host loopback). Point it elsewhere with:

```sh
flutter run --dart-define=FG_BASE_URL=http://192.168.x.x:8000
```

A valid per-user JWT must be present in secure storage under `fg_access_token`
(the login / pairing flow is a later slice; until then seed it manually for
testing). Without a token the feed idles in the `unauthorized` state by design.

## Tests

`flutter test` — model / contract tests for the SSE payload (including the
DB-less null-`event_id` frame the gateway emits without Postgres). `flutter
analyze` is clean.
