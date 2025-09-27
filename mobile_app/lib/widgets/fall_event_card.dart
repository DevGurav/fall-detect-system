import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../models/fall_event.dart';

class FallEventCard extends StatelessWidget {
  final FallEvent event;
  final VoidCallback? onTap;
  final VoidCallback? onAcknowledge;

  const FallEventCard({
    super.key,
    required this.event,
    this.onTap,
    this.onAcknowledge,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isAlert = event.fallDetected;
    
    return Card(
      margin: const EdgeInsets.only(bottom: 12.0),
      elevation: isAlert ? 4 : 2,
      color: isAlert ? Colors.red[50] : Colors.white,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: isAlert ? BorderSide(color: Colors.red[300]!, width: 1) : BorderSide.none,
      ),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.all(16.0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  // Status Icon
                  Container(
                    padding: const EdgeInsets.all(8.0),
                    decoration: BoxDecoration(
                      color: isAlert ? Colors.red[100] : Colors.green[100],
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Icon(
                      isAlert ? Icons.warning : Icons.check_circle,
                      color: isAlert ? Colors.red[700] : Colors.green[700],
                      size: 20,
                    ),
                  ),
                  const SizedBox(width: 12),
                  
                  // Status Text and Confidence
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          event.statusText,
                          style: theme.textTheme.titleMedium?.copyWith(
                            fontWeight: FontWeight.bold,
                            color: isAlert ? Colors.red[800] : Colors.green[800],
                          ),
                        ),
                        Text(
                          'Confidence: ${event.confidenceText}',
                          style: theme.textTheme.bodySmall?.copyWith(
                            color: Colors.grey[600],
                          ),
                        ),
                      ],
                    ),
                  ),
                  
                  // Timestamp
                  Text(
                    _formatTime(event.timestamp),
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: Colors.grey[600],
                    ),
                  ),
                ],
              ),
              
              const SizedBox(height: 12),
              
              // Device and Full Timestamp
              Row(
                children: [
                  Icon(
                    Icons.devices,
                    size: 16,
                    color: Colors.grey[600],
                  ),
                  const SizedBox(width: 4),
                  Text(
                    'Device: ${event.deviceId}',
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: Colors.grey[600],
                    ),
                  ),
                  const Spacer(),
                  Text(
                    DateFormat('MMM d, h:mm a').format(event.timestamp),
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: Colors.grey[600],
                    ),
                  ),
                ],
              ),
              
              // Sensor Data Preview
              const SizedBox(height: 8),
              Container(
                padding: const EdgeInsets.all(8.0),
                decoration: BoxDecoration(
                  color: Colors.grey[100],
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceAround,
                  children: [
                    _buildSensorPreview('Accel', _getAccelMagnitude(event.sensorData)),
                    _buildSensorPreview('Gyro', _getGyroMagnitude(event.sensorData)),
                  ],
                ),
              ),
              
              // Action Button for Fall Events
              if (isAlert) ...[
                const SizedBox(height: 12),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton.icon(
                    onPressed: onAcknowledge,
                    icon: const Icon(Icons.check, size: 18),
                    label: const Text('Acknowledge'),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.orange[600],
                      foregroundColor: Colors.white,
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(8),
                      ),
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildSensorPreview(String label, double value) {
    return Column(
      children: [
        Text(
          label,
          style: const TextStyle(
            fontSize: 12,
            fontWeight: FontWeight.w500,
            color: Colors.grey,
          ),
        ),
        const SizedBox(height: 2),
        Text(
          value.toStringAsFixed(1),
          style: const TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.bold,
          ),
        ),
      ],
    );
  }

  String _formatTime(DateTime timestamp) {
    final now = DateTime.now();
    final difference = now.difference(timestamp);
    
    if (difference.inMinutes < 1) {
      return 'Just now';
    } else if (difference.inMinutes < 60) {
      return '${difference.inMinutes}m ago';
    } else if (difference.inHours < 24) {
      return '${difference.inHours}h ago';
    } else {
      return '${difference.inDays}d ago';
    }
  }

  double _getAccelMagnitude(SensorData data) {
    return (data.accelX * data.accelX + 
           data.accelY * data.accelY + 
           data.accelZ * data.accelZ).abs();
  }

  double _getGyroMagnitude(SensorData data) {
    return (data.gyroX * data.gyroX + 
           data.gyroY * data.gyroY + 
           data.gyroZ * data.gyroZ).abs();
  }
}