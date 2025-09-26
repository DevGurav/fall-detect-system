# ESP32 Fall Detection Integration

This document provides complete ESP32 code and setup instructions for the fall detection system.

## � Hardware Requirements

- **ESP32 Development Board** (ESP32-WROOM-32 or similar)
- **MPU6050 Accelerometer + Gyroscope Module**
- **Jumper Wires**
- **Breadboard** (optional)
- **USB Cable** for programming
- **Power Supply** (battery pack for wearable use)

## 📐 Wiring Diagram

```
ESP32          MPU6050
-----          -------
3.3V    -----> VCC
GND     -----> GND
GPIO21  -----> SDA (I2C Data)
GPIO22  -----> SCL (I2C Clock)
```

## � Arduino IDE Setup

### 1. Install ESP32 Board Package
1. Open Arduino IDE
2. Go to **File → Preferences**
3. Add this URL to "Additional Board Manager URLs":
   ```
   https://dl.espressif.com/dl/package_esp32_index.json
   ```
4. Go to **Tools → Board → Boards Manager**
5. Search for "ESP32" and install **ESP32 by Espressif Systems**

### 2. Install Required Libraries
Go to **Sketch → Include Library → Manage Libraries** and install:
- **MPU6050** by Electronic Cats (or Jeff Rowberg)
- **ArduinoJson** by Benoit Blanchon
- **HTTPClient** (built-in with ESP32)
- **WiFi** (built-in with ESP32)

### 3. Board Configuration
- **Board**: "ESP32 Dev Module"
- **Upload Speed**: "921600"
- **CPU Frequency**: "240MHz (WiFi/BT)"
- **Flash Size**: "4MB (32Mb)"
- **Partition Scheme**: "Default 4MB with spiffs"

## 🚀 Complete ESP32 Code

Save this as `fall_detection_esp32.ino`:
const float fallThreshold = 2.5; // G-force threshold for potential fall

// Hardware setup
MPU6050 mpu;
WiFiClient wifiClient;

// Sensor data structure
struct SensorReading {
  float accelX, accelY, accelZ;
  float gyroX, gyroY, gyroZ;
  float totalAccel;
  unsigned long timestamp;
};

void setup() {
  Serial.begin(115200);
  Wire.begin();
  
  // Initialize MPU6050
  Serial.println("Initializing MPU6050...");
  mpu.begin();
  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  mpu.setGyroRange(MPU6050_RANGE_500_DEG);
  mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
  
  // Connect to WiFi
  connectToWiFi();
  
  Serial.println("Fall Detection System Ready!");
  Serial.println("Monitoring for falls...");
}

void loop() {
  // Read sensor data
  SensorReading reading = readSensorData();
  
  // Print current readings for debugging
  printSensorData(reading);
  
  // Check for potential fall
  if (isPotentialFall(reading)) {
    Serial.println("🚨 POTENTIAL FALL DETECTED! Sending to API...");
    sendToAPI(reading);
    delay(5000); // Prevent multiple rapid detections
  }
  
  delay(measurementInterval);
}

void connectToWiFi() {
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  
  while (WiFi.status() != WL_CONNECTED) {
    delay(1000);
    Serial.print(".");
  }
  
  Serial.println();
  Serial.printf("Connected to %s\n", ssid);
  Serial.printf("IP address: %s\n", WiFi.localIP().toString().c_str());
}

SensorReading readSensorData() {
  SensorReading reading;
  
  sensors_event_t a, g, temp;
  mpu.getEvent(&a, &g, &temp);
  
  // Convert to standard units
  reading.accelX = a.acceleration.x; // m/s²
  reading.accelY = a.acceleration.y;
  reading.accelZ = a.acceleration.z;
  reading.gyroX = g.gyro.x; // rad/s
  reading.gyroY = g.gyro.y;
  reading.gyroZ = g.gyro.z;
  
  // Calculate total acceleration magnitude
  reading.totalAccel = sqrt(
    reading.accelX * reading.accelX + 
    reading.accelY * reading.accelY + 
    reading.accelZ * reading.accelZ
  ) / 9.81; // Convert to G-force
  
  reading.timestamp = millis();
  
  return reading;
}

bool isPotentialFall(const SensorReading& reading) {
  // Simple fall detection logic
  // In a real implementation, you might use more sophisticated algorithms
  
  // Check for sudden acceleration change (impact or free fall)
  if (reading.totalAccel > fallThreshold || reading.totalAccel < 0.5) {
    return true;
  }
  
  // Check for high gyroscope values (tumbling)
  float totalGyro = sqrt(
    reading.gyroX * reading.gyroX + 
    reading.gyroY * reading.gyroY + 
    reading.gyroZ * reading.gyroZ
  );
  
  if (totalGyro > 5.0) { // rad/s
    return true;
  }
  
  return false;
}

void sendToAPI(const SensorReading& reading) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi not connected, attempting reconnection...");
    connectToWiFi();
    return;
  }
  
  HTTPClient http;
  http.begin(serverURL);
  http.addHeader("Content-Type", "application/json");
  
  // Create JSON payload
  JsonDocument doc;
  doc["device_id"] = deviceId;
  doc["accelX"] = reading.accelX;
  doc["accelY"] = reading.accelY;
  doc["accelZ"] = reading.accelZ;
  doc["gyroX"] = reading.gyroX;
  doc["gyroY"] = reading.gyroY;
  doc["gyroZ"] = reading.gyroZ;
  doc["timestamp"] = reading.timestamp;
  doc["total_accel"] = reading.totalAccel;
  
  String jsonString;
  serializeJson(doc, jsonString);
  
  Serial.println("Sending data: " + jsonString);
  
  int httpResponseCode = http.POST(jsonString);
  
  if (httpResponseCode > 0) {
    String response = http.getString();
    Serial.printf("HTTP Response: %d\n", httpResponseCode);
    Serial.println("Response: " + response);
    
    // Parse response to check if fall was detected
    JsonDocument responseDoc;
    deserializeJson(responseDoc, response);
    
    bool fallDetected = responseDoc["fall"];
    float confidence = responseDoc["confidence"];
    
    if (fallDetected) {
      Serial.printf("🚨 FALL CONFIRMED by AI! Confidence: %.1f%%\n", confidence * 100);
      // You could add local alerts here (LED, buzzer, etc.)
    } else {
      Serial.printf("✅ Normal activity detected. Confidence: %.1f%%\n", confidence * 100);
    }
    
  } else {
    Serial.printf("Error sending data: %d\n", httpResponseCode);
    Serial.println("Error: " + http.errorToString(httpResponseCode));
  }
  
  http.end();
}

void printSensorData(const SensorReading& reading) {
  Serial.printf("Accel: X=%.2f Y=%.2f Z=%.2f (Total=%.2fG) | ", 
    reading.accelX, reading.accelY, reading.accelZ, reading.totalAccel);
  Serial.printf("Gyro: X=%.2f Y=%.2f Z=%.2f\n", 
    reading.gyroX, reading.gyroY, reading.gyroZ);
}

// Optional: Add watchdog timer for reliability
void IRAM_ATTR resetModule() {
  ets_printf("watchdog timer expired, restarting\n");
  esp_restart();
}
```

## ⚙️ Advanced Configuration

### Power Management
```cpp
#include "esp_sleep.h"

void enterDeepSleep() {
  // Wake up every 30 seconds to check sensors
  esp_sleep_enable_timer_wakeup(30 * 1000000); // microseconds
  esp_deep_sleep_start();
}

void optimizePowerConsumption() {
  // Reduce CPU frequency
  setCpuFrequencyMhz(80); // Default is 240MHz
  
  // Disable WiFi when not needed
  WiFi.disconnect();
  WiFi.mode(WIFI_OFF);
  
  // Use light sleep during measurements
  esp_sleep_enable_timer_wakeup(1000000); // 1 second
  esp_light_sleep_start();
}
```

### Enhanced Fall Detection
```cpp
// Circular buffer for sensor history
#define BUFFER_SIZE 10
SensorReading sensorBuffer[BUFFER_SIZE];
int bufferIndex = 0;

void addToBuffer(const SensorReading& reading) {
  sensorBuffer[bufferIndex] = reading;
  bufferIndex = (bufferIndex + 1) % BUFFER_SIZE;
}

bool detectFallPattern() {
  // Look for fall pattern: high acceleration -> low acceleration -> impact
  // This is a simplified version - real implementation would be more complex
  
  float maxAccel = 0, minAccel = 10;
  for (int i = 0; i < BUFFER_SIZE; i++) {
    float accel = sensorBuffer[i].totalAccel;
    if (accel > maxAccel) maxAccel = accel;
    if (accel < minAccel) minAccel = accel;
  }
  
  // Fall pattern: high peak followed by low valley
  return (maxAccel > 3.0 && minAccel < 0.5);
}
```

### WiFi Reconnection and Error Handling
```cpp
void ensureWiFiConnection() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi disconnected, reconnecting...");
    WiFi.disconnect();
    WiFi.begin(ssid, password);
    
    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 20) {
      delay(500);
      Serial.print(".");
      attempts++;
    }
    
    if (WiFi.status() == WL_CONNECTED) {
      Serial.println("\nWiFi reconnected!");
    } else {
      Serial.println("\nWiFi reconnection failed!");
      // Consider entering deep sleep and retry later
    }
  }
}

void handleHTTPError(int errorCode) {
  switch (errorCode) {
    case HTTP_CODE_OK:
      Serial.println("Request successful");
      break;
    case HTTP_CODE_BAD_REQUEST:
      Serial.println("Bad request - check data format");
      break;
    case HTTP_CODE_UNAUTHORIZED:
      Serial.println("Unauthorized - check API key");
      break;
    case HTTP_CODE_INTERNAL_SERVER_ERROR:
      Serial.println("Server error - try again later");
      break;
    default:
      Serial.printf("HTTP Error: %d\n", errorCode);
  }
}
```

## 🔧 Hardware Optimization

### 3D Printed Enclosure
Design considerations:
- Waterproof/water-resistant
- Comfortable for wrist wearing
- Easy access to charging port
- Proper ventilation
- Secure sensor mounting

### Battery Management
```cpp
#include "driver/adc.h"

float readBatteryVoltage() {
  // Assuming voltage divider on GPIO34
  int raw = analogRead(34);
  float voltage = raw * (3.3 / 4095.0) * 2; // Adjust for voltage divider
  return voltage;
}

void checkBatteryLevel() {
  float voltage = readBatteryVoltage();
  int percentage = map(voltage * 100, 320, 420, 0, 100); // 3.2V to 4.2V range
  
  Serial.printf("Battery: %.2fV (%d%%)\n", voltage, percentage);
  
  if (percentage < 20) {
    Serial.println("⚠️  Low battery warning!");
    // Send low battery alert to backend
  }
  
  if (percentage < 5) {
    Serial.println("🔋 Critical battery level - entering deep sleep");
    enterDeepSleep();
  }
}
```

## 🧪 Testing and Calibration

### Sensor Calibration
```cpp
void calibrateSensor() {
  Serial.println("Calibrating sensor - keep device still for 10 seconds...");
  
  float accelX_offset = 0, accelY_offset = 0, accelZ_offset = 0;
  int samples = 100;
  
  for (int i = 0; i < samples; i++) {
    sensors_event_t a, g, temp;
    mpu.getEvent(&a, &g, &temp);
    
    accelX_offset += a.acceleration.x;
    accelY_offset += a.acceleration.y;
    accelZ_offset += a.acceleration.z;
    
    delay(100);
  }
  
  accelX_offset /= samples;
  accelY_offset /= samples;
  accelZ_offset = (accelZ_offset / samples) - 9.81; // Remove gravity
  
  Serial.printf("Calibration offsets: X=%.2f, Y=%.2f, Z=%.2f\n", 
    accelX_offset, accelY_offset, accelZ_offset);
}
```

### Fall Simulation Testing
```cpp
void testFallDetection() {
  Serial.println("Fall detection test mode - shake device vigorously");
  
  while (true) {
    SensorReading reading = readSensorData();
    
    if (isPotentialFall(reading)) {
      Serial.println("✅ Fall detection triggered!");
      Serial.printf("Total acceleration: %.2fG\n", reading.totalAccel);
      delay(2000);
    }
    
    delay(100);
    
    // Exit test mode after 30 seconds
    if (millis() > 30000) break;
  }
}
```

## 📊 Data Collection and Analysis

### Local Data Logging
```cpp
#include "SPIFFS.h"

void logDataToFile(const SensorReading& reading) {
  if (!SPIFFS.begin()) {
    Serial.println("SPIFFS initialization failed");
    return;
  }
  
  File file = SPIFFS.open("/sensor_data.csv", FILE_APPEND);
  if (file) {
    file.printf("%lu,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f\n",
      reading.timestamp,
      reading.accelX, reading.accelY, reading.accelZ,
      reading.gyroX, reading.gyroY, reading.gyroZ);
    file.close();
  }
}
```

## 🔍 Troubleshooting

### Common Issues

1. **WiFi Connection Problems**:
   - Check SSID and password
   - Ensure 2.4GHz network (ESP32 doesn't support 5GHz)
   - Check signal strength

2. **Sensor Reading Issues**:
   - Verify wiring connections
   - Check I2C address (default: 0x68)
   - Test with I2C scanner

3. **API Communication Errors**:
   - Verify backend URL
   - Check JSON format
   - Test with Postman first

### I2C Scanner Code
```cpp
void scanI2C() {
  Serial.println("Scanning I2C devices...");
  
  for (byte address = 1; address < 127; address++) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() == 0) {
      Serial.printf("I2C device found at address 0x%02X\n", address);
    }
  }
}
```

## 🚀 Production Considerations

1. **Security**: Implement HTTPS certificate validation
2. **Reliability**: Add watchdog timers and error recovery
3. **Updates**: Implement OTA (Over-The-Air) updates
4. **Monitoring**: Send device health data to backend
5. **Privacy**: Hash or encrypt sensitive data before transmission

## 📚 Additional Resources

- [ESP32 Documentation](https://docs.espressif.com/projects/esp32/en/latest/)
- [MPU6050 Datasheet](https://invensense.tdk.com/products/motion-tracking/6-axis/mpu-6050/)
- [Arduino JSON Library](https://arduinojson.org/)
- [Fall Detection Algorithms](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6339236/)

This ESP32 code provides a solid foundation for the wristband device. Remember to test thoroughly and adjust thresholds based on real-world usage patterns.