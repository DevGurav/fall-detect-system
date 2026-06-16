import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/config/env.dart';
import '../../../core/network/fall_event_service.dart';
import '../../auth/presentation/widgets/logout_action.dart';
import '../../emergency/data/emergency_service.dart';
import '../../pairing/presentation/pairing_screen.dart';
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
            padding: const EdgeInsets.only(right: 4),
            child: _StatusBadge(status: status),
          ),
          if (feed.isNotEmpty)
            IconButton(
              icon: const Icon(Icons.done_all),
              tooltip: 'Clear feed',
              onPressed: () => ref.read(fallFeedProvider.notifier).clear(),
            ),
          IconButton(
            icon: const Icon(Icons.devices),
            tooltip: 'Pair device',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const PairingScreen()),
            ),
          ),
          const LogoutAction(),
        ],
      ),
      body: feed.isEmpty
          ? const _EmptyState()
          : ListView.separated(
              // Extra bottom padding so the SOS FAB never covers the last card.
              padding: const EdgeInsets.only(
                  left: 12, right: 12, top: 12, bottom: 96),
              itemCount: feed.length,
              separatorBuilder: (_, __) => const SizedBox(height: 8),
              itemBuilder: (_, i) => _AlertCard(event: feed[i], hero: i == 0),
            ),
      floatingActionButton: const _SosFab(),
    );
  }
}

// ── SOS floating action button ────────────────────────────────────────────────

class _SosFab extends ConsumerStatefulWidget {
  const _SosFab();

  @override
  ConsumerState<_SosFab> createState() => _SosFabState();
}

class _SosFabState extends ConsumerState<_SosFab> {
  bool _sending = false;

  // Capture messenger before any await so it's never used across an async gap
  // with an unrelated mounted check (lint: don't_use_build_context_synchronously).
  Future<void> _confirm() async {
    final messenger = ScaffoldMessenger.of(context);

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Send Emergency SOS?'),
        content: const Text(
          'This will create an emergency alert and notify all registered '
          'contacts immediately.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: Colors.red,
              foregroundColor: Colors.white,
            ),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Send SOS'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    setState(() => _sending = true);
    try {
      final svc = EmergencyService(
        baseUrl: Env.baseUrl,
        tokenStore: ref.read(tokenStoreProvider),
      );
      await svc.triggerSos();
      if (mounted) {
        messenger.showSnackBar(
          const SnackBar(
            content: Text('Emergency SOS sent.'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } on EmergencyException catch (e) {
      if (mounted) {
        messenger.showSnackBar(SnackBar(content: Text(e.message)));
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return FloatingActionButton.extended(
      onPressed: _sending ? null : _confirm,
      backgroundColor: Colors.red,
      foregroundColor: Colors.white,
      icon: _sending
          ? const SizedBox(
              width: 20,
              height: 20,
              child: CircularProgressIndicator(
                  color: Colors.white, strokeWidth: 2.5),
            )
          : const Icon(Icons.emergency),
      label: const Text('SOS', style: TextStyle(fontWeight: FontWeight.bold)),
    );
  }
}

// ── supporting widgets ────────────────────────────────────────────────────────

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
