import 'package:flutter/material.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/app/app_shell_state.dart';
import 'features/alerts/application/alert_providers.dart';
import 'features/alerts/presentation/live_alert_screen.dart';
import 'features/alerts/presentation/timeline_screen.dart';
import 'features/auth/application/auth_providers.dart';
import 'features/auth/presentation/login_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Initialise foreground-service options once at boot. The service is
  // started/stopped inside fallEventServiceProvider when the SSE loop runs,
  // keeping the socket alive even when the app is killed or backgrounded.
  FlutterForegroundTask.init(
    androidNotificationOptions: AndroidNotificationOptions(
      channelId: 'fg_sse_channel',
      channelName: 'Fall Guardian Alerts',
      channelDescription:
          'Keeps the fall-alert connection alive when the app is in the background.',
      channelImportance: NotificationChannelImportance.LOW,
      priority: NotificationPriority.LOW,
    ),
    iosNotificationOptions: const IOSNotificationOptions(),
    foregroundTaskOptions: ForegroundTaskOptions(
      eventAction: ForegroundTaskEventAction.nothing(),
      autoRunOnBoot: false,
    ),
  );

  // Build the container up front so notifications are initialised before the
  // first frame. The SSE loop starts only once we're past the login gate.
  final container = ProviderContainer();
  await container.read(notificationServiceProvider).init(
        onFallTapped: () => container.read(homeTabProvider.notifier).showTimeline(),
      );

  // Initialise FCM (the killed-app push path). Best-effort: if Firebase isn't
  // configured the app degrades to SSE-only. A token rotation re-registers with
  // the gateway (no-ops until the user is signed in).
  final messaging = container.read(messagingServiceProvider);
  await messaging.init(
    onFallTapped: () => container.read(homeTabProvider.notifier).showTimeline(),
  );
  messaging.onTokenRefresh.listen(
    (token) => container.read(authServiceProvider).registerPushToken(token),
  );

  // Track foreground/background so the SSE feed only raises a notification when
  // the live screen isn't already showing.
  AppLifecycleListener(
    onStateChange: (state) =>
        container.read(appResumedProvider.notifier).update(state),
  );

  runApp(
    UncontrolledProviderScope(
      container: container,
      child: const FallGuardianApp(),
    ),
  );
}

class FallGuardianApp extends StatelessWidget {
  const FallGuardianApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Fall Guardian',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF1565C0)),
        useMaterial3: true,
      ),
      home: const _RootGate(),
    );
  }
}

/// Routes on auth state: splash while restoring the session, then login or feed.
class _RootGate extends ConsumerWidget {
  const _RootGate();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authControllerProvider);
    return switch (auth) {
      AuthStatus.unknown =>
        const Scaffold(body: Center(child: CircularProgressIndicator())),
      AuthStatus.authenticated => const HomeShell(),
      AuthStatus.unauthenticated => const LoginScreen(),
    };
  }
}

/// Authenticated shell — bottom nav between the live feed and the timeline.
/// An IndexedStack keeps both alive (the SSE socket stays connected on switch);
/// the selected tab lives in [homeTabProvider] so a notification tap can route
/// here to the timeline.
class HomeShell extends ConsumerWidget {
  const HomeShell({super.key});

  static const _screens = [LiveAlertScreen(), TimelineScreen()];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final index = ref.watch(homeTabProvider);
    return Scaffold(
      body: IndexedStack(index: index, children: _screens),
      bottomNavigationBar: NavigationBar(
        selectedIndex: index,
        onDestinationSelected: (i) =>
            ref.read(homeTabProvider.notifier).select(i),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.notifications_active_outlined),
            selectedIcon: Icon(Icons.notifications_active),
            label: 'Live',
          ),
          NavigationDestination(
            icon: Icon(Icons.history_outlined),
            selectedIcon: Icon(Icons.history),
            label: 'History',
          ),
        ],
      ),
    );
  }
}
