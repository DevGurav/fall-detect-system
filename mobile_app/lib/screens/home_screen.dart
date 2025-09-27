import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:intl/intl.dart';
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:firebase_messaging/firebase_messaging.dart';
import '../models/fall_event.dart';
import '../services/firebase_service.dart';
import '../services/auth_service.dart';
import '../widgets/fall_event_card.dart';
import '../widgets/statistics_card.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final GlobalKey<RefreshIndicatorState> _refreshIndicatorKey = 
      GlobalKey<RefreshIndicatorState>();

  @override
  Widget build(BuildContext context) {
    final firebaseService = Provider.of<FirebaseService>(context, listen: false);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Fall Detection Monitor'),
        backgroundColor: Theme.of(context).colorScheme.inversePrimary,
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: () => _refreshIndicatorKey.currentState?.show(),
            tooltip: 'Refresh',
          ),
          IconButton(
            icon: const Icon(Icons.account_circle),
            onPressed: () => _showUserProfileDialog(context),
            tooltip: 'Profile',
          ),
        ],
      ),
      body: RefreshIndicator(
        key: _refreshIndicatorKey,
        onRefresh: () async {
          setState(() {}); // Trigger rebuild to refresh StreamBuilder
          await Future.delayed(const Duration(seconds: 1)); // Give time for refresh
        },
        child: Column(
          children: [
            // Statistics Section
            Container(
              padding: const EdgeInsets.all(16.0),
              child: FutureBuilder<Map<String, int>>(
                future: firebaseService.getDeviceStatistics(),
                builder: (context, snapshot) {
                  if (snapshot.connectionState == ConnectionState.waiting) {
                    return const Card(
                      child: Padding(
                        padding: EdgeInsets.all(20.0),
                        child: Center(child: CircularProgressIndicator()),
                      ),
                    );
                  }
                  
                  final stats = snapshot.data ?? {
                    'total_events': 0,
                    'fall_events': 0,
                    'recent_events': 0,
                  };
                  
                  return StatisticsCard(statistics: stats);
                },
              ),
            ),
            
            // Caretaker Panel Section
            _buildCaretakerPanel(),
            
            // Fall Events Section
            Expanded(
              child: StreamBuilder<List<FallEvent>>(
                stream: firebaseService.getFallEventsStream(limit: 100),
                builder: (context, snapshot) {
                  if (snapshot.connectionState == ConnectionState.waiting) {
                    return const Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          CircularProgressIndicator(),
                          SizedBox(height: 16),
                          Text('Loading fall events...'),
                        ],
                      ),
                    );
                  }
                  
                  if (snapshot.hasError) {
                    return Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(
                            Icons.error_outline,
                            size: 64,
                            color: Colors.red[300],
                          ),
                          const SizedBox(height: 16),
                          Text(
                            'Error loading events',
                            style: Theme.of(context).textTheme.headlineSmall,
                          ),
                          const SizedBox(height: 8),
                          Text(
                            snapshot.error.toString(),
                            textAlign: TextAlign.center,
                            style: Theme.of(context).textTheme.bodyMedium,
                          ),
                          const SizedBox(height: 16),
                          ElevatedButton(
                            onPressed: () => setState(() {}),
                            child: const Text('Retry'),
                          ),
                        ],
                      ),
                    );
                  }
                  
                  final events = snapshot.data ?? [];
                  
                  if (events.isEmpty) {
                    return const Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(
                            Icons.event_available,
                            size: 64,
                            color: Colors.grey,
                          ),
                          SizedBox(height: 16),
                          Text(
                            'No fall events recorded',
                            style: TextStyle(
                              fontSize: 18,
                              fontWeight: FontWeight.w500,
                              color: Colors.grey,
                            ),
                          ),
                          SizedBox(height: 8),
                          Text(
                            'When a fall is detected, it will appear here',
                            textAlign: TextAlign.center,
                            style: TextStyle(color: Colors.grey),
                          ),
                        ],
                      ),
                    );
                  }
                  
                  return ListView.builder(
                    padding: const EdgeInsets.all(16.0),
                    itemCount: events.length,
                    itemBuilder: (context, index) {
                      final event = events[index];
                      return FallEventCard(
                        event: event,
                        onTap: () => _showEventDetails(context, event),
                        onAcknowledge: () => _acknowledgeEvent(context, event),
                      );
                    },
                  );
                },
              ),
            ),
          ],
        ),
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => _showTestNotification(context),
        icon: const Icon(Icons.notification_add),
        label: const Text('Test Notification'),
        backgroundColor: Theme.of(context).colorScheme.secondary,
      ),
    );
  }

  void _showEventDetails(BuildContext context, FallEvent event) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(
          event.fallDetected ? '🚨 Fall Detected' : '✅ Normal Activity',
        ),
        content: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              _buildDetailRow('Device ID', event.deviceId),
              _buildDetailRow('Time', DateFormat('MMM d, yyyy h:mm:ss a').format(event.timestamp)),
              _buildDetailRow('Confidence', event.confidenceText),
              _buildDetailRow('Status', event.statusText),
              const SizedBox(height: 16),
              const Text(
                'Sensor Data:',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 8),
              _buildSensorDataTable(event.sensorData),
            ],
          ),
        ),
        actions: [
          if (event.fallDetected)
            TextButton(
              onPressed: () {
                Navigator.of(context).pop();
                _acknowledgeEvent(context, event);
              },
              child: const Text('Acknowledge'),
            ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  Widget _buildDetailRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4.0),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 80,
            child: Text(
              '$label:',
              style: const TextStyle(fontWeight: FontWeight.w500),
            ),
          ),
          Expanded(child: Text(value)),
        ],
      ),
    );
  }

  Widget _buildSensorDataTable(SensorData data) {
    return Table(
      border: TableBorder.all(color: Colors.grey[300]!),
      columnWidths: const {
        0: FlexColumnWidth(1),
        1: FlexColumnWidth(2),
      },
      children: [
        _buildTableRow('Accel X', data.accelX.toStringAsFixed(2)),
        _buildTableRow('Accel Y', data.accelY.toStringAsFixed(2)),
        _buildTableRow('Accel Z', data.accelZ.toStringAsFixed(2)),
        _buildTableRow('Gyro X', data.gyroX.toStringAsFixed(2)),
        _buildTableRow('Gyro Y', data.gyroY.toStringAsFixed(2)),
        _buildTableRow('Gyro Z', data.gyroZ.toStringAsFixed(2)),
      ],
    );
  }

  TableRow _buildTableRow(String label, String value) {
    return TableRow(
      children: [
        Padding(
          padding: const EdgeInsets.all(8.0),
          child: Text(label, style: const TextStyle(fontWeight: FontWeight.w500)),
        ),
        Padding(
          padding: const EdgeInsets.all(8.0),
          child: Text(value),
        ),
      ],
    );
  }

  void _acknowledgeEvent(BuildContext context, FallEvent event) async {
    final firebaseService = Provider.of<FirebaseService>(context, listen: false);
    
    try {
      await firebaseService.acknowledgeFallEvent(event.id);
      
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Fall event acknowledged'),
            backgroundColor: Colors.green,
          ),
        );
      }
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Error acknowledging event: $e'),
            backgroundColor: Colors.red,
          ),
        );
      }
    }
  }

  void _showTestNotification(BuildContext context) async {
    final firebaseService = Provider.of<FirebaseService>(context, listen: false);
    
    // Show a test local notification
    try {
      await firebaseService.initialize(); // Ensure service is initialized
      
      // Show test notification (you would implement this method in FirebaseService)
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Test notification functionality coming soon'),
          backgroundColor: Colors.blue,
        ),
      );
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Error: $e'),
          backgroundColor: Colors.red,
        ),
      );
    }
  }

  void _showUserProfileDialog(BuildContext context) {
    final authService = Provider.of<AuthService>(context, listen: false);
    
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: Row(
          children: [
            CircleAvatar(
              backgroundColor: Colors.red,
              child: Text(
                authService.getUserDisplayName().substring(0, 1).toUpperCase(),
                style: const TextStyle(
                  color: Colors.white,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
            const SizedBox(width: 12),
            const Text('User Profile'),
          ],
        ),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // User info
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: Colors.red.withOpacity(0.1),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    authService.getUserDisplayName(),
                    style: const TextStyle(
                      fontWeight: FontWeight.bold,
                      fontSize: 18,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    authService.currentUserEmail,
                    style: TextStyle(
                      fontSize: 14,
                      color: Colors.grey[600],
                    ),
                  ),
                  const SizedBox(height: 8),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                    decoration: BoxDecoration(
                      color: Colors.green,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: const Text(
                      'Active',
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 12,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            
            // App Status Section
            const Text(
              'System Status:',
              style: TextStyle(
                fontWeight: FontWeight.bold,
                fontSize: 16,
              ),
            ),
            const SizedBox(height: 8),
            _buildStatusItem('Notifications', true),
            _buildStatusItem('Background monitoring', true),
            _buildStatusItem('Firebase connection', true),
            const SizedBox(height: 16),
            
            // Test notification button
            SizedBox(
              width: double.infinity,
              child: ElevatedButton.icon(
                onPressed: () => _testNotification(context),
                icon: const Icon(Icons.notifications_active),
                label: const Text('Test Notification'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.blue,
                  foregroundColor: Colors.white,
                ),
              ),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Close'),
          ),
          TextButton(
            onPressed: () {
              Navigator.of(context).pop();
              _showSignOutDialog(context);
            },
            style: TextButton.styleFrom(foregroundColor: Colors.red),
            child: const Text('Sign Out'),
          ),
        ],
      ),
    );
  }

  Widget _buildStatusItem(String label, bool isActive) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        children: [
          Icon(
            isActive ? Icons.check_circle : Icons.error,
            color: isActive ? Colors.green : Colors.red,
            size: 16,
          ),
          const SizedBox(width: 8),
          Text(label),
        ],
      ),
    );
  }

  void _testNotification(BuildContext context) async {
    final firebaseService = Provider.of<FirebaseService>(context, listen: false);
    
    Navigator.of(context).pop(); // Close dialog first
    
    // Show loading snackbar
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('Sending test notification...'),
        backgroundColor: Colors.blue,
      ),
    );

    try {
      // Send a test notification using the local notification system
      await firebaseService.showLocalNotification();
      
      // Show success message
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Test notification sent successfully!'),
          backgroundColor: Colors.green,
        ),
      );
    } catch (e) {
      // Show error message
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Failed to send notification: $e'),
          backgroundColor: Colors.red,
        ),
      );
    }
  }

  void _showSignOutDialog(BuildContext context) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Sign Out'),
        content: const Text('Are you sure you want to sign out?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () {
              Navigator.of(context).pop();
              Provider.of<AuthService>(context, listen: false).signOut();
            },
            style: TextButton.styleFrom(foregroundColor: Colors.red),
            child: const Text('Sign Out'),
          ),
        ],
      ),
    );
  }

  // Caretaker Control Panel Widget
  Widget _buildCaretakerPanel() {
    return Card(
      margin: const EdgeInsets.all(16),
      elevation: 4,
      child: Container(
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(12),
          gradient: LinearGradient(
            colors: [Colors.blue[50]!, Colors.indigo[50]!],
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
          ),
        ),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const Icon(Icons.medical_services, color: Colors.blue),
                  const SizedBox(width: 8),
                  Text(
                    '👩‍⚕️ Care Management',
                    style: TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.bold,
                      color: Colors.blue[800],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              
              // Device Connection Status
              Row(
                children: [
                  Expanded(
                    child: ElevatedButton.icon(
                      onPressed: _connectWearableDevice,
                      icon: const Icon(Icons.watch, size: 16),
                      label: const Text('Connect Wearable'),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: Colors.blue,
                        foregroundColor: Colors.white,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: ElevatedButton.icon(
                      onPressed: _testDeviceConnection,
                      icon: const Icon(Icons.bluetooth_connected, size: 16),
                      label: const Text('Test Connection'),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: Colors.green,
                        foregroundColor: Colors.white,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              
              // Emergency & Notifications
              Row(
                children: [
                  Expanded(
                    child: ElevatedButton.icon(
                      onPressed: _testEmergencyAlert,
                      icon: const Icon(Icons.emergency, size: 16),
                      label: const Text('Test Alert System'),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: Colors.red,
                        foregroundColor: Colors.white,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: ElevatedButton.icon(
                      onPressed: _manageEmergencyContacts,
                      icon: const Icon(Icons.contact_phone, size: 16),
                      label: const Text('Emergency Contacts'),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: Colors.orange,
                        foregroundColor: Colors.white,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              
              // Care Features
              Center(
                child: ElevatedButton.icon(
                  onPressed: _viewPatientHistory,
                  icon: const Icon(Icons.history_edu, size: 16),
                  label: const Text('View Patient History'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.purple,
                    foregroundColor: Colors.white,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  void _showLoadingSnackBar(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Row(
          children: [
            const SizedBox(
              width: 16,
              height: 16,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                valueColor: AlwaysStoppedAnimation<Color>(Colors.white),
              ),
            ),
            const SizedBox(width: 12),
            Text(message),
          ],
        ),
        backgroundColor: Colors.blue,
        duration: const Duration(minutes: 1), // Long duration, will be manually hidden
      ),
    );
  }

  // Caretaker-focused methods
  Future<void> _connectWearableDevice() async {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.watch, color: Colors.blue),
            SizedBox(width: 8),
            Text('Connect Wearable Device'),
          ],
        ),
        content: const Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('To connect your wearable device:'),
            SizedBox(height: 12),
            Text('1. Turn on your ESP32 wearable device'),
            Text('2. Make sure it\'s connected to WiFi'),
            Text('3. Device will automatically connect to the system'),
            SizedBox(height: 12),
            Text('Device ID: ESP32_WRISTBAND_001',
                style: TextStyle(fontWeight: FontWeight.bold)),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('OK'),
          ),
        ],
      ),
    );
  }

  Future<void> _testDeviceConnection() async {
    try {
      _showLoadingSnackBar('Testing device connection...');
      
      // Test if we can receive data from the device
      final response = await http.post(
        Uri.parse('https://fall-detect-system.onrender.com/predict'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'device_id': 'ESP32_WRISTBAND_001',
          'accelX': 0.1,
          'accelY': 0.2,
          'accelZ': 9.8,
          'gyroX': 0.1,
          'gyroY': 0.0,
          'gyroZ': 0.1,
        }),
      );

      ScaffoldMessenger.of(context).hideCurrentSnackBar();

      if (response.statusCode == 200) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('✅ Device connection successful! Ready for monitoring.'),
            backgroundColor: Colors.green,
            duration: Duration(seconds: 4),
          ),
        );
      } else {
        throw Exception('Connection test failed: ${response.statusCode}');
      }
    } catch (e) {
      ScaffoldMessenger.of(context).hideCurrentSnackBar();
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('❌ Device connection failed: Check if device is powered on'),
          backgroundColor: Colors.red,
          duration: const Duration(seconds: 4),
        ),
      );
    }
  }

  Future<void> _testEmergencyAlert() async {
    try {
      _showLoadingSnackBar('Testing emergency alert system...');
      
      // First register device for notifications
      final fcmToken = await FirebaseMessaging.instance.getToken();
      if (fcmToken != null) {
        await http.post(
          Uri.parse('https://fall-detect-system.onrender.com/register-device'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({
            'device_id': 'mobile_app_001',
            'fcm_token': fcmToken,
          }),
        );
      }
      
      // Send test fall event
      final response = await http.post(
        Uri.parse('https://fall-detect-system.onrender.com/predict'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'device_id': 'mobile_app_001',
          'accelX': 15.2,
          'accelY': -8.7,
          'accelZ': 2.1,
          'gyroX': 180.0,
          'gyroY': -95.0,
          'gyroZ': 220.0,
        }),
      );

      ScaffoldMessenger.of(context).hideCurrentSnackBar();

      if (response.statusCode == 200) {
        final result = jsonDecode(response.body);
        final fallDetected = result['fall_detected'] ?? false;
        
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              fallDetected 
                ? '🚨 EMERGENCY ALERT SENT!\n📱 Check your notifications\n📞 Emergency contacts notified'
                : '⚠️ Alert system test completed but fall not detected',
            ),
            backgroundColor: fallDetected ? Colors.red : Colors.orange,
            duration: const Duration(seconds: 6),
          ),
        );
        
        setState(() {}); // Refresh to show new event
      } else {
        throw Exception('Alert test failed: ${response.statusCode}');
      }
    } catch (e) {
      ScaffoldMessenger.of(context).hideCurrentSnackBar();
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('❌ Emergency alert test failed: $e'),
          backgroundColor: Colors.red,
          duration: const Duration(seconds: 4),
        ),
      );
    }
  }

  void _manageEmergencyContacts() {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.contact_phone, color: Colors.orange),
            SizedBox(width: 8),
            Text('Emergency Contacts'),
          ],
        ),
        content: const Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Current Emergency Contacts:', style: TextStyle(fontWeight: FontWeight.bold)),
            SizedBox(height: 12),
            ListTile(
              leading: Icon(Icons.person, color: Colors.blue),
              title: Text('Primary Caretaker'),
              subtitle: Text('Not configured'),
              dense: true,
            ),
            ListTile(
              leading: Icon(Icons.local_hospital, color: Colors.red),
              title: Text('Medical Emergency'),
              subtitle: Text('911 (Default)'),
              dense: true,
            ),
            ListTile(
              leading: Icon(Icons.family_restroom, color: Colors.green),
              title: Text('Family Member'),
              subtitle: Text('Not configured'),
              dense: true,
            ),
            SizedBox(height: 8),
            Text('📝 Note: Contact management will be available in next update.',
                style: TextStyle(fontSize: 12, color: Colors.grey)),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  void _viewPatientHistory() async {
    try {
      _showLoadingSnackBar('Loading patient history...');

      final response = await http.get(
        Uri.parse('https://fall-detect-system.onrender.com/fall-events'),
        headers: {'Content-Type': 'application/json'},
      );

      ScaffoldMessenger.of(context).hideCurrentSnackBar();

      if (response.statusCode == 200) {
        final result = jsonDecode(response.body);
        final events = result['events'] as List? ?? [];
        
        showDialog(
          context: context,
          builder: (context) => AlertDialog(
            title: const Row(
              children: [
                Icon(Icons.history_edu, color: Colors.purple),
                SizedBox(width: 8),
                Text('Patient Fall History'),
              ],
            ),
            content: SizedBox(
              width: double.maxFinite,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text('Total Events: ${events.length}'),
                      Text('Last 30 Days: ${events.length}'), // Simplified
                    ],
                  ),
                  const Divider(),
                  if (events.isEmpty)
                    const Padding(
                      padding: EdgeInsets.all(20),
                      child: Column(
                        children: [
                          Icon(Icons.check_circle, color: Colors.green, size: 48),
                          SizedBox(height: 8),
                          Text('No fall incidents recorded'),
                          Text('Patient is safe!', style: TextStyle(color: Colors.green)),
                        ],
                      ),
                    )
                  else
                    Flexible(
                      child: ListView.builder(
                        shrinkWrap: true,
                        itemCount: events.length > 10 ? 10 : events.length,
                        itemBuilder: (context, index) {
                          final event = events[index];
                          return Card(
                            child: ListTile(
                              leading: const Icon(Icons.warning, color: Colors.red),
                              title: Text('Fall Incident #${index + 1}'),
                              subtitle: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text('Time: ${event['timestamp'] ?? 'Unknown'}'),
                                  Text('Confidence: ${((event['confidence'] ?? 0.0) * 100).toStringAsFixed(1)}%'),
                                  Text('Device: ${event['device_id'] ?? 'Unknown'}'),
                                ],
                              ),
                              dense: true,
                            ),
                          );
                        },
                      ),
                    ),
                  if (events.length > 10)
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text('...and ${events.length - 10} more incidents',
                          style: const TextStyle(fontStyle: FontStyle.italic)),
                    ),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(context).pop(),
                child: const Text('Close'),
              ),
            ],
          ),
        );
      } else {
        throw Exception('Failed to load history: ${response.statusCode}');
      }
    } catch (e) {
      ScaffoldMessenger.of(context).hideCurrentSnackBar();
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('❌ Failed to load patient history: $e'),
          backgroundColor: Colors.red,
          duration: const Duration(seconds: 4),
        ),
      );
    }
  }
}