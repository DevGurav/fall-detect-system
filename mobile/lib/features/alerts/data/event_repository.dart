import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../../core/auth/token_store.dart';
import 'models/fall_event.dart' show FallSeverity;

DateTime? _parseDt(Object? v) =>
    v is String ? DateTime.tryParse(v)?.toLocal() : null;

/// A persisted fall verdict on the caregiver timeline. Mirrors backend
/// `EventOut` (schemas.py).
class TimelineEvent {
  const TimelineEvent({
    required this.id,
    required this.deviceRef,
    required this.tsStartUnixMs,
    required this.isFall,
    required this.confidence,
    required this.severity,
    required this.leadTimeMs,
    required this.modelVersion,
    required this.acknowledgedAt,
    required this.createdAt,
  });

  final String id;
  final String deviceRef;
  final int tsStartUnixMs;
  final bool isFall;
  final double confidence;
  final FallSeverity severity;
  final double? leadTimeMs;
  final String modelVersion;
  final DateTime? acknowledgedAt;
  final DateTime createdAt;

  bool get isAcknowledged => acknowledgedAt != null;

  DateTime get occurredAt =>
      DateTime.fromMillisecondsSinceEpoch(tsStartUnixMs, isUtc: true).toLocal();

  TimelineEvent copyWith({DateTime? acknowledgedAt}) => TimelineEvent(
        id: id,
        deviceRef: deviceRef,
        tsStartUnixMs: tsStartUnixMs,
        isFall: isFall,
        confidence: confidence,
        severity: severity,
        leadTimeMs: leadTimeMs,
        modelVersion: modelVersion,
        acknowledgedAt: acknowledgedAt ?? this.acknowledgedAt,
        createdAt: createdAt,
      );

  factory TimelineEvent.fromJson(Map<String, dynamic> j) => TimelineEvent(
        id: j['id'] as String,
        deviceRef: (j['device_ref'] ?? 'unknown') as String,
        tsStartUnixMs: (j['ts_start_unix_ms'] as num?)?.toInt() ?? 0,
        isFall: (j['is_fall'] as bool?) ?? true,
        confidence: (j['confidence'] as num?)?.toDouble() ?? 0.0,
        severity: FallSeverity.parse(j['severity'] as String?),
        leadTimeMs: (j['lead_time_ms'] as num?)?.toDouble(),
        modelVersion: (j['model_version'] ?? '') as String,
        acknowledgedAt: _parseDt(j['acknowledged_at']),
        createdAt: _parseDt(j['created_at']) ?? DateTime.now(),
      );
}

class EventRepositoryException implements Exception {
  EventRepositoryException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// Reads the caregiver timeline (`GET /v1/events`) and acknowledges events
/// (`POST /v1/events/{id}/acknowledge`), authenticating with the stored JWT.
class EventRepository {
  EventRepository({
    required this.baseUrl,
    required TokenStore tokenStore,
    http.Client? client,
  })  : _tokenStore = tokenStore,
        _client = client ?? http.Client();

  final String baseUrl;
  final TokenStore _tokenStore;
  final http.Client _client;

  Future<Map<String, String>> _headers() async {
    final token = await _tokenStore.readAccessToken();
    if (token == null || token.isEmpty) {
      throw EventRepositoryException('Not signed in.');
    }
    return {'Authorization': 'Bearer $token', 'Accept': 'application/json'};
  }

  Future<List<TimelineEvent>> fetchTimeline({int limit = 50, int offset = 0}) async {
    final uri = Uri.parse('$baseUrl/v1/events').replace(
      queryParameters: {'limit': '$limit', 'offset': '$offset'},
    );
    late final http.Response res;
    try {
      res = await _client
          .get(uri, headers: await _headers())
          .timeout(const Duration(seconds: 15));
    } on EventRepositoryException {
      rethrow;
    } on Exception {
      throw EventRepositoryException('Network error loading the timeline.');
    }
    if (res.statusCode != 200) {
      throw EventRepositoryException('Failed to load timeline (HTTP ${res.statusCode}).');
    }
    final page = jsonDecode(res.body) as Map<String, dynamic>;
    final items = (page['items'] as List<dynamic>?) ?? const [];
    return [for (final e in items) TimelineEvent.fromJson(e as Map<String, dynamic>)];
  }

  Future<TimelineEvent> acknowledge(String eventId) async {
    final uri = Uri.parse('$baseUrl/v1/events/$eventId/acknowledge');
    late final http.Response res;
    try {
      res = await _client
          .post(uri, headers: await _headers())
          .timeout(const Duration(seconds: 15));
    } on EventRepositoryException {
      rethrow;
    } on Exception {
      throw EventRepositoryException('Network error.');
    }
    if (res.statusCode == 404) throw EventRepositoryException('Event not found.');
    if (res.statusCode != 200) {
      throw EventRepositoryException('Failed to acknowledge (HTTP ${res.statusCode}).');
    }
    return TimelineEvent.fromJson(jsonDecode(res.body) as Map<String, dynamic>);
  }
}
