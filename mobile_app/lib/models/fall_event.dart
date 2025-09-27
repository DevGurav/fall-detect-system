class FallEvent {
  final String id;
  final String deviceId;
  final DateTime timestamp;
  final bool fallDetected;
  final double confidence;
  final SensorData sensorData;
  final String processedAt;

  const FallEvent({
    required this.id,
    required this.deviceId,
    required this.timestamp,
    required this.fallDetected,
    required this.confidence,
    required this.sensorData,
    required this.processedAt,
  });

  factory FallEvent.fromMap(Map<String, dynamic> map, String documentId) {
    return FallEvent(
      id: documentId,
      deviceId: map['device_id'] ?? '',
      timestamp: map['timestamp']?.toDate() ?? DateTime.now(),
      fallDetected: map['fall_detected'] ?? false,
      confidence: (map['confidence'] ?? 0.0).toDouble(),
      sensorData: SensorData.fromMap(map['sensor_data'] ?? {}),
      processedAt: map['processed_at'] ?? '',
    );
  }

  Map<String, dynamic> toMap() {
    return {
      'device_id': deviceId,
      'timestamp': timestamp,
      'fall_detected': fallDetected,
      'confidence': confidence,
      'sensor_data': sensorData.toMap(),
      'processed_at': processedAt,
    };
  }

  String get statusText => fallDetected ? 'Fall Detected' : 'Normal Activity';
  String get confidenceText => '${(confidence * 100).toStringAsFixed(1)}%';
}

class SensorData {
  final double accelX;
  final double accelY;
  final double accelZ;
  final double gyroX;
  final double gyroY;
  final double gyroZ;

  const SensorData({
    required this.accelX,
    required this.accelY,
    required this.accelZ,
    required this.gyroX,
    required this.gyroY,
    required this.gyroZ,
  });

  factory SensorData.fromMap(Map<String, dynamic> map) {
    return SensorData(
      accelX: (map['accelX'] ?? 0.0).toDouble(),
      accelY: (map['accelY'] ?? 0.0).toDouble(),
      accelZ: (map['accelZ'] ?? 0.0).toDouble(),
      gyroX: (map['gyroX'] ?? 0.0).toDouble(),
      gyroY: (map['gyroY'] ?? 0.0).toDouble(),
      gyroZ: (map['gyroZ'] ?? 0.0).toDouble(),
    );
  }

  Map<String, dynamic> toMap() {
    return {
      'accelX': accelX,
      'accelY': accelY,
      'accelZ': accelZ,
      'gyroX': gyroX,
      'gyroY': gyroY,
      'gyroZ': gyroZ,
    };
  }
}