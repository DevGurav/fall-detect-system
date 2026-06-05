/// App-wide configuration. Override per build with
/// `--dart-define=FG_BASE_URL=https://your-gateway`.
class Env {
  Env._();

  /// FastAPI gateway origin. The Android emulator reaches the host machine at
  /// `10.0.2.2`; a physical device needs the host's LAN IP; the iOS simulator
  /// can use `localhost`. Default targets the Android emulator → host :8000.
  static const String baseUrl = String.fromEnvironment(
    'FG_BASE_URL',
    defaultValue: 'http://10.0.2.2:8000',
  );

  /// Live caregiver SSE feed on the gateway (backend Phase 27).
  static const String eventStreamPath = '/v1/events/stream';
}
