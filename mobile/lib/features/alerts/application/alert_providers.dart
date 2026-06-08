import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/auth/token_store.dart';
import '../../../core/config/env.dart';
import '../../../core/app/app_shell_state.dart';
import '../../../core/network/fall_event_service.dart';
import '../../../core/notifications/notification_service.dart';
import '../data/models/fall_event.dart';

/// Secure token storage (read by the SSE service for the bearer header).
final tokenStoreProvider = Provider<TokenStore>((ref) => TokenStore());

/// OS-notification surface. [NotificationService.init] is called once at boot.
final notificationServiceProvider =
    Provider<NotificationService>((ref) => NotificationService());

/// The long-lived SSE connection manager. Created and started here; torn down
/// with the container. The always-mounted [sseStatusProvider] / [fallFeedProvider]
/// keep it alive for the app's lifetime.
final fallEventServiceProvider = Provider<FallEventService>((ref) {
  final tokens = ref.watch(tokenStoreProvider);
  final service = FallEventService(
    baseUrl: Env.baseUrl,
    eventStreamPath: Env.eventStreamPath,
    tokenProvider: tokens.readAccessToken,
  )..start();
  ref.onDispose(service.dispose);
  return service;
});

/// Connection lifecycle for the status badge.
final sseStatusProvider = StreamProvider<SseStatus>(
  (ref) => ref.watch(fallEventServiceProvider).status,
);

/// Newest-first list of confirmed falls received this session. Subscribing in
/// [build] both accumulates the in-app feed and fans each event out to an OS
/// notification — one subscription, two sinks.
final fallFeedProvider =
    NotifierProvider<FallFeed, List<FallEvent>>(FallFeed.new);

class FallFeed extends Notifier<List<FallEvent>> {
  @override
  List<FallEvent> build() {
    final service = ref.watch(fallEventServiceProvider);
    final notifier = ref.watch(notificationServiceProvider);

    final sub = service.events.listen((event) {
      state = [event, ...state];
      // Notify only when the live feed isn't already on screen: app in the
      // background, or the user is on a different tab.
      final liveVisible = ref.read(appResumedProvider) &&
          ref.read(homeTabProvider) == HomeTab.live;
      if (!liveVisible) unawaited(notifier.showFall(event));
    });
    ref.onDispose(sub.cancel);

    return const [];
  }

  void clear() => state = const [];
}

/// The latest alert (for the hero banner), or null when the feed is empty.
final latestFallProvider = Provider<FallEvent?>((ref) {
  final feed = ref.watch(fallFeedProvider);
  return feed.isEmpty ? null : feed.first;
});
