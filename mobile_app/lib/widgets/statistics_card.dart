import 'package:flutter/material.dart';

class StatisticsCard extends StatelessWidget {
  final Map<String, int> statistics;

  const StatisticsCard({
    super.key,
    required this.statistics,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    
    return Card(
      elevation: 3,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
      ),
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  Icons.analytics,
                  color: theme.colorScheme.primary,
                  size: 24,
                ),
                const SizedBox(width: 8),
                Text(
                  'Device Statistics',
                  style: theme.textTheme.titleLarge?.copyWith(
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 20),
            
            Row(
              children: [
                Expanded(
                  child: _buildStatItem(
                    context,
                    'Total Events',
                    statistics['total_events'] ?? 0,
                    Icons.event,
                    Colors.blue,
                  ),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: _buildStatItem(
                    context,
                    'Fall Events',
                    statistics['fall_events'] ?? 0,
                    Icons.warning,
                    Colors.red,
                  ),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: _buildStatItem(
                    context,
                    'Recent (24h)',
                    statistics['recent_events'] ?? 0,
                    Icons.schedule,
                    Colors.orange,
                  ),
                ),
              ],
            ),
            
            const SizedBox(height: 16),
            
            // Status Indicator
            _buildStatusIndicator(context),
          ],
        ),
      ),
    );
  }

  Widget _buildStatItem(
    BuildContext context,
    String label,
    int value,
    IconData icon,
    Color color,
  ) {
    final theme = Theme.of(context);
    
    return Container(
      padding: const EdgeInsets.all(12.0),
      decoration: BoxDecoration(
        color: color.withOpacity(0.1),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: color.withOpacity(0.3),
          width: 1,
        ),
      ),
      child: Column(
        children: [
          Icon(
            icon,
            color: color,
            size: 28,
          ),
          const SizedBox(height: 8),
          Text(
            value.toString(),
            style: theme.textTheme.headlineSmall?.copyWith(
              fontWeight: FontWeight.bold,
              color: color,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            label,
            style: theme.textTheme.bodySmall?.copyWith(
              color: Colors.grey[600],
            ),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }

  Widget _buildStatusIndicator(BuildContext context) {
    final theme = Theme.of(context);
    final fallEvents = statistics['fall_events'] ?? 0;
    final recentEvents = statistics['recent_events'] ?? 0;
    
    Color statusColor;
    IconData statusIcon;
    String statusText;
    
    if (fallEvents == 0) {
      statusColor = Colors.green;
      statusIcon = Icons.check_circle;
      statusText = 'All systems normal';
    } else if (recentEvents > 0) {
      statusColor = Colors.red;
      statusIcon = Icons.warning;
      statusText = 'Recent fall detected - check events';
    } else {
      statusColor = Colors.orange;
      statusIcon = Icons.info;
      statusText = 'Previous falls recorded';
    }
    
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12.0, vertical: 8.0),
      decoration: BoxDecoration(
        color: statusColor.withOpacity(0.1),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(
          color: statusColor.withOpacity(0.3),
          width: 1,
        ),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            statusIcon,
            color: statusColor,
            size: 20,
          ),
          const SizedBox(width: 8),
          Text(
            statusText,
            style: theme.textTheme.bodyMedium?.copyWith(
              color: statusColor,
              fontWeight: FontWeight.w500,
            ),
          ),
        ],
      ),
    );
  }
}