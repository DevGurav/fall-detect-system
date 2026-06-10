import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/config/env.dart';
import '../application/pairing_providers.dart';
import '../data/calibration_service.dart';

/// 15-minute fit-at-first ADL onboarding screen.
///
/// Shown after a device is successfully paired. The ESP32 streams ADL windows
/// to `/v1/devices/{id}/calibration-windows` automatically. This screen runs
/// the countdown and calls `/v1/devices/{id}/calibrate` when the session ends
/// to fit per-user z-score normalizers.
class CalibrationScreen extends ConsumerStatefulWidget {
  const CalibrationScreen({super.key});

  @override
  ConsumerState<CalibrationScreen> createState() => _CalibrationScreenState();
}

class _CalibrationScreenState extends ConsumerState<CalibrationScreen> {
  static const _totalDuration = Duration(minutes: 15);
  static const _minDuration = Duration(minutes: 5);

  Timer? _timer;
  Duration _elapsed = Duration.zero;
  bool _fitting = false;
  bool _done = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() => _elapsed += const Duration(seconds: 1));
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  bool get _canFinishEarly => _elapsed >= _minDuration;
  bool get _sessionComplete => _elapsed >= _totalDuration;

  Duration get _remaining => _totalDuration > _elapsed
      ? _totalDuration - _elapsed
      : Duration.zero;

  String _fmt(Duration d) {
    final mm = d.inMinutes.remainder(60).toString().padLeft(2, '0');
    final ss = d.inSeconds.remainder(60).toString().padLeft(2, '0');
    return '$mm:$ss';
  }

  Future<void> _finalize() async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() {
      _fitting = true;
      _error = null;
    });
    try {
      final svc = CalibrationService(
        baseUrl: Env.baseUrl,
        deviceTokenStore: ref.read(deviceTokenStoreProvider),
      );
      final result = await svc.fitCalibration();
      if (!mounted) return;
      setState(() => _done = true);
      messenger.showSnackBar(SnackBar(
        content: Text(
            'Calibration complete — ${result.nAdlWindows} ADL windows fitted.'),
        backgroundColor: Colors.green,
      ));
    } on CalibrationException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _fitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final progress =
        (_elapsed.inSeconds / _totalDuration.inSeconds).clamp(0.0, 1.0);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Device Calibration'),
        automaticallyImplyLeading: !_fitting,
      ),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: _done
            ? _DoneState(onFinish: () => Navigator.of(context).pop())
            : _ActiveState(
                elapsed: _elapsed,
                remaining: _remaining,
                progress: progress,
                canFinishEarly: _canFinishEarly,
                sessionComplete: _sessionComplete,
                fitting: _fitting,
                error: _error,
                fmtFn: _fmt,
                scheme: scheme,
                onFinalize: _finalize,
              ),
      ),
    );
  }
}

// ── sub-widgets ───────────────────────────────────────────────────────────────

class _ActiveState extends StatelessWidget {
  const _ActiveState({
    required this.elapsed,
    required this.remaining,
    required this.progress,
    required this.canFinishEarly,
    required this.sessionComplete,
    required this.fitting,
    required this.error,
    required this.fmtFn,
    required this.scheme,
    required this.onFinalize,
  });

  final Duration elapsed;
  final Duration remaining;
  final double progress;
  final bool canFinishEarly;
  final bool sessionComplete;
  final bool fitting;
  final String? error;
  final String Function(Duration) fmtFn;
  final ColorScheme scheme;
  final VoidCallback onFinalize;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const Text(
          'Wear your Fall Guardian device and go about your '
          'normal daily activities for 15 minutes.',
          style: TextStyle(fontSize: 16),
        ),
        const SizedBox(height: 8),
        const Text(
          'This teaches the system your personal movement patterns — '
          'reducing false alarms and making fall detection more accurate for you.',
          style: TextStyle(color: Colors.black54),
        ),
        const SizedBox(height: 32),
        Center(
          child: Stack(
            alignment: Alignment.center,
            children: [
              SizedBox(
                width: 160,
                height: 160,
                child: CircularProgressIndicator(
                  value: progress,
                  strokeWidth: 8,
                  backgroundColor: scheme.surfaceContainerHighest,
                  color: sessionComplete ? Colors.green : scheme.primary,
                ),
              ),
              Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    sessionComplete ? 'Done!' : fmtFn(remaining),
                    style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                          fontWeight: FontWeight.bold,
                          color: sessionComplete ? Colors.green : scheme.primary,
                        ),
                  ),
                  if (!sessionComplete)
                    Text('remaining',
                        style: Theme.of(context).textTheme.bodySmall),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 32),
        if (error != null) ...[
          Row(
            children: [
              Icon(Icons.error_outline, color: scheme.error),
              const SizedBox(width: 8),
              Expanded(child: Text(error!, style: TextStyle(color: scheme.error))),
            ],
          ),
          const SizedBox(height: 16),
        ],
        FilledButton.icon(
          onPressed: (canFinishEarly || sessionComplete) && !fitting
              ? onFinalize
              : null,
          icon: fitting
              ? const SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(
                      color: Colors.white, strokeWidth: 2.5),
                )
              : const Icon(Icons.check_circle),
          label: Text(
            sessionComplete
                ? 'Apply calibration'
                : canFinishEarly
                    ? 'Finish early & apply calibration'
                    : 'Available after ${fmtFn(Duration(minutes: 5) - elapsed)}',
          ),
        ),
        if (!canFinishEarly) ...[
          const SizedBox(height: 12),
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Skip for now'),
          ),
        ],
      ],
    );
  }
}

class _DoneState extends StatelessWidget {
  const _DoneState({required this.onFinish});
  final VoidCallback onFinish;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.check_circle,
              size: 80, color: Theme.of(context).colorScheme.primary),
          const SizedBox(height: 16),
          Text('Calibration complete!',
              style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 8),
          const Text(
            'Your Fall Guardian is now personalised to your movement patterns.',
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 32),
          FilledButton(
            onPressed: onFinish,
            child: const Text('Continue'),
          ),
        ],
      ),
    );
  }
}
