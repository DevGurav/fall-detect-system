import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/network/fall_event_service.dart';
import '../application/alert_providers.dart';
import '../data/models/fall_event.dart';

/// The caregiver's live alert screen — consumes the SSE feed via Riverpod and
/// renders connection status + a newest-first list of confirmed falls.
class LiveAlertScreen extends ConsumerWidget {
  const LiveAlertScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final status = ref.watch(sseStatusProvider);
    final feed = ref.watch(fallFeedProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Fall Guardian'),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: _StatusBadge(status: status),
          ),
        ],
      ),
      body: feed.isEmpty
          ? const _EmptyState()
          : ListView.separated(
              padding: const EdgeInsets.all(12),
              itemCount: feed.length,
              separatorBuilder: (_, __) => const SizedBox(height: 8),
              itemBuilder: (_, i) => _AlertCard(event: feed[i], hero: i == 0),
            ),
      floatingActionButton: feed.isEmpty
          ? null
          : FloatingActionButton.extended(
              onPressed: () => ref.read(fallFeedProvider.notifier).clear(),
              icon: const Icon(Icons.done_all),
              label: const Text('Clear'),
            ),
    );
  }
}

class _StatusBadge extends StatelessWidget {
  const _StatusBadge({required this.status});

  final AsyncValue<SseStatus> status;

  @override
  Widget build(BuildContext context) {
    final s = status.value ?? SseStatus.connecting;
    final (color, label) = switch (s) {
      SseStatus.connected => (Colors.green, 'Live'),
      SseStatus.connecting => (Colors.amber, 'Connecting'),
      SseStatus.reconnecting => (Colors.orange, 'Reconnecting'),
      SseStatus.unauthorized => (Colors.red, 'Sign in'),
      SseStatus.stopped => (Colors.grey, 'Offline'),
    };
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Icons.circle, size: 10, color: color),
        const SizedBox(width: 6),
        Text(label, style: Theme.of(context).textTheme.labelMedium),
      ],
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.shield_outlined,
              size: 72, color: Theme.of(context).colorScheme.primary),
          const SizedBox(height: 16),
          Text('All clear', style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 4),
          const Text('Watching for falls in real time.'),
        ],
      ),
    );
  }
}

class _AlertCard extends StatelessWidget {
  const _AlertCard({required this.event, required this.hero});

  final FallEvent event;
  final bool hero;

  Color get _severityColor => switch (event.severity) {
        FallSeverity.high => Colors.red,
        FallSeverity.medium => Colors.deepOrange,
        FallSeverity.low => Colors.amber,
        FallSeverity.none => Colors.blueGrey,
      };

  @override
  Widget build(BuildContext context) {
    final color = _severityColor;
    final t = event.occurredAt;
    final time = '${t.hour.toString().padLeft(2, '0')}:'
        '${t.minute.toString().padLeft(2, '0')}';
    return Card(
      elevation: hero ? 4 : 1,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(color: hero ? color : Colors.transparent, width: 2),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: [
            CircleAvatar(
              radius: 24,
              backgroundColor: color.withValues(alpha: 0.15),
              child: Icon(Icons.warning_amber_rounded, color: color),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Fall detected',
                    style: Theme.of(context)
                        .textTheme
                        .titleMedium
                        ?.copyWith(fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 2),
                  Text('Device ${event.deviceId} • $time'),
                  const SizedBox(height: 2),
                  Text(
                    '${event.severity.label} severity • '
                    '${(event.confidence * 100).round()}% confidence',
                    style: TextStyle(color: color, fontWeight: FontWeight.w600),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
