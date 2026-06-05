import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;

import 'package:http/http.dart' as http;

import '../../features/alerts/data/models/fall_event.dart';

/// Lifecycle of the SSE connection, surfaced to the UI for a status badge.
enum SseStatus { connecting, connected, reconnecting, unauthorized, stopped }

typedef TokenProvider = Future<String?> Function();

/// Consumes the gateway's `GET /v1/events/stream` Server-Sent Events feed and
/// exposes a clean broadcast [Stream] of [FallEvent]s plus a [Stream] of
/// [SseStatus].
///
/// All transport policy lives here so the UI never sees a dropped socket:
///   * **reconnect** — an outer loop re-opens the stream forever (until disposed
///     or a 401), so a flaky network self-heals.
///   * **backoff** — exponential with jitter, capped at [_maxBackoff], reset on
///     a clean connect.
///   * **watchdog** — the backend emits a `: keepalive` comment every 15 s; if
///     no bytes (data *or* keepalive) arrive within [_idleTimeout] the socket is
///     treated as half-open and force-cycled. This catches dead TCP that never
///     fires `onDone`.
class FallEventService {
  FallEventService({
    required this.baseUrl,
    required this.eventStreamPath,
    required TokenProvider tokenProvider,
    http.Client? client,
  })  : _tokenProvider = tokenProvider,
        _client = client ?? http.Client();

  final String baseUrl;
  final String eventStreamPath;
  final TokenProvider _tokenProvider;
  final http.Client _client;

  static const _idleTimeout = Duration(seconds: 30); // > server 15 s keepalive
  static const _connectTimeout = Duration(seconds: 10);
  static const _maxBackoff = Duration(seconds: 30);

  final _events = StreamController<FallEvent>.broadcast();
  final _status = StreamController<SseStatus>.broadcast();
  final _rng = math.Random();

  StreamSubscription<String>? _lineSub;
  Timer? _watchdog;
  bool _disposed = false;
  bool _running = false;

  Stream<FallEvent> get events => _events.stream;
  Stream<SseStatus> get status => _status.stream;

  /// Begin the connect/reconnect loop. Idempotent; safe to call again after an
  /// `unauthorized` stop once a fresh token is available.
  void start() {
    if (_running || _disposed) return;
    _running = true;
    unawaited(_loop());
  }

  Future<void> _loop() async {
    var attempt = 0;
    while (!_disposed) {
      _emitStatus(attempt == 0 ? SseStatus.connecting : SseStatus.reconnecting);
      try {
        await _connectAndConsume(); // returns only when the stream ends/errors
        attempt = 0; // a clean end (rare) resets backoff
      } on _UnauthorizedException {
        _emitStatus(SseStatus.unauthorized);
        _running = false;
        return; // bad/absent token — caller must re-auth then start() again
      } catch (_) {
        // network / non-200 / idle-timeout → fall through to backoff
      }
      if (_disposed) break;
      await Future<void>.delayed(_backoff(attempt++));
    }
    _emitStatus(SseStatus.stopped);
  }

  Future<void> _connectAndConsume() async {
    final token = await _tokenProvider();
    if (token == null || token.isEmpty) throw _UnauthorizedException();

    final uri = Uri.parse('$baseUrl$eventStreamPath');
    final request = http.Request('GET', uri)
      ..headers.addAll({
        'Authorization': 'Bearer $token',
        'Accept': 'text/event-stream',
        'Cache-Control': 'no-cache',
      });

    final response = await _client.send(request).timeout(_connectTimeout);
    if (response.statusCode == 401 || response.statusCode == 403) {
      throw _UnauthorizedException();
    }
    if (response.statusCode != 200) {
      throw http.ClientException('SSE HTTP ${response.statusCode}', uri);
    }
    _emitStatus(SseStatus.connected);

    final closed = Completer<void>();
    String? event;
    final data = StringBuffer();

    void arm() {
      _watchdog?.cancel();
      _watchdog = Timer(_idleTimeout, () {
        if (!closed.isCompleted) {
          closed.completeError(TimeoutException('SSE idle > $_idleTimeout'));
        }
      });
    }

    arm();
    _lineSub = response.stream
        .transform(utf8.decoder)
        .transform(const LineSplitter())
        .listen(
      (line) {
        arm(); // any byte — incl. a ':' keepalive — proves the link is live
        if (line.isEmpty) {
          // blank line = frame boundary
          if (event == 'fall' && data.isNotEmpty) _dispatch(data.toString());
          event = null;
          data.clear();
        } else if (line.startsWith('event:')) {
          event = line.substring(6).trim();
        } else if (line.startsWith('data:')) {
          data.write(line.substring(5).trim());
        }
        // ':' comments (keepalive) and the 'retry:' directive are ignored;
        // the watchdog is already re-armed above.
      },
      cancelOnError: true,
      onError: (Object e) {
        if (!closed.isCompleted) closed.completeError(e);
      },
      onDone: () {
        if (!closed.isCompleted) closed.complete();
      },
    );

    try {
      await closed.future;
    } finally {
      _watchdog?.cancel();
      await _lineSub?.cancel();
      _lineSub = null;
    }
  }

  void _dispatch(String rawData) {
    try {
      _events.add(FallEvent.fromRawData(rawData));
    } catch (_) {
      // malformed frame — drop it rather than tear down the stream
    }
  }

  Duration _backoff(int attempt) {
    final shift = attempt > 5 ? 5 : attempt; // cap the exponent
    final expMs = 1000 * (1 << shift); // 1,2,4,8,16,32 s
    final cappedMs = math.min(expMs, _maxBackoff.inMilliseconds);
    return Duration(milliseconds: cappedMs + _rng.nextInt(500)); // + jitter
  }

  void _emitStatus(SseStatus s) {
    if (!_disposed && !_status.isClosed) _status.add(s);
  }

  Future<void> dispose() async {
    _disposed = true;
    _running = false;
    _watchdog?.cancel();
    await _lineSub?.cancel();
    _client.close();
    await _events.close();
    await _status.close();
  }
}

class _UnauthorizedException implements Exception {}
