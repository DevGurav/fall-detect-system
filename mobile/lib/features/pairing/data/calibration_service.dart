import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../../core/auth/device_token_store.dart';

class CalibrationException implements Exception {
  CalibrationException(this.message);
  final String message;
  @override
  String toString() => message;
}

class CalibrationResult {
  const CalibrationResult({
    required this.deviceId,
    required this.nAdlWindows,
    required this.fittedAt,
  });
  final String deviceId;
  final int nAdlWindows;
  final DateTime fittedAt;
}

/// Calls POST /v1/devices/{id}/calibrate using the stored device JWT.
/// The ESP32 sends ADL windows; this service only triggers the fit
/// after the 15-min session ends.
class CalibrationService {
  CalibrationService({
    required this.baseUrl,
    required DeviceTokenStore deviceTokenStore,
    http.Client? client,
  })  : _deviceTokenStore = deviceTokenStore,
        _client = client ?? http.Client();

  final String baseUrl;
  final DeviceTokenStore _deviceTokenStore;
  final http.Client _client;

  Future<CalibrationResult> fitCalibration() async {
    final deviceId = await _deviceTokenStore.readDeviceId();
    final deviceToken = await _deviceTokenStore.readDeviceToken();
    if (deviceId == null || deviceToken == null) {
      throw CalibrationException('No paired device found. Pair a device first.');
    }
    late final http.Response res;
    try {
      res = await _client
          .post(
            Uri.parse('$baseUrl/v1/devices/$deviceId/calibrate'),
            headers: {
              'Content-Type': 'application/json',
              'Authorization': 'Bearer $deviceToken',
            },
          )
          .timeout(const Duration(seconds: 30));
    } on Exception {
      throw CalibrationException('Network error — could not complete calibration.');
    }
    if (res.statusCode == 200) {
      final json = jsonDecode(res.body) as Map<String, dynamic>;
      return CalibrationResult(
        deviceId: json['device_id'] as String,
        nAdlWindows: json['n_adl_windows'] as int,
        fittedAt: DateTime.parse(json['fitted_at'] as String),
      );
    }
    if (res.statusCode == 401) throw CalibrationException('Device token expired — re-pair the device.');
    if (res.statusCode == 404) throw CalibrationException('Device not found — check pairing.');
    throw CalibrationException('Calibration failed (HTTP ${res.statusCode}).');
  }
}
