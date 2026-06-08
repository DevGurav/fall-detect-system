import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'features/alerts/application/alert_providers.dart';
import 'features/alerts/presentation/live_alert_screen.dart';
import 'features/alerts/presentation/timeline_screen.dart';
import 'features/auth/application/auth_providers.dart';
import 'features/auth/presentation/login_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Build the container up front so notifications are initialised before the
  // first frame. The SSE loop starts only once we're past the login gate.
  final container = ProviderContainer();
  await container.read(notificationServiceProvider).init();

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
/// An IndexedStack keeps both alive (the SSE socket stays connected on switch).
class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;
  static const _screens = [LiveAlertScreen(), TimelineScreen()];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: IndexedStack(index: _index, children: _screens),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
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
