import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';

/// Top-level background handler — firebase_messaging requires this to be a
/// top-level (or static) function annotated for the background isolate. The
/// backend sends a `notification` block alongside `data`, so Android renders the
/// tray entry itself when the app is backgrounded/killed; this handler only has
/// to ensure Firebase is initialised in that isolate.
@pragma('vm:entry-point')
Future<void> firebaseBackgroundHandler(RemoteMessage message) async {
  await Firebase.initializeApp();
}

/// Thin wrapper over Firebase Cloud Messaging — the push path that wakes the app
/// when it is **killed** (the live SSE feed only covers a running app).
///
/// Division of labour:
///   * **killed / background** → the OS auto-displays the FCM `notification`; a
///     tap routes to the timeline via [onFallTapped].
///   * **foreground** → the SSE feed (`FallEventService`) is the source of truth
///     and already raises the local notification, so this service deliberately
///     does NOT surface foreground messages (that would double the alert).
///
/// Everything is best-effort: if Firebase is not configured (e.g. iOS with no
/// `GoogleService-Info.plist`, or before `google-services.json` lands) the app
/// degrades cleanly to SSE-only and [token] stays null.
class MessagingService {
  MessagingService([FirebaseMessaging? messaging]) : _messaging = messaging;

  FirebaseMessaging? _messaging;
  bool _available = false;

  static bool _isFallLike(RemoteMessage m) {
    final type = m.data['type'];
    return type == 'fall' || type == 'sos';
  }

  /// Initialise Firebase + FCM. [onFallTapped] fires when the user opens the app
  /// by tapping an FCM notification (background tap or cold launch). Safe to call
  /// when Firebase is unconfigured — it no-ops and leaves FCM unavailable.
  Future<void> init({void Function()? onFallTapped}) async {
    try {
      await Firebase.initializeApp();
      _messaging ??= FirebaseMessaging.instance;
      FirebaseMessaging.onBackgroundMessage(firebaseBackgroundHandler);
      await _messaging!.requestPermission();
      _available = true;

      // App brought to the foreground by tapping a background notification.
      FirebaseMessaging.onMessageOpenedApp.listen((m) {
        if (_isFallLike(m)) onFallTapped?.call();
      });
      // Cold launch: the app was started from a terminated state by a tap.
      final initial = await _messaging!.getInitialMessage();
      if (initial != null && _isFallLike(initial)) onFallTapped?.call();
    } catch (_) {
      // Firebase not configured → SSE-only. Non-fatal by design.
      _available = false;
    }
  }

  /// The current FCM registration token, or null when FCM is unavailable.
  Future<String?> token() async {
    if (!_available) return null;
    try {
      return await _messaging!.getToken();
    } catch (_) {
      return null;
    }
  }

  /// Fires when FCM rotates the token so the caller can re-register it with the
  /// gateway. An empty stream when FCM is unavailable.
  Stream<String> get onTokenRefresh =>
      _available ? _messaging!.onTokenRefresh : const Stream.empty();
}
