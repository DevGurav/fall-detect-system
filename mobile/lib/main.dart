import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'features/alerts/application/alert_providers.dart';
import 'features/alerts/presentation/live_alert_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Build the container up front so notifications are initialised and the SSE
  // loop is connecting before the first frame paints.
  final container = ProviderContainer();
  await container.read(notificationServiceProvider).init();
  container.read(fallEventServiceProvider); // ..start() runs in the provider

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
      home: const LiveAlertScreen(),
    );
  }
}
