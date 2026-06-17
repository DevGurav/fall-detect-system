import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/config/env.dart';
import '../../../core/network/fall_event_service.dart';
import '../../auth/presentation/widgets/account_menu.dart';
import '../../emergency/data/emergency_service.dart';
import '../application/alert_providers.dart';
import '../data/models/fall_event.dart';
import 'widgets/alert_format.dart';
import 'widgets/event_detail_sheet.dart';

/// The caregiver's live alert screen — consumes the SSE feed via Riverpod and
/// renders a connection-aware state plus a newest-first list of confirmed falls.
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
          const AccountMenu(),
        ],
      ),
      body: feed.isEmpty
          ? _EmptyState(status: status.value ?? SseStatus.connecting)
          : Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _FeedSummary(feed: feed),
                Expanded(
                  child: ListView.separated(
                    padding: const EdgeInsets.fromLTRB(14, 4, 14, 100),
                    itemCount: feed.length,
                    separatorBuilder: (_, __) => const SizedBox(height: 10),
                    itemBuilder: (_, i) => _AlertCard(event: feed[i], hero: i == 0),
                  ),
                ),
              ],
            ),
      floatingActionButton: const _SosFab(),
    );
  }
}

// ── feed summary strip (shown above the list when alerts exist) ────────────────

class _FeedSummary extends StatelessWidget {
  const _FeedSummary({required this.feed});

  final List<FallEvent> feed;

  FallSeverity get _worst => feed
      .map((e) => e.severity)
      .reduce((a, b) => a.index >= b.index ? a : b);

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final style = severityStyle(_worst);
    final n = feed.length;
    return Container(
      margin: const EdgeInsets.fromLTRB(14, 12, 14, 6),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [style.color.withValues(alpha: 0.18), style.color.withValues(alpha: 0.04)],
        ),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: style.color.withValues(alpha: 0.35)),
      ),
      child: Row(
        children: [
          Icon(Icons.notifications_active_rounded, color: style.color),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  n == 1 ? '1 alert this session' : '$n alerts this session',
                  style: theme.textTheme.titleMedium
                      ?.copyWith(fontWeight: FontWeight.w700),
                ),
                Text(
                  'Most recent ${relativeTime(feed.first.occurredAt)}',
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                ),
              ],
            ),
          ),
          SeverityPill(severity: _worst),
        ],
      ),
    );
  }
}

// ── alert card ────────────────────────────────────────────────────────────────

class _AlertCard extends StatelessWidget {
  const _AlertCard({required this.event, required this.hero});

  final FallEvent event;
  final bool hero;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final style = severityStyle(event.severity);
    final manual = isManualSos(event.deviceId);

    return Material(
      color: theme.colorScheme.surfaceContainerLow,
      borderRadius: BorderRadius.circular(16),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: () => showEventDetailSheet(
          context,
          deviceId: event.deviceId,
          severity: event.severity,
          confidence: event.confidence,
          occurredAt: event.occurredAt,
          leadTimeMs: event.leadTimeMs,
          modelVersion: event.modelVersion,
        ),
        child: Container(
          decoration: BoxDecoration(
            border: Border.all(
              color: hero ? style.color : theme.colorScheme.outlineVariant,
              width: hero ? 1.6 : 1,
            ),
            borderRadius: BorderRadius.circular(16),
          ),
          child: IntrinsicHeight(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Container(width: 5, color: style.color),
                Expanded(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Row(
                      children: [
                        Container(
                          width: 46,
                          height: 46,
                          decoration: BoxDecoration(
                            color: style.color.withValues(alpha: 0.15),
                            shape: BoxShape.circle,
                          ),
                          child: Icon(alertIcon(event.deviceId, event.severity),
                              color: style.color),
                        ),
                        const SizedBox(width: 14),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Row(
                                children: [
                                  Expanded(
                                    child: Text(
                                      alertTitle(event.deviceId),
                                      style: theme.textTheme.titleMedium
                                          ?.copyWith(fontWeight: FontWeight.w700),
                                    ),
                                  ),
                                  Text(
                                    relativeTime(event.occurredAt),
                                    style: theme.textTheme.bodySmall?.copyWith(
                                        color: theme.colorScheme.onSurfaceVariant),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 6),
                              Row(
                                children: [
                                  SeverityPill(severity: event.severity),
                                  const SizedBox(width: 8),
                                  Expanded(
                                    child: Text(
                                      manual
                                          ? sourceLabel(event.deviceId)
                                          : showsConfidence(
                                                  event.deviceId, event.confidence)
                                              ? confidenceLabel(event.confidence)
                                              : sourceLabel(event.deviceId),
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: theme.textTheme.bodySmall?.copyWith(
                                          color:
                                              theme.colorScheme.onSurfaceVariant),
                                    ),
                                  ),
                                ],
                              ),
                            ],
                          ),
                        ),
                        Icon(Icons.chevron_right_rounded,
                            color: theme.colorScheme.onSurfaceVariant),
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

// ── connection-aware empty state ──────────────────────────────────────────────

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.status});

  final SseStatus status;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;

    // (icon, tint, title, subtitle, showPulse)
    final (IconData icon, Color tint, String title, String subtitle, bool live) =
        switch (status) {
      SseStatus.connected => (
          Icons.shield_rounded,
          scheme.primary,
          'All clear',
          "Fall Guardian is watching in real time.\nYou'll be alerted the moment a fall is detected.",
          true,
        ),
      SseStatus.connecting => (
          Icons.wifi_tethering_rounded,
          const Color(0xFFF59E0B),
          'Connecting…',
          'Linking to the alert stream.',
          false,
        ),
      SseStatus.reconnecting => (
          Icons.wifi_tethering_rounded,
          const Color(0xFFF97316),
          'Reconnecting…',
          'The connection dropped — trying again.',
          false,
        ),
      SseStatus.unauthorized => (
          Icons.lock_outline_rounded,
          const Color(0xFFEF4444),
          'Please sign in again',
          'Your session expired, so alerts are paused.',
          false,
        ),
      SseStatus.stopped => (
          Icons.cloud_off_rounded,
          scheme.onSurfaceVariant,
          'Offline',
          'Not receiving alerts right now.',
          false,
        ),
    };

    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 110,
              height: 110,
              decoration: BoxDecoration(
                color: tint.withValues(alpha: 0.12),
                shape: BoxShape.circle,
              ),
              child: Icon(icon, size: 54, color: tint),
            ),
            const SizedBox(height: 24),
            Text(title,
                style: theme.textTheme.headlineSmall
                    ?.copyWith(fontWeight: FontWeight.w700)),
            const SizedBox(height: 8),
            Text(subtitle,
                textAlign: TextAlign.center,
                style: theme.textTheme.bodyMedium
                    ?.copyWith(color: scheme.onSurfaceVariant)),
            if (live) ...[
              const SizedBox(height: 20),
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  PulsingDot(color: scheme.primary),
                  const SizedBox(width: 8),
                  Text('Live',
                      style: theme.textTheme.labelLarge?.copyWith(
                          color: scheme.primary, fontWeight: FontWeight.w700)),
                ],
              ),
            ],
          ],
        ),
      ),
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

// ── status badge ──────────────────────────────────────────────────────────────

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
        s == SseStatus.connected
            ? PulsingDot(color: color, size: 9)
            : Icon(Icons.circle, size: 10, color: color),
        const SizedBox(width: 6),
        Text(label, style: Theme.of(context).textTheme.labelMedium),
      ],
    );
  }
}
