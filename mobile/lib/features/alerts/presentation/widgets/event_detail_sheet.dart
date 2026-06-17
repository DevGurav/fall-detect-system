import 'package:flutter/material.dart';

import '../../data/models/fall_event.dart';
import 'alert_format.dart';

/// A bottom sheet with the full detail of a single alert. Shared by the live
/// feed and the history timeline; takes primitives so either model can open it.
Future<void> showEventDetailSheet(
  BuildContext context, {
  required String deviceId,
  required FallSeverity severity,
  required double confidence,
  required DateTime occurredAt,
  double? leadTimeMs,
  String? modelVersion,
  DateTime? acknowledgedAt,
}) {
  return showModalBottomSheet<void>(
    context: context,
    showDragHandle: true,
    isScrollControlled: true,
    builder: (context) => _EventDetailSheet(
      deviceId: deviceId,
      severity: severity,
      confidence: confidence,
      occurredAt: occurredAt,
      leadTimeMs: leadTimeMs,
      modelVersion: modelVersion,
      acknowledgedAt: acknowledgedAt,
    ),
  );
}

class _EventDetailSheet extends StatelessWidget {
  const _EventDetailSheet({
    required this.deviceId,
    required this.severity,
    required this.confidence,
    required this.occurredAt,
    this.leadTimeMs,
    this.modelVersion,
    this.acknowledgedAt,
  });

  final String deviceId;
  final FallSeverity severity;
  final double confidence;
  final DateTime occurredAt;
  final double? leadTimeMs;
  final String? modelVersion;
  final DateTime? acknowledgedAt;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final style = severityStyle(severity);
    final manual = isManualSos(deviceId);

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 4, 20, 28),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  width: 52,
                  height: 52,
                  decoration: BoxDecoration(
                    color: style.color.withValues(alpha: 0.16),
                    shape: BoxShape.circle,
                  ),
                  child: Icon(alertIcon(deviceId, severity), color: style.color),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        alertTitle(deviceId),
                        style: theme.textTheme.titleLarge
                            ?.copyWith(fontWeight: FontWeight.w700),
                      ),
                      const SizedBox(height: 4),
                      Text(relativeTime(occurredAt),
                          style: theme.textTheme.bodyMedium?.copyWith(
                              color: theme.colorScheme.onSurfaceVariant)),
                    ],
                  ),
                ),
                SeverityPill(severity: severity),
              ],
            ),
            const SizedBox(height: 20),
            const Divider(height: 1),
            const SizedBox(height: 8),
            _DetailRow(
                icon: Icons.schedule_rounded,
                label: 'When',
                value: fullDateTime(occurredAt)),
            _DetailRow(
                icon: manual ? Icons.touch_app_rounded : Icons.watch_rounded,
                label: 'Source',
                value: sourceLabel(deviceId)),
            if (showsConfidence(deviceId, confidence))
              _DetailRow(
                  icon: Icons.insights_rounded,
                  label: 'Model confidence',
                  value: confidenceLabel(confidence)),
            if (leadTimeMs != null)
              _DetailRow(
                  icon: Icons.bolt_rounded,
                  label: 'Pre-impact lead',
                  value: '${leadTimeMs!.round()} ms'),
            if (modelVersion != null && modelVersion!.isNotEmpty)
              _DetailRow(
                  icon: Icons.memory_rounded,
                  label: 'Model',
                  value: modelVersion!),
            _DetailRow(
              icon: acknowledgedAt != null
                  ? Icons.check_circle_rounded
                  : Icons.radio_button_unchecked_rounded,
              label: 'Status',
              value: acknowledgedAt != null
                  ? 'Acknowledged ${relativeTime(acknowledgedAt!)}'
                  : 'Not acknowledged',
              valueColor: acknowledgedAt != null ? Colors.green : null,
            ),
            if (manual) ...[
              const SizedBox(height: 14),
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: style.color.withValues(alpha: 0.10),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Row(
                  children: [
                    Icon(Icons.info_outline_rounded,
                        size: 18, color: style.color),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        'This alert was raised manually with the SOS button, '
                        'not by the detection model.',
                        style: theme.textTheme.bodySmall,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _DetailRow extends StatelessWidget {
  const _DetailRow({
    required this.icon,
    required this.label,
    required this.value,
    this.valueColor,
  });

  final IconData icon;
  final String label;
  final String value;
  final Color? valueColor;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 20, color: theme.colorScheme.onSurfaceVariant),
          const SizedBox(width: 14),
          Text(label,
              style: theme.textTheme.bodyMedium
                  ?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
          const Spacer(),
          Flexible(
            child: Text(
              value,
              textAlign: TextAlign.right,
              style: theme.textTheme.bodyMedium?.copyWith(
                fontWeight: FontWeight.w600,
                color: valueColor,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
