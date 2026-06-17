import 'package:flutter/material.dart';

import '../../data/models/fall_event.dart';

/// Shared presentation helpers for fall/SOS alerts, used by both the live feed
/// and the history timeline so the two screens speak the same visual language.

/// Visual treatment for a severity level — colour, short label, icon.
class SeverityStyle {
  const SeverityStyle(this.color, this.label, this.icon);
  final Color color;
  final String label;
  final IconData icon;
}

SeverityStyle severityStyle(FallSeverity s) => switch (s) {
      FallSeverity.high =>
        const SeverityStyle(Color(0xFFEF4444), 'High', Icons.priority_high_rounded),
      FallSeverity.medium =>
        const SeverityStyle(Color(0xFFF97316), 'Medium', Icons.warning_amber_rounded),
      FallSeverity.low =>
        const SeverityStyle(Color(0xFFF59E0B), 'Low', Icons.info_outline_rounded),
      FallSeverity.none =>
        const SeverityStyle(Color(0xFF64748B), 'Info', Icons.notifications_none_rounded),
    };

/// Manual SOS events ride the same alert channel as detected falls but carry the
/// sentinel device id "manual-sos" and no model confidence.
bool isManualSos(String deviceId) => deviceId == 'manual-sos';

/// Headline for an alert.
String alertTitle(String deviceId) =>
    isManualSos(deviceId) ? 'Emergency SOS' : 'Fall detected';

/// Icon for an alert (SOS gets its own glyph; falls use the severity icon).
IconData alertIcon(String deviceId, FallSeverity severity) =>
    isManualSos(deviceId) ? Icons.sos_rounded : severityStyle(severity).icon;

/// Friendly source label — the raw SOS sentinel is hidden.
String sourceLabel(String deviceId) =>
    isManualSos(deviceId) ? 'Triggered manually' : 'Device $deviceId';

/// Whether to show a model-confidence figure (manual SOS / zero is meaningless).
bool showsConfidence(String deviceId, double confidence) =>
    !isManualSos(deviceId) && confidence > 0;

/// "92% confidence".
String confidenceLabel(double confidence) =>
    '${(confidence * 100).round()}% confidence';

/// Compact relative time: "just now", "5m ago", "3h ago", "2d ago", else date.
String relativeTime(DateTime t) {
  final d = DateTime.now().difference(t);
  if (d.isNegative || d.inSeconds < 45) return 'just now';
  if (d.inMinutes < 60) return '${d.inMinutes}m ago';
  if (d.inHours < 24) return '${d.inHours}h ago';
  if (d.inDays < 7) return '${d.inDays}d ago';
  return '${_two(t.day)}/${_two(t.month)}';
}

String clockTime(DateTime t) => '${_two(t.hour)}:${_two(t.minute)}';

const _months = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// "18 Jun 2026, 16:03" — for the detail sheet.
String fullDateTime(DateTime t) =>
    '${t.day} ${_months[t.month - 1]} ${t.year}, ${clockTime(t)}';

/// Day-bucket label for grouping the timeline: "Today", "Yesterday", "16 Jun".
String dayLabel(DateTime t) {
  final now = DateTime.now();
  final today = DateTime(now.year, now.month, now.day);
  final day = DateTime(t.year, t.month, t.day);
  final diff = today.difference(day).inDays;
  if (diff == 0) return 'Today';
  if (diff == 1) return 'Yesterday';
  final sameYear = t.year == now.year;
  return sameYear
      ? '${t.day} ${_months[t.month - 1]}'
      : '${t.day} ${_months[t.month - 1]} ${t.year}';
}

String _two(int n) => n.toString().padLeft(2, '0');

/// A softly pulsing dot — the "live" indicator on the feed and status badge.
class PulsingDot extends StatefulWidget {
  const PulsingDot({super.key, required this.color, this.size = 12});

  final Color color;
  final double size;

  @override
  State<PulsingDot> createState() => _PulsingDotState();
}

class _PulsingDotState extends State<PulsingDot>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1100),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _c,
      builder: (_, __) {
        final t = _c.value;
        return Container(
          width: widget.size,
          height: widget.size,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: widget.color,
            boxShadow: [
              BoxShadow(
                color: widget.color.withValues(alpha: 0.55 * (1 - t)),
                blurRadius: 5 + 9 * t,
                spreadRadius: 1.5 * t,
              ),
            ],
          ),
        );
      },
    );
  }
}

/// A small rounded severity chip — shared across alert cards, tiles, and sheet.
class SeverityPill extends StatelessWidget {
  const SeverityPill({super.key, required this.severity});

  final FallSeverity severity;

  @override
  Widget build(BuildContext context) {
    final style = severityStyle(severity);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 5),
      decoration: BoxDecoration(
        color: style.color.withValues(alpha: 0.16),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        style.label,
        style: TextStyle(
            color: style.color, fontWeight: FontWeight.w700, fontSize: 12.5),
      ),
    );
  }
}
