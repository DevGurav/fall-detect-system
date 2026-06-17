import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/widgets/account_menu.dart';
import '../application/timeline_providers.dart';
import '../data/event_repository.dart';
import '../data/models/fall_event.dart' show FallSeverity;

String _two(int n) => n.toString().padLeft(2, '0');

/// The caregiver fall timeline — history of confirmed falls with acknowledge.
class TimelineScreen extends ConsumerWidget {
  const TimelineScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(timelineProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('History'),
        actions: const [AccountMenu()],
      ),
      body: RefreshIndicator(
        onRefresh: () => ref.read(timelineProvider.notifier).refresh(),
        child: switch (async) {
          AsyncError(:final error) => _Message(
              scrollable: true,
              icon: Icons.cloud_off,
              title: "Couldn't load the timeline",
              subtitle: '$error',
            ),
          AsyncData(:final value) => value.isEmpty
              ? const _Message(
                  scrollable: true,
                  icon: Icons.inbox_outlined,
                  title: 'No falls yet',
                  subtitle: 'Confirmed falls will appear here.',
                )
              : ListView.separated(
                  physics: const AlwaysScrollableScrollPhysics(),
                  padding: const EdgeInsets.all(12),
                  itemCount: value.length,
                  separatorBuilder: (_, __) => const SizedBox(height: 8),
                  itemBuilder: (_, i) => _EventTile(event: value[i]),
                ),
          _ => const Center(child: CircularProgressIndicator()),
        },
      ),
    );
  }
}

class _EventTile extends ConsumerWidget {
  const _EventTile({required this.event});

  final TimelineEvent event;

  Color get _color => switch (event.severity) {
        FallSeverity.high => Colors.red,
        FallSeverity.medium => Colors.deepOrange,
        FallSeverity.low => Colors.amber,
        FallSeverity.none => Colors.blueGrey,
      };

  Future<void> _ack(BuildContext context, WidgetRef ref) async {
    try {
      await ref.read(timelineProvider.notifier).acknowledge(event.id);
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Could not acknowledge — try again.')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final color = _color;
    final t = event.occurredAt;
    final when = '${_two(t.day)}/${_two(t.month)} ${_two(t.hour)}:${_two(t.minute)}';
    return Card(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Row(
          children: [
            CircleAvatar(
              backgroundColor: color.withValues(alpha: 0.15),
              child: Icon(Icons.warning_amber_rounded, color: color),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Device ${event.deviceRef}',
                    style: Theme.of(context)
                        .textTheme
                        .titleMedium
                        ?.copyWith(fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    '${event.severity.label} • ${(event.confidence * 100).round()}% • $when',
                    style: TextStyle(color: color, fontWeight: FontWeight.w600),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            if (event.isAcknowledged)
              const _AckChip()
            else
              FilledButton.tonal(
                onPressed: () => _ack(context, ref),
                child: const Text('Acknowledge'),
              ),
          ],
        ),
      ),
    );
  }
}

class _AckChip extends StatelessWidget {
  const _AckChip();

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        const Icon(Icons.check_circle, size: 18, color: Colors.green),
        const SizedBox(width: 4),
        Text('Acked', style: TextStyle(color: scheme.onSurfaceVariant)),
      ],
    );
  }
}

class _Message extends StatelessWidget {
  const _Message({
    required this.icon,
    required this.title,
    this.subtitle,
    this.scrollable = false,
  });

  final IconData icon;
  final String title;
  final String? subtitle;
  final bool scrollable;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final content = Padding(
      padding: const EdgeInsets.all(32),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 64, color: theme.colorScheme.onSurfaceVariant),
          const SizedBox(height: 16),
          Text(title, textAlign: TextAlign.center, style: theme.textTheme.titleMedium),
          if (subtitle != null) ...[
            const SizedBox(height: 6),
            Text(
              subtitle!,
              textAlign: TextAlign.center,
              style: theme.textTheme.bodySmall
                  ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
          ],
        ],
      ),
    );
    if (!scrollable) return Center(child: content);
    return LayoutBuilder(
      builder: (context, constraints) => SingleChildScrollView(
        physics: const AlwaysScrollableScrollPhysics(),
        child: ConstrainedBox(
          constraints: BoxConstraints(minHeight: constraints.maxHeight),
          child: Center(child: content),
        ),
      ),
    );
  }
}
