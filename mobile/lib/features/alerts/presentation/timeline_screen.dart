import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/widgets/account_menu.dart';
import '../application/timeline_providers.dart';
import '../data/event_repository.dart';
import 'widgets/alert_format.dart';
import 'widgets/event_detail_sheet.dart';

/// The caregiver fall timeline — history of confirmed falls, grouped by day,
/// with a summary header and per-event acknowledge.
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
              icon: Icons.cloud_off_rounded,
              title: "Couldn't load history",
              subtitle: '$error',
            ),
          AsyncData(:final value) => value.isEmpty
              ? const _Message(
                  scrollable: true,
                  icon: Icons.inbox_outlined,
                  title: 'No falls yet',
                  subtitle: 'Confirmed falls and SOS alerts will appear here.',
                )
              : _TimelineList(events: value),
          _ => const Center(child: CircularProgressIndicator()),
        },
      ),
    );
  }
}

/// Builds the grouped, summarised list. Items are a flat sequence of headers,
/// day labels, and event tiles so it scrolls as one [ListView].
class _TimelineList extends StatelessWidget {
  const _TimelineList({required this.events});

  final List<TimelineEvent> events;

  @override
  Widget build(BuildContext context) {
    final unacked = events.where((e) => !e.isAcknowledged).length;

    // Group consecutive events by day label, preserving newest-first order.
    final items = <Widget>[
      _SummaryHeader(total: events.length, unacked: unacked),
    ];
    String? currentDay;
    for (final e in events) {
      final label = dayLabel(e.occurredAt);
      if (label != currentDay) {
        currentDay = label;
        items.add(_DayHeader(label: label));
      }
      items.add(_EventTile(event: e));
    }

    return ListView.builder(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(14, 8, 14, 24),
      itemCount: items.length,
      itemBuilder: (_, i) => items[i],
    );
  }
}

class _SummaryHeader extends StatelessWidget {
  const _SummaryHeader({required this.total, required this.unacked});

  final int total;
  final int unacked;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Row(
        children: [
          Expanded(
            child: _StatChip(
              icon: Icons.history_rounded,
              value: '$total',
              label: total == 1 ? 'event' : 'events',
              color: theme.colorScheme.primary,
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: _StatChip(
              icon: unacked == 0
                  ? Icons.check_circle_rounded
                  : Icons.notifications_active_rounded,
              value: '$unacked',
              label: 'to review',
              color: unacked == 0
                  ? Colors.green
                  : const Color(0xFFF97316),
            ),
          ),
        ],
      ),
    );
  }
}

class _StatChip extends StatelessWidget {
  const _StatChip({
    required this.icon,
    required this.value,
    required this.label,
    required this.color,
  });

  final IconData icon;
  final String value;
  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: color.withValues(alpha: 0.25)),
      ),
      child: Row(
        children: [
          Icon(icon, color: color, size: 22),
          const SizedBox(width: 12),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(value,
                  style: theme.textTheme.titleLarge
                      ?.copyWith(fontWeight: FontWeight.w800, height: 1)),
              Text(label,
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
            ],
          ),
        ],
      ),
    );
  }
}

class _DayHeader extends StatelessWidget {
  const _DayHeader({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 18, 4, 8),
      child: Text(
        label.toUpperCase(),
        style: theme.textTheme.labelMedium?.copyWith(
          color: theme.colorScheme.onSurfaceVariant,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.8,
        ),
      ),
    );
  }
}

class _EventTile extends ConsumerWidget {
  const _EventTile({required this.event});

  final TimelineEvent event;

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
    final theme = Theme.of(context);
    final style = severityStyle(event.severity);
    final subtitle = showsConfidence(event.deviceRef, event.confidence)
        ? '${relativeTime(event.occurredAt)} · ${confidenceLabel(event.confidence)}'
        : '${relativeTime(event.occurredAt)} · ${sourceLabel(event.deviceRef)}';

    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Material(
        color: theme.colorScheme.surfaceContainerLow,
        borderRadius: BorderRadius.circular(16),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: () => showEventDetailSheet(
            context,
            deviceId: event.deviceRef,
            severity: event.severity,
            confidence: event.confidence,
            occurredAt: event.occurredAt,
            leadTimeMs: event.leadTimeMs,
            modelVersion: event.modelVersion,
            acknowledgedAt: event.acknowledgedAt,
          ),
          child: IntrinsicHeight(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Container(width: 5, color: style.color),
                Expanded(
                  child: Padding(
                    padding: const EdgeInsets.all(14),
                    child: Row(
                      children: [
                        Container(
                          width: 42,
                          height: 42,
                          decoration: BoxDecoration(
                            color: style.color.withValues(alpha: 0.15),
                            shape: BoxShape.circle,
                          ),
                          child: Icon(
                              alertIcon(event.deviceRef, event.severity),
                              color: style.color,
                              size: 22),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                alertTitle(event.deviceRef),
                                style: theme.textTheme.titleMedium
                                    ?.copyWith(fontWeight: FontWeight.w700),
                              ),
                              const SizedBox(height: 2),
                              Text(
                                subtitle,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: theme.textTheme.bodySmall?.copyWith(
                                    color: theme.colorScheme.onSurfaceVariant),
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
                            style: FilledButton.styleFrom(
                              padding: const EdgeInsets.symmetric(horizontal: 14),
                              visualDensity: VisualDensity.compact,
                            ),
                            child: const Text('Ack'),
                          ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _AckChip extends StatelessWidget {
  const _AckChip();

  @override
  Widget build(BuildContext context) {
    return const Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Icons.check_circle_rounded, size: 18, color: Colors.green),
        SizedBox(width: 4),
        Text('Acked', style: TextStyle(color: Colors.green, fontWeight: FontWeight.w600)),
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
          Container(
            width: 96,
            height: 96,
            decoration: BoxDecoration(
              color: theme.colorScheme.surfaceContainerHighest
                  .withValues(alpha: 0.5),
              shape: BoxShape.circle,
            ),
            child: Icon(icon, size: 46, color: theme.colorScheme.onSurfaceVariant),
          ),
          const SizedBox(height: 20),
          Text(title,
              textAlign: TextAlign.center,
              style: theme.textTheme.titleLarge
                  ?.copyWith(fontWeight: FontWeight.w700)),
          if (subtitle != null) ...[
            const SizedBox(height: 8),
            Text(
              subtitle!,
              textAlign: TextAlign.center,
              style: theme.textTheme.bodyMedium
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
