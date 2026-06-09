import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../../core/auth/token_store.dart';

class EmergencyException implements Exception {
  EmergencyException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// Calls POST /v1/emergency to create a manual-SOS event. The backend persists
/// it, publishes to SSE, and dispatches FCM — same path as a detected fall but
/// with source=MANUAL and confidence=1.0.
class EmergencyService {
  EmergencyService({
    required this.baseUrl,
    required TokenStore tokenStore,
    http.Client? client,
  })  : _tokenStore = tokenStore,
        _client = client ?? http.Client();

  final String baseUrl;
  final TokenStore _tokenStore;
  final http.Client _client;

  Future<void> triggerSos({String? deviceRef, String? note}) async {
    final token = await _tokenStore.readAccessToken();
    if (token == null) throw EmergencyException('Not signed in.');
    late final http.Response res;
    try {
      res = await _client
          .post(
            Uri.parse('$baseUrl/v1/emergency'),
            headers: {
              'Content-Type': 'application/json',
              'Authorization': 'Bearer $token',
            },
            body: jsonEncode({
              if (deviceRef != null) 'device_ref': deviceRef,
              if (note != null) 'note': note,
            }),
          )
          .timeout(const Duration(seconds: 15));
    } on Exception {
      throw EmergencyException('Network error — SOS could not be sent.');
    }
    if (res.statusCode == 201) return;
    if (res.statusCode == 401) throw EmergencyException('Session expired — please sign in again.');
    throw EmergencyException('SOS failed (HTTP ${res.statusCode}).');
  }
}
