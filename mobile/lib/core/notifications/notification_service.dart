import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import '../../features/alerts/data/models/fall_event.dart';

/// Surfaces confirmed falls as OS notifications so an alert is seen when the
/// live screen isn't on screen (app backgrounded, or a different tab). The
/// in-app banner is driven separately by Riverpod state.
class NotificationService {
  NotificationService([FlutterLocalNotificationsPlugin? plugin])
      : _plugin = plugin ?? FlutterLocalNotificationsPlugin();

  static const _channelId = 'fall_alerts';
  static const _channelName = 'Fall alerts';
  static const _channelDesc = 'Critical alerts when a fall is confirmed';
  static const _fallPayload = 'fall';

  final FlutterLocalNotificationsPlugin _plugin;
  void Function()? _onFallTapped;

  /// [onFallTapped] fires when the user taps a fall notification — or launches
  /// the app from one — so the shell can route to the timeline to acknowledge.
  Future<void> init({void Function()? onFallTapped}) async {
    _onFallTapped = onFallTapped;
    const android = AndroidInitializationSettings('@mipmap/ic_launcher');
    const darwin = DarwinInitializationSettings(
      requestAlertPermission: true,
      requestBadgePermission: true,
      requestSoundPermission: true,
    );
    await _plugin.initialize(
      settings: const InitializationSettings(android: android, iOS: darwin),
      onDidReceiveNotificationResponse: _onResponse,
    );
    await _plugin
        .resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin>()
        ?.requestNotificationsPermission();

    // Cold-launch: the app was opened by tapping a fall notification.
    final launch = await _plugin.getNotificationAppLaunchDetails();
    if ((launch?.didNotificationLaunchApp ?? false) &&
        launch?.notificationResponse?.payload == _fallPayload) {
      _onFallTapped?.call();
    }
  }

  void _onResponse(NotificationResponse response) {
    if (response.payload == _fallPayload) _onFallTapped?.call();
  }

  Future<void> showFall(FallEvent e) {
    final details = NotificationDetails(
      android: const AndroidNotificationDetails(
        _channelId,
        _channelName,
        channelDescription: _channelDesc,
        importance: Importance.max,
        priority: Priority.high,
        category: AndroidNotificationCategory.alarm,
        fullScreenIntent: true,
      ),
      iOS: const DarwinNotificationDetails(
        presentAlert: true,
        presentBadge: true,
        presentSound: true,
      ),
    );
    return _plugin.show(
      id: e.notificationId,
      title: 'Fall detected',
      body: '${e.deviceId} • ${e.severity.label} • '
          '${(e.confidence * 100).round()}% confidence',
      notificationDetails: details,
      payload: _fallPayload,
    );
  }
}
