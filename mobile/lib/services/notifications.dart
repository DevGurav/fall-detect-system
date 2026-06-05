import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import '../features/alerts/data/models/fall_event.dart';

/// Surfaces confirmed falls as OS notifications so an alert is seen even when
/// the live screen isn't focused. The in-app banner is driven separately by
/// Riverpod state; both consume the same [FallEvent].
class NotificationService {
  NotificationService([FlutterLocalNotificationsPlugin? plugin])
      : _plugin = plugin ?? FlutterLocalNotificationsPlugin();

  static const _channelId = 'fall_alerts';
  static const _channelName = 'Fall alerts';
  static const _channelDesc = 'Critical alerts when a fall is confirmed';

  final FlutterLocalNotificationsPlugin _plugin;

  Future<void> init() async {
    const android = AndroidInitializationSettings('@mipmap/ic_launcher');
    const darwin = DarwinInitializationSettings(
      requestAlertPermission: true,
      requestBadgePermission: true,
      requestSoundPermission: true,
    );
    await _plugin.initialize(
      settings: const InitializationSettings(android: android, iOS: darwin),
    );
    await _plugin
        .resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin>()
        ?.requestNotificationsPermission();
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
    );
  }
}
