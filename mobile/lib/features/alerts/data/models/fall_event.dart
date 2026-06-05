import 'dart:convert';

/// Severity ladder, mirrored from the backend `Severity` enum (schemas.py).
enum FallSeverity {
  none,
  low,
  medium,
  high;

  static FallSeverity parse(String? v) => switch (v) {
        'high' => FallSeverity.high,
        'medium' => FallSeverity.medium,
        'low' => FallSeverity.low,
        _ => FallSeverity.none,
      };

  String get label => switch (this) {
        FallSeverity.high => 'High',
        FallSeverity.medium => 'Medium',
        FallSeverity.low => 'Low',
        FallSeverity.none => 'None',
      };
}

/// A confirmed-fall alert as delivered on an SSE `fall` frame.
///
/// Mirrors the backend `_alert_payload` (event_store.py). `eventId` is null when
/// the gateway runs DB-less — the alert still fires, there's just no stored row
/// to deep-link into.
class FallEvent {
  const FallEvent({
    required this.eventId,
    required this.deviceId,
    required this.tsStartUnixMs,
    required this.isFall,
    required this.confidence,
    required this.severity,
    required this.leadTimeMs,
    required this.modelVersion,
    required this.receivedAt,
  });

  final String? eventId;
  final String deviceId;
  final int tsStartUnixMs;
  final bool isFall;
  final double confidence;
  final FallSeverity severity;
  final double? leadTimeMs;
  final String modelVersion;

  /// Client receipt time — orders the feed when there's no stored row id.
  final DateTime receivedAt;

  /// Window start as a local wall-clock time.
  DateTime get occurredAt =>
      DateTime.fromMillisecondsSinceEpoch(tsStartUnixMs, isUtc: true).toLocal();

  /// Stable 31-bit id for the OS notification (event_id when persisted).
  int get notificationId =>
      (eventId ?? '$deviceId-$tsStartUnixMs').hashCode & 0x7fffffff;

  factory FallEvent.fromJson(Map<String, dynamic> j) => FallEvent(
        eventId: j['event_id'] as String?,
        deviceId: (j['device_id'] ?? 'unknown') as String,
        tsStartUnixMs: (j['ts_start_unix_ms'] as num?)?.toInt() ?? 0,
        isFall: (j['is_fall'] as bool?) ?? true,
        confidence: (j['confidence'] as num?)?.toDouble() ?? 0.0,
        severity: FallSeverity.parse(j['severity'] as String?),
        leadTimeMs: (j['lead_time_ms'] as num?)?.toDouble(),
        modelVersion: (j['model_version'] ?? '') as String,
        receivedAt: DateTime.now(),
      );

  /// Parse the raw `data:` payload of an SSE frame.
  static FallEvent fromRawData(String data) =>
      FallEvent.fromJson(jsonDecode(data) as Map<String, dynamic>);
}
