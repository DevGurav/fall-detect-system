import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:http/http.dart' as http;
import '../models/fall_event.dart';

/// Firebase service to handle FCM, Firestore, and notifications
class FirebaseService {
  static final FirebaseService _instance = FirebaseService._internal();
  factory FirebaseService() => _instance;
  FirebaseService._internal();

  FirebaseMessaging? _messaging;
  FirebaseFirestore? _firestore;
  final FlutterLocalNotificationsPlugin _localNotifications = 
      FlutterLocalNotificationsPlugin();

  bool _isInitialized = false;

  /// Initialize Firebase services
  Future<void> initialize() async {
    if (_isInitialized) return;

    try {
      // Initialize Firebase
      await Firebase.initializeApp();
      
      // Initialize Firebase services after Firebase.initializeApp()
      _messaging = FirebaseMessaging.instance;
      _firestore = FirebaseFirestore.instance;
      
      // Initialize local notifications
      await _initializeLocalNotifications();
      
      // Request permission for notifications
      await _requestNotificationPermissions();
      
      // Set up FCM message handlers
      await _setupMessageHandlers();
      
      // Register device for FCM
      await registerDevice();
      
      _isInitialized = true;
      print('Firebase service initialized successfully');
    } catch (e) {
      print('Error initializing Firebase service: $e');
      rethrow;
    }
  }

  /// Initialize local notifications
  Future<void> _initializeLocalNotifications() async {
    const androidSettings = AndroidInitializationSettings('@mipmap/ic_launcher');
    const iosSettings = DarwinInitializationSettings(
      requestAlertPermission: true,
      requestBadgePermission: true,
      requestSoundPermission: true,
    );
    
    const settings = InitializationSettings(
      android: androidSettings,
      iOS: iosSettings,
    );

    await _localNotifications.initialize(
      settings,
      onDidReceiveNotificationResponse: _onNotificationTapped,
    );

    // Create notification channel for Android
    if (Platform.isAndroid) {
      const channel = AndroidNotificationChannel(
        'fall_detection',
        'Fall Detection',
        description: 'Notifications for fall detection alerts',
        importance: Importance.high,
        playSound: true,
      );

      await _localNotifications
          .resolvePlatformSpecificImplementation<
              AndroidFlutterLocalNotificationsPlugin>()
          ?.createNotificationChannel(channel);
    }
  }

  /// Handle notification taps
  void _onNotificationTapped(NotificationResponse response) {
    print('Notification tapped with payload: ${response.payload}');
    // Handle navigation to fall details screen
    // You can add navigation logic here
  }

  /// Show a local test notification
  Future<void> showLocalNotification() async {
    if (!_isInitialized) {
      print('Firebase service not initialized');
      return;
    }

    const AndroidNotificationDetails androidPlatformChannelSpecifics =
        AndroidNotificationDetails(
      'test_channel',
      'Test Notifications',
      channelDescription: 'Test notifications for fall detection app',
      importance: Importance.max,
      priority: Priority.high,
      showWhen: false,
      icon: '@mipmap/ic_launcher',
    );

    const NotificationDetails platformChannelSpecifics = NotificationDetails(
      android: androidPlatformChannelSpecifics,
    );

    await _localNotifications.show(
      DateTime.now().millisecondsSinceEpoch.remainder(100000),
      'Fall Detection Test',
      'This is a test notification from your Fall Detection app! 📱',
      platformChannelSpecifics,
      payload: 'test_notification',
    );
  }

  /// Request notification permissions
  Future<void> _requestNotificationPermissions() async {
    // Request FCM permissions
    NotificationSettings settings = await _messaging!.requestPermission(
      alert: true,
      badge: true,
      sound: true,
      carPlay: false,
      criticalAlert: false,
      provisional: false,
    );

    print('FCM permission status: ${settings.authorizationStatus}');

    // Request local notification permissions for iOS
    if (Platform.isIOS) {
      await _localNotifications
          .resolvePlatformSpecificImplementation<
              IOSFlutterLocalNotificationsPlugin>()
          ?.requestPermissions(
            alert: true,
            badge: true,
            sound: true,
          );
    }
  }

  /// Set up FCM message handlers
  Future<void> _setupMessageHandlers() async {
    // Handle messages when app is in foreground
    FirebaseMessaging.onMessage.listen(_handleForegroundMessage);

    // Handle messages when app is in background but not terminated
    FirebaseMessaging.onMessageOpenedApp.listen(_handleBackgroundMessage);

    // Handle messages when app is terminated
    RemoteMessage? initialMessage = await _messaging!.getInitialMessage();
    if (initialMessage != null) {
      _handleTerminatedMessage(initialMessage);
    }
  }

  /// Handle foreground messages
  Future<void> _handleForegroundMessage(RemoteMessage message) async {
    print('Received foreground message: ${message.data}');
    
    // Show local notification
    await _showLocalNotification(
      title: message.notification?.title ?? 'Fall Detection Alert',
      body: message.notification?.body ?? 'A fall has been detected',
      data: message.data,
    );
  }

  /// Handle background messages (app open but in background)
  void _handleBackgroundMessage(RemoteMessage message) {
    print('App opened from background message: ${message.data}');
    // Navigate to fall details screen
    _navigateToFallDetails(message.data);
  }

  /// Handle terminated messages (app completely closed)
  void _handleTerminatedMessage(RemoteMessage message) {
    print('App opened from terminated state: ${message.data}');
    // Navigate to fall details screen
    _navigateToFallDetails(message.data);
  }

  /// Show local notification
  Future<void> _showLocalNotification({
    required String title,
    required String body,
    Map<String, dynamic> data = const {},
  }) async {
    const androidDetails = AndroidNotificationDetails(
      'fall_detection',
      'Fall Detection',
      channelDescription: 'Notifications for fall detection alerts',
      importance: Importance.max,
      priority: Priority.high,
      showWhen: true,
      icon: '@mipmap/ic_launcher',
      color: const Color(0xFFFF0000), // Red color for urgency
      playSound: true,
      enableVibration: true,
    );

    const iosDetails = DarwinNotificationDetails(
      presentAlert: true,
      presentBadge: true,
      presentSound: true,
    );

    const details = NotificationDetails(
      android: androidDetails,
      iOS: iosDetails,
    );

    await _localNotifications.show(
      DateTime.now().millisecondsSinceEpoch.remainder(100000),
      title,
      body,
      details,
      payload: jsonEncode(data),
    );
  }

  /// Navigate to fall details screen
  void _navigateToFallDetails(Map<String, dynamic> data) {
    // Implement navigation logic here
    // You can use a global navigator key or a navigation service
    print('Navigate to fall details: $data');
  }

  /// Get FCM token
  Future<String?> getFCMToken() async {
    try {
      String? token = await _messaging!.getToken();
      print('FCM Token: $token');
      return token;
    } catch (e) {
      print('Error getting FCM token: $e');
      return null;
    }
  }

  /// Register device with backend
  Future<void> registerDevice() async {
    try {
      String? token = await getFCMToken();
      if (token == null) {
        print('No FCM token available');
        return;
      }

      // Replace with your backend URL
      const backendUrl = 'https://fall-detect-system.onrender.com';
      
      final response = await http.post(
        Uri.parse('$backendUrl/register-device'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'fcm_token': token,
          'device_id': await _getDeviceId(),
        }),
      );

      if (response.statusCode == 200) {
        print('Device registered successfully');
      } else {
        print('Failed to register device: ${response.body}');
      }
    } catch (e) {
      print('Error registering device: $e');
    }
  }

  /// Get device ID (simplified version)
  Future<String> _getDeviceId() async {
    // In a real app, you might want to use a more robust device ID
    // For now, we'll use the FCM token as a unique identifier
    String? token = await getFCMToken();
    return token?.substring(0, 8) ?? 'unknown';
  }

  /// Get fall events from Firestore
  Stream<List<FallEvent>> getFallEventsStream({int limit = 50}) {
    return _firestore!
        .collection('fall_events')
        .orderBy('timestamp', descending: true)
        .limit(limit)
        .snapshots()
        .map((snapshot) {
      return snapshot.docs.map((doc) {
        return FallEvent.fromMap(doc.data(), doc.id);
      }).toList();
    });
  }

  /// Get fall events as a future (one-time fetch)
  Future<List<FallEvent>> getFallEvents({int limit = 50}) async {
    try {
      QuerySnapshot snapshot = await _firestore!
          .collection('fall_events')
          .orderBy('timestamp', descending: true)
          .limit(limit)
          .get();

      return snapshot.docs.map((doc) {
        return FallEvent.fromMap(doc.data() as Map<String, dynamic>, doc.id);
      }).toList();
    } catch (e) {
      print('Error getting fall events: $e');
      return [];
    }
  }

  /// Mark fall event as acknowledged
  Future<void> acknowledgeFallEvent(String eventId) async {
    try {
      await _firestore!.collection('fall_events').doc(eventId).update({
        'acknowledged': true,
        'acknowledged_at': FieldValue.serverTimestamp(),
      });
      print('Fall event acknowledged: $eventId');
    } catch (e) {
      print('Error acknowledging fall event: $e');
    }
  }

  /// Get device statistics
  Future<Map<String, int>> getDeviceStatistics() async {
    try {
      // Get total events
      QuerySnapshot totalEvents = await _firestore!
          .collection('fall_events')
          .get();

      // Get fall events only
      QuerySnapshot fallEvents = await _firestore!
          .collection('fall_events')
          .where('fall_detected', isEqualTo: true)
          .get();

      // Get events from last 24 hours
      DateTime yesterday = DateTime.now().subtract(const Duration(days: 1));
      QuerySnapshot recentEvents = await _firestore!
          .collection('fall_events')
          .where('timestamp', isGreaterThan: yesterday)
          .get();

      return {
        'total_events': totalEvents.size,
        'fall_events': fallEvents.size,
        'recent_events': recentEvents.size,
      };
    } catch (e) {
      print('Error getting statistics: $e');
      return {
        'total_events': 0,
        'fall_events': 0,
        'recent_events': 0,
      };
    }
  }

  /// Dispose resources
  void dispose() {
    // Clean up resources if needed
  }
}

/// Background message handler (must be top-level function)
@pragma('vm:entry-point')
Future<void> firebaseMessagingBackgroundHandler(RemoteMessage message) async {
  await Firebase.initializeApp();
  print('Handling background message: ${message.messageId}');
  
  // You can perform background tasks here
  // Note: UI operations are not allowed in background handlers
}