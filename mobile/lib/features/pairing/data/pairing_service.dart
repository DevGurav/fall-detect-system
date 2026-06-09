import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../../core/auth/device_token_store.dart';
import '../../../core/auth/token_store.dart';

class PairingException implements Exception {
  PairingException(this.message);
  final String message;
  @override
  String toString() => message;
}

class GeneratedCode {
  const GeneratedCode({required this.code, required this.expiresAt});
  final String code;
  final DateTime expiresAt;
}

/// Handles the two-step pairing flow:
///
/// 1. [generateCode] — user-JWT-authenticated; creates an 8-char pairing code
///    on the backend for the caregiver to display on-screen for the wearable.
///
/// 2. [pairAsDevice] — no-auth; redeems a code as if this phone were the
///    device. Used for demo/testing before real ESP32 firmware (Phase 31).
class PairingService {
  PairingService({
    required this.baseUrl,
    required TokenStore tokenStore,
    required DeviceTokenStore deviceTokenStore,
    http.Client? client,
  })  : _tokenStore = tokenStore,
        _deviceTokenStore = deviceTokenStore,
        _client = client ?? http.Client();

  final String baseUrl;
  final TokenStore _tokenStore;
  final DeviceTokenStore _deviceTokenStore;
  final http.Client _client;

  Future<GeneratedCode> generateCode() async {
    final token = await _tokenStore.readAccessToken();
    if (token == null) throw PairingException('Not signed in.');
    late final http.Response res;
    try {
      res = await _client
          .post(
            Uri.parse('$baseUrl/v1/devices/pairing-codes'),
            headers: {
              'Content-Type': 'application/json',
              'Authorization': 'Bearer $token',
            },
          )
          .timeout(const Duration(seconds: 15));
    } on Exception {
      throw PairingException('Network error — check your connection.');
    }
    if (res.statusCode == 201) {
      final json = jsonDecode(res.body) as Map<String, dynamic>;
      return GeneratedCode(
        code: json['code'] as String,
        expiresAt: DateTime.parse(json['expires_at'] as String),
      );
    }
    if (res.statusCode == 429) throw PairingException('Too many attempts — wait a moment.');
    throw PairingException('Failed to generate code (HTTP ${res.statusCode}).');
  }

  /// Redeem a pairing code as the device — stores the resulting device JWT
  /// so the phone can act as a pseudo-device for demo purposes.
  Future<void> pairAsDevice(String code, String deviceId) async {
    late final http.Response res;
    try {
      res = await _client
          .post(
            Uri.parse('$baseUrl/v1/devices/pair'),
            headers: const {'Content-Type': 'application/json'},
            body: jsonEncode({'code': code.toUpperCase(), 'device_id': deviceId}),
          )
          .timeout(const Duration(seconds: 15));
    } on Exception {
      throw PairingException('Network error — check your connection.');
    }
    switch (res.statusCode) {
      case 200:
        final json = jsonDecode(res.body) as Map<String, dynamic>;
        await _deviceTokenStore.writeDevice(
          json['device_token'] as String,
          json['device_id'] as String,
        );
      case 400:
        throw PairingException('Invalid or expired pairing code.');
      case 429:
        throw PairingException('Too many attempts — wait a moment.');
      default:
        throw PairingException('Pairing failed (HTTP ${res.statusCode}).');
    }
  }
}
