import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/features/alerts/data/models/fall_event.dart';

void main() {
  test('parses a persisted backend fall frame', () {
    const raw = '{"type":"fall","event_id":"abc-123","device_id":"wrist-7",'
        '"ts_start_unix_ms":1733400000000,"is_fall":true,"confidence":0.91,'
        '"severity":"high","lead_time_ms":250.0,"model_version":"cloud-v3"}';
    final e = FallEvent.fromRawData(raw);
    expect(e.eventId, 'abc-123');
    expect(e.deviceId, 'wrist-7');
    expect(e.isFall, isTrue);
    expect(e.severity, FallSeverity.high);
    expect(e.confidence, closeTo(0.91, 1e-9));
    expect(e.leadTimeMs, 250.0);
    expect(e.notificationId, isPositive);
  });

  test('tolerates a DB-less frame (null event_id and lead_time_ms)', () {
    const raw = '{"type":"fall","event_id":null,"device_id":"wrist-7",'
        '"ts_start_unix_ms":1733400000000,"is_fall":true,"confidence":0.8,'
        '"severity":"medium","lead_time_ms":null,"model_version":"cloud-v3"}';
    final e = FallEvent.fromRawData(raw);
    expect(e.eventId, isNull);
    expect(e.leadTimeMs, isNull);
    expect(e.severity, FallSeverity.medium);
    expect(e.notificationId, isPositive);
  });

  test('unknown severity falls back to none', () {
    expect(FallSeverity.parse('weird'), FallSeverity.none);
    expect(FallSeverity.parse(null), FallSeverity.none);
  });
}
