import 'package:flutter/material.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:provider/provider.dart';
import 'services/firebase_service.dart';
import 'services/auth_service.dart';
import 'screens/auth_wrapper.dart';

/// Background message handler - must be top-level function
@pragma('vm:entry-point')
Future<void> firebaseMessagingBackgroundHandler(RemoteMessage message) async {
  print('Handling background message: ${message.messageId}');
  // Handle background FCM messages here
}

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  
  // Initialize Firebase service first
  FirebaseService firebaseService = FirebaseService();
  await firebaseService.initialize();
  
  // Set background message handler
  FirebaseMessaging.onBackgroundMessage(firebaseMessagingBackgroundHandler);
  
  runApp(const FallDetectionApp());
}

class FallDetectionApp extends StatelessWidget {
  const FallDetectionApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider<AuthService>(
          create: (_) => AuthService()..initialize(),
        ),
        Provider<FirebaseService>(
          create: (_) => FirebaseService(),
          dispose: (_, service) => service.dispose(),
        ),
      ],
      child: MaterialApp(
        title: 'Fall Detection Monitor',
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: Colors.red),
          useMaterial3: true,
          appBarTheme: const AppBarTheme(
            backgroundColor: Colors.red,
            foregroundColor: Colors.white,
          ),
        ),
        home: const AuthWrapper(), // Use AuthWrapper instead of direct HomeScreen
        debugShowCheckedModeBanner: false,
      ),
    );
  }
}
